"""
对**用户自己的模型**做 h-收敛检查（"我的网格够不够细"）。

与 `convergence.py` 的分工
-------------------------
- `convergence.py`：制造解基准。**精确解已知**，所以量的是真实误差，回答
  "这套离散格式会不会收敛、阶数对不对"。
- 本模块：用户的模型。**没有精确解**，只能做**自收敛**（比较逐级加密得到的
  同一个物理量），回答"这个量在这里稳不稳定"。

自收敛能证明什么、不能证明什么（必须写进接口响应里）
---------------------------------------------------
能证明：该量随加密单调趋稳、差值在缩小、可以估一个观测阶与外推极限。
**不能**证明：极限就是真解。建模错误（材料、约束、载荷方向弄错）会收敛到
一个**错误的稳定值**——那类错误不会随加密消失。所以结论里必须把"离散误差
在减小"和"模型是对的"分开说，否则用户会以为"收敛了 = 结果可信"。

应力奇异：加密不会改善，这不是网格质量问题
-----------------------------------------
尖角、点载荷、以及"固定约束加在单个节点"这类地方，线弹性解的真值本身是
**无穷大**。此时最大应力会随加密一直上升（差值不缩小），`assess()` 会判为
"尚未进入收敛区"。这是**正确**的判定，但理由不是网格差——是问题本身没有
有限的最大应力。结论里必须点出这种可能，否则用户会无止境地加密下去。

每个考察量单独判定
-----------------
位移收敛不代表应力收敛（后者对网格更敏感）。所以这里对每个量分别判定，
整体结论只在**所有**考察量都进入收敛区时才说"已收敛"，并列出哪个没有。

不留痕迹
-------
网格一律划分在几何的**临时副本**上，用完删除：直接对用户的几何划分会覆盖
`<几何名>.msh`——那正是他当前在用的网格。前两轮已经因为"为了验证而调用的
写接口改了别的东西"吃过亏（`POST /api/generate-cube` 重写被 git 跟踪的 STEP）。
"""

from __future__ import annotations

import os
import shutil
import uuid
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from auth import require_user
from config import resolve_upload_path
from convergence import assess, successive_differences
from jobs import run_in_worker
from logging_config import get_logger

logger = get_logger(__name__)

#: 整个 router 都要求登录（与其余计算类端点一致，见 docs/04）
router = APIRouter(dependencies=[Depends(require_user)])

#: 单级网格的单元数上限。收敛检查至少要跑 3 级，而单元数按 h³ 增长，
#: 不设上限时"最细一级"很容易把服务打死（或把内存吃光）。
MAX_STUDY_ELEMENTS = 200_000

#: 允许的级数范围。最少 3 级才能估阶；上限 4 级——再多一级单元数翻 8 倍，
#: 而第 4 级之后观测阶基本不变（见 convergence.py 的实测）。
MIN_STUDY_LEVELS = 3
MAX_STUDY_LEVELS = 4

#: 默认的基础网格尺寸与加密比
DEFAULT_BASE_MESH_SIZE = 1.5
DEFAULT_REFINEMENT_RATIO = 2.0

#: 判定"够细了"用的相对变化阈值（默认 5%）。
#:
#: 这是**工程判据**、不是数学判据：它回答"再加密结果还会不会明显变"，
#: 而不是"离真值还有多远"。
#:
#: 5% 这个数不是随手定的：收敛序列的最后一级变化大致就等于"剩余误差"本身，
#: 所以想让它降到 2% 以下，等于要求剩余误差只有量值的 2%——3~4 级网格
#: 基本达不到。实测 4 级时最大应力最后一级仍变化 8%。**大多数 3 级检查会
#: 落在 `marginal`，那是老实话**，不是功能坏了。要更严就显式调小这个值。
DEFAULT_TOLERANCE = 0.05

#: 分析类型 -> (请求模型类名, 求解函数名, 考察量)
#: 用"名字"而不是直接 import：`solver`/`modal` 在导入期会拉起整套装配依赖，
#: 而本模块在 `main.py` 里是被早期导入的。延迟到调用时再取，也顺带避免了
#: 潜在的循环导入。
ANALYSES: Dict[str, Dict[str, Any]] = {
    "structural": {
        "request": ("solver", "SolverRequest"),
        "solve": ("solver", "solve_impl"),
        "primary": "max_stress",
        "quantities": ("max_stress", "max_displacement"),
        "labels": {
            "max_stress": "最大 von Mises 应力",
            "max_displacement": "最大位移",
        },
    },
    "thermal": {
        "request": ("thermal", "ThermalRequest"),
        "solve": ("thermal", "solve_thermal_impl"),
        "primary": "max_heat_flux",
        "quantities": ("max_heat_flux", "max_temperature"),
        "labels": {
            "max_heat_flux": "最大热流密度",
            "max_temperature": "最高温度",
        },
    },
    "modal": {
        "request": ("modal", "ModalRequest"),
        "solve": ("modal", "solve_modal_impl"),
        "primary": "first_elastic_frequency",
        "quantities": ("first_elastic_frequency",),
        "labels": {"first_elastic_frequency": "第一阶弹性频率"},
    },
}


def _resolve(target: Tuple[str, str]) -> Any:
    """按 ``(模块名, 属性名)`` 延迟取出对象。"""
    module_name, attribute = target
    import importlib

    return getattr(importlib.import_module(module_name), attribute)


def extract_quantities(analysis_type: str, result: Any) -> Dict[str, float]:
    """
    从求解结果里取出**随网格变化的标量**。

    为什么不用整个结果：收敛检查要比较的是"同一个量的逐级数值"，必须是一个
    数。矢量场（位移、应力）逐级比较没有意义——节点数都变了。
    """
    if analysis_type == "structural":
        return {
            "max_stress": float(result.max_stress),
            "max_displacement": float(result.max_displacement),
        }
    if analysis_type == "thermal":
        return {
            "max_heat_flux": float(result.max_heat_flux),
            "max_temperature": float(result.max_temperature),
        }
    # 模态：自由-自由会有若干阶刚体模态（频率≈0，且与网格无关，拿它做收敛
    # 检查等于拿一个常数去比），所以取**第一阶弹性频率**。
    frequencies = [float(value) for value in (result.frequencies or [])]
    if not frequencies:
        raise HTTPException(status_code=500, detail="模态结果里没有频率")
    index = min(int(getattr(result, "rigid_body_modes", 0) or 0), len(frequencies) - 1)
    return {"first_elastic_frequency": frequencies[index]}


class StudyRequest(BaseModel):
    #: 求解器请求体原样放在这里（与 `/api/solve`、`/api/thermal/solve`、
    #: `/api/modal/solve` 的 body 完全同形）。逐级都**用同一份配置**，
    #: 保证各级之间只有网格不同——这正是收敛检查成立的前提。
    setup: Dict[str, Any]
    #: 'structural' | 'thermal' | 'modal'
    analysis_type: str = "structural"
    #: 显式给出各级网格尺寸。必须严格递减且至少 3 级；不填则按
    #: `base_mesh_size` 逐级减半生成 `levels` 级。
    mesh_sizes: Optional[List[float]] = None
    base_mesh_size: float = DEFAULT_BASE_MESH_SIZE
    #: 默认 4 级而不是 3 级：实测 3 级常常还停在前渐近区（test_part.step 用
    #: 3/1.5/0.75 时判为"未进入收敛区"，补到 4 级就正常了）。代价是最细一级
    #: 的单元数约为首级的几十倍——所以这个接口本来就是给异步任务用的。
    levels: int = 4
    #: 相对变化阈值：最后一级变化小于它就认为"够细了"
    tolerance: float = DEFAULT_TOLERANCE

    model_config = {"extra": "forbid"}


class StudyLevel(BaseModel):
    label: str
    mesh_size: float
    elements: int
    nodes: int
    quantities: Dict[str, float]


class StudyResponse(BaseModel):
    #: 'converged'（都进入收敛区且变化小于阈值）
    #: | 'marginal'（都在趋稳，但主考察量最后一级变化仍超过阈值）
    #: | 'not-converged' | 'insufficient'（级数不足，不做判断）
    status: str
    analysis_type: str
    primary: str
    quantities: List[str]
    labels: Dict[str, str]
    levels: List[StudyLevel]
    #: 考察量 -> `convergence.assess()` 的判定结果
    assessments: Dict[str, Dict[str, Any]]
    tolerance: float
    verdict: str
    notes: List[str]
    warnings: List[str]


def derive_mesh_sizes(request: StudyRequest) -> List[float]:
    """
    决定各级网格尺寸。

    显式给定时按给定的走（但仍然要校验），否则从 `base_mesh_size` 逐级减半。
    """
    if request.mesh_sizes is not None:
        sizes = [float(size) for size in request.mesh_sizes]
        if len(sizes) < MIN_STUDY_LEVELS:
            raise HTTPException(
                status_code=400,
                detail=f"mesh_sizes 至少需要 {MIN_STUDY_LEVELS} 级才能估计收敛阶",
            )
        if len(sizes) > MAX_STUDY_LEVELS:
            raise HTTPException(
                status_code=400,
                detail=f"mesh_sizes 最多 {MAX_STUDY_LEVELS} 级（再多一级单元数翻 8 倍）",
            )
        if any(size <= 0 for size in sizes):
            raise HTTPException(status_code=400, detail="网格尺寸必须为正数")
        for earlier, later in zip(sizes, sizes[1:]):
            if later >= earlier:
                raise HTTPException(
                    status_code=400,
                    detail=f"网格尺寸必须严格递减：{earlier} -> {later}",
                )
        return sizes

    if not MIN_STUDY_LEVELS <= request.levels <= MAX_STUDY_LEVELS:
        raise HTTPException(
            status_code=400,
            detail=f"levels 必须在 {MIN_STUDY_LEVELS}~{MAX_STUDY_LEVELS} 之间",
        )
    if request.base_mesh_size <= 0:
        raise HTTPException(status_code=400, detail="base_mesh_size 必须为正数")
    return [
        request.base_mesh_size / (DEFAULT_REFINEMENT_RATIO ** index)
        for index in range(request.levels)
    ]


async def study_impl(request: StudyRequest) -> StudyResponse:
    """
    逐级加密、逐级求解、比较同一个物理量。

    在单线程 gmsh 工作器里执行（网格划分与求解都碰 gmsh，不能并发）。
    """
    from geometry import generate_mesh_impl

    analysis = ANALYSES.get(request.analysis_type)
    if analysis is None:
        raise HTTPException(
            status_code=400,
            detail=f"analysis_type 只能是 {'/'.join(ANALYSES)}",
        )

    sizes = derive_mesh_sizes(request)

    request_class = _resolve(analysis["request"])
    solve = _resolve(analysis["solve"])

    # 先在**划分网格之前**把配置校验一遍：配置错却先花几分钟划网格，
    # 用户等到的是一句本可以立刻给出的 400。
    try:
        request_class(**request.setup)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"求解配置不合法：{exc}")

    geometry_filename = request.setup.get("geometry_filename")
    if not isinstance(geometry_filename, str) or not geometry_filename:
        raise HTTPException(status_code=400, detail="setup 里缺少 geometry_filename")

    try:
        source = resolve_upload_path(geometry_filename)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"文件名不合法：{exc}")
    if not source.exists():
        raise HTTPException(status_code=404, detail="Geometry file not found")

    # 临时副本：直接对用户几何划网格会覆盖他正在用的 `<几何名>.msh`
    suffix = Path(geometry_filename).suffix
    temporary_name = f"conv_{uuid.uuid4().hex[:8]}{suffix}"
    try:
        temporary_path = resolve_upload_path(temporary_name)
    except ValueError as exc:  # pragma: no cover - 自己生成的名字不该不合法
        raise HTTPException(status_code=500, detail=f"临时文件名不合法：{exc}")
    mesh_path = str(temporary_path) + ".msh"

    notes: List[str] = [
        "本检查是**自收敛**：你的模型没有解析解，所以能证明的是"
        "「这个量随加密趋于稳定」，**不能**证明模型与边界条件正确"
        "——建模错误会收敛到一个错误的稳定值，不会随加密消失。",
        "每个考察量单独判定：位移收敛不代表应力收敛（后者对网格更敏感）。",
        "「观测收敛阶」在这个场景下**没有理论参照**——不同物理量的理论阶不同"
        "（P1 单元的 L2 误差是二阶，而「节点最大值」这类泛函往往只有一阶甚至"
        "更低）。它只用来判断「是否已进入渐近区」，不要当成精度保证。",
        "网格划分在几何的**临时副本**上，不会覆盖你当前的网格。",
        "若某个量（尤其最大应力）的差值不缩小，可能是**应力奇异**"
        "（尖角、点载荷、单点约束）——那里真值本身无穷大，加密不会改善，"
        "这不是网格质量问题。",
    ]
    warnings: List[str] = []
    levels: List[StudyLevel] = []

    shutil.copyfile(source, temporary_path)
    try:
        for index, size in enumerate(sizes):
            info = await generate_mesh_impl(filename=temporary_name, mesh_size=size)
            element_count = len(info.elements)

            if element_count > MAX_STUDY_ELEMENTS:
                notes.append(
                    f"mesh_size={size:g} 会产生 {element_count} 个单元，"
                    f"超过上限 {MAX_STUDY_ELEMENTS}，**已停止加密**。"
                    f"可放粗 mesh_sizes 或用更少的级数。"
                )
                break

            # 各级用**当级**的 B-Rep 面元数据：几何没变，实体 id 也不变，
            # 但用当级的更保险（面元数据是随网格一起算出来的）。
            body = dict(request.setup)
            body["geometry_filename"] = temporary_name
            body["faces"] = info.faces
            result = await solve(request_class(**body))

            quantities = extract_quantities(request.analysis_type, result)
            for message in getattr(result, "warnings", None) or []:
                if message not in warnings:
                    warnings.append(message)

            levels.append(
                StudyLevel(
                    label=f"mesh_size={size:g}",
                    mesh_size=float(size),
                    elements=element_count,
                    nodes=len(info.nodes),
                    quantities=quantities,
                )
            )

            # 预测下一级：单元数大致按 h⁻³ 增长，超上限就不必再划了
            if index + 1 < len(sizes):
                next_size = sizes[index + 1]
                predicted = element_count * (size / next_size) ** 3
                if predicted > MAX_STUDY_ELEMENTS:
                    notes.append(
                        f"下一级 mesh_size={next_size:g} 预计约 {predicted:.0f} 个单元，"
                        f"超过上限 {MAX_STUDY_ELEMENTS}，**已提前停止加密**。"
                    )
                    break
    finally:
        for path in (str(temporary_path), mesh_path):
            if os.path.exists(path):
                try:
                    os.remove(path)
                except OSError:  # pragma: no cover - 删除失败不该让整个请求失败
                    logger.warning("收敛检查临时文件删除失败：%s", path)

    return _build_response(request, analysis, levels, notes, warnings)


def _build_response(
    request: StudyRequest,
    analysis: Dict[str, Any],
    levels: List[StudyLevel],
    notes: List[str],
    warnings: List[str],
) -> StudyResponse:
    """把逐级数值交给 `convergence.assess()` 判定，并汇总成一句结论。"""
    labels = dict(analysis["labels"])
    quantity_names = [name for name in analysis["quantities"]]

    if len(levels) < MIN_STUDY_LEVELS:
        # 级数不足时**不做任何收敛判断**：两级结果接近可能是收敛，也可能
        # 是两处都错得一样。宁可说"无法判断"。
        notes.append(
            f"只取得 {len(levels)} 级网格，少于判断收敛阶所需的 "
            f"{MIN_STUDY_LEVELS} 级，**无法给出收敛性判定**。"
        )
        return StudyResponse(
            status="insufficient",
            analysis_type=request.analysis_type,
            primary=analysis["primary"],
            quantities=quantity_names,
            labels=labels,
            levels=levels,
            assessments={},
            tolerance=request.tolerance,
            verdict=(
                f"网格级数不足（{len(levels)} 级），无法判断是否收敛。"
                "请放粗起始网格尺寸或减少每级的加密倍数。"
            ),
            notes=notes,
            warnings=warnings,
        )

    assessments: Dict[str, Dict[str, Any]] = {}
    # 用**实测**网格尺寸估阶，而不是名义的 mesh_size：gmsh 的 mesh_size 只是
    # 目标值，实测 test_part.step 用 3/1.5/0.75 划出来的单元数是 426/1366/9462，
    # 折算成平均单元尺寸后加密比是 1.47 和 1.91 —— 套 log(r)/log(2) 会算出错的阶数。
    # 平均单元尺寸取 N^(-1/3)（绝对尺度无关，估阶只用比值）。
    measured_sizes = [level.elements ** (-1.0 / 3.0) for level in levels]
    for name in quantity_names:
        # 用户模型没有精确解 ⇒ 给不出理论阶数，只能判"是否进入收敛区"。
        # 这也是为什么这里的 expected_order 一律为 None。
        assessments[name] = assess(
            [level.quantities[name] for level in levels],
            expected_order=None,
            label=labels.get(name, name),
            sizes=measured_sizes,
        )

    primary = analysis["primary"]
    primary_assessment = assessments.get(primary, {})
    all_converged = all(item["converged"] for item in assessments.values())
    failed = [
        labels.get(name, name)
        for name, item in assessments.items()
        if not item["converged"]
    ]

    # "够细了"的工程判据：主考察量的最后一级相对变化小于阈值。
    relative = primary_assessment.get("last_relative_change")
    changes = successive_differences(
        [level.quantities[primary] for level in levels]
    )
    shrink = all(later < earlier for earlier, later in zip(changes, changes[1:]))

    if all_converged and relative is not None and relative <= request.tolerance:
        status = "converged"
        verdict = (
            f"{labels.get(primary, primary)}逐级变化 {relative * 100:.2f}%"
            f"（阈值 {request.tolerance * 100:.2f}%），已进入收敛区；"
            "所有考察量都趋于稳定。**注意**：这只说明离散误差在减小，"
            "不代表模型、材料与边界条件正确。"
        )
    elif all_converged:
        # 都在趋稳（差值在缩小），但变化幅度还没降到阈值以下：可以定性比较，
        # 还不能当"足够细"。给一个独立状态，免得界面把它显示成"已收敛"。
        status = "marginal"
        verdict = (
            f"所有考察量都在单调趋稳，但{labels.get(primary, primary)}最后一级"
            f"仍变化 {relative * 100:.2f}%（阈值 {request.tolerance * 100:.2f}%）。"
            "可用于定性比较；要更稳请继续加密。"
        )
    else:
        status = "not-converged"
        verdict = (
            f"以下考察量**尚未进入收敛区**：{'、'.join(failed)}。"
            "它们的相邻差值没有变小。若指的是最大应力，请优先检查是否存在"
            "应力奇异（尖角、点载荷、单点约束）——那里真值本身无穷大，"
            "加密不会改善；否则请继续加密。"
        )

    if not shrink and status != "insufficient":
        notes.append(
            f"注意：{labels.get(primary, primary)}的逐级差值不是每一级都在缩小"
            f"（{['%.6g' % value for value in changes]}），收敛过程不平稳。"
        )

    # 把**实测**加密幅度说出来。这是本轮踩到的坑：gmsh 的 mesh_size 只是名义
    # 目标，真实网格并不按它成比例加密（实测 3/1.5/0.75 划出来的单元数是
    # 426/1366/9462，折算加密比 1.47 和 1.91）。不说明这一点，用户看到
    # "差值没变小"只会以为是自己的模型有问题。
    counts = [level.elements for level in levels]
    measured = [count ** (-1.0 / 3.0) for count in counts]
    actual_ratios = [
        measured[index] / measured[index + 1] for index in range(len(measured) - 1)
    ]
    notes.append(
        "实测各级单元数 "
        + " → ".join(str(count) for count in counts)
        + "，折算成平均单元尺寸后的**实际加密比**为 "
        + "、".join(f"{ratio:.2f}" for ratio in actual_ratios)
        + "（名义上每级减半 ⇒ 2.00）。收敛阶是按**实测**加密比估计的；"
        "若实际加密比明显偏离 2，说明 gmsh 没有按名义尺寸成比例加密，"
        "此时「差值必须变小」这个判据在前几级会失效。"
    )

    return StudyResponse(
        status=status,
        analysis_type=request.analysis_type,
        primary=primary,
        quantities=quantity_names,
        labels=labels,
        levels=levels,
        assessments=assessments,
        tolerance=request.tolerance,
        verdict=verdict,
        notes=notes,
        warnings=warnings,
    )


@router.post("/convergence/study", response_model=StudyResponse)
async def convergence_study(request: StudyRequest):
    """
    对当前模型做 h-收敛检查（同步）。

    逐级加密网格、用**同一份配置**重复求解，比较同一个物理量是否趋于稳定。
    级数一多耗时会明显增长（单元数按 h³ 涨），前端请用
    `POST /api/jobs/convergence` 走异步。
    """
    return await run_in_worker(study_impl, request=request)
