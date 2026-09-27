"""
收敛性检查（"网格够不够细"）——网格质量的另一半。

为什么必须有这一半
-----------------
`mesh_quality.py` 回答的是"网格干不干净"（单元形状好不好）。但**形状好不等于
够细**：一个全是正四面体、却只有 200 个单元的网格，算出来的应力峰值照样可能
差着百分之几十。判断"够不够细"只有一种办法——**加密，看结果是否收敛**。

本模块做两件事：

1. **收敛阶的度量与判定**（纯函数，可脱离网格单独测试）：给一串随加密变化的
   数值，算相邻差值、用 Richardson 外推估计收敛阶 `p` 与极限值，并给出结论。
2. **制造解基准（manufactured solution）**：一个**精确解已知**的问题，在逐级
   加密的网格上求解并测量真实误差，用来回答"我们究竟能不能测出收敛阶"。
   如果连一个精确解已知的问题都测不出理论阶数（P1 单元：L2 二阶、H1 一阶），
   那么对用户模型做任何"已收敛"的判断都是空话。

为什么制造解取二次函数
---------------------
取 `T = x² − y²`：它在三维里是**调和函数**（ΔT = 2 − 2 + 0 = 0），因此是
`∇·(k∇T) = 0` 的精确解，可以用现有的热传导算子直接算。关键是它**不属于
P1 单元空间**——如果取线性函数（例如 `T = x`），有限元解会逐节点精确，误差
恒为机器精度，**任何网格都测不出收敛阶**，基准就变成了空转。

两种网格来源，各回答一个问题
---------------------------
- `structured`：skfem 的结构化张量网格。每个方向 n 等分且**自相似**，所以
  误差比值应当**恰好**是 2^p。它检验的是"离散格式的阶数"和"我们量阶数的
  方法对不对"，因此可以用很紧的容差断言。
- `pipeline`：走**本项目真实的网格路径**（gmsh 划分 → `load_tet_mesh_from_msh`
  读回）。非结构网格在前渐近区比值会偏小（实测 L2 比值 3.50 → 3.80，对应阶数
  1.81 → 1.93），所以容差放宽。它检验的是"我们自己的网格读写没有破坏收敛性"
  ——上一轮 `edge_lengths` 那个漏转置的 bug 正是出在这条路径上。

诚实的边界（必须写清楚）
----------------------
基准复用 `thermal.assemble_conductivity`（生产代码的装配），但**没有**走
`solve_thermal_impl` 的边界条件映射：制造解的边界值在面上是**变化**的
（`T = x²− y²`），而当前的 BC 模型只支持"每个面一个常数温度"。所以本基准
覆盖的是**算子装配 + 求解 + 我们的网格读写**，不覆盖"面 → 节点"的 Dirichlet
映射（那条路径由 `tests/test_thermal.py` 的解析解断言负责）。

要让生产热传导求解器本身也能做制造解验证，需要给 BC 模型加"空间变化的
Dirichlet"，那是后续的事。
"""

from __future__ import annotations

import math
import os
import shutil
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from skfem import Basis, ElementTetP1, Functional, MeshTet, asm, condense, solve
from skfem.helpers import dot, grad

from auth import require_user
from config import resolve_upload_path
from fe_utils import load_tet_mesh_from_msh
from logging_config import get_logger
from thermal import assemble_conductivity

logger = get_logger(__name__)

#: 整个 router 都要求登录（与其余计算类端点一致，见 docs/04）
router = APIRouter(dependencies=[Depends(require_user)])

#: 结构化基准的默认加密级数。n = 4,8,16,32 → 单元数 384 … 196608。
STRUCTURED_LEVELS = (4, 8, 16, 32)

#: 结构化基准允许的最大单元数（防止有人把 level 调得过大把服务打死）
MAX_STRUCTURED_ELEMENTS = 400_000

#: 流水线基准默认的网格尺寸序列（立方体边长 10，所以 5→2.5→1.25 就是逐级减半）。
#: 用 4 级而不是 3 级是有原因的：**收敛阶只用最后两级差值估计**，而 H1 误差
#: 进入渐近区比 L2 慢——实测 3 级（2.5/1.25/0.625）给出的 H1 阶只有 0.68，
#: 多一级（0.3125）后升到 0.94。3 级也能过（容差放宽），但 4 级才是可信的数。
PIPELINE_MESH_SIZES = (2.5, 1.25, 0.625, 0.3125)

#: 流水线基准的单元数上限。最细一级（mesh_size=0.3125）约 15 万单元、耗时数秒，
#: 再细一级单元数翻 8 倍，服务端不合适。
MAX_PIPELINE_ELEMENTS = 250_000

#: 流水线基准用来划分网格的几何文件名。
#: **必须是独立的副本**：直接对 `default_cube.step` 划分会覆盖用户正在用的
#: `default_cube.step.msh`（上一轮已经因为"验证接口改了别的东西"吃过一次亏）。
BENCHMARK_GEOMETRY = "benchmark_cube.step"

#: 默认的加密比（每级网格尺寸减半）
DEFAULT_REFINEMENT_RATIO = 2.0


# ---------------------------------------------------------------------------
# 一、收敛阶的度量与判定（纯函数）
# ---------------------------------------------------------------------------

def successive_differences(values: Sequence[float]) -> List[float]:
    """相邻两级的绝对差 ``|u_{k+1} − u_k|``。"""
    return [abs(float(b) - float(a)) for a, b in zip(values, values[1:])]


def is_monotone(values: Sequence[float]) -> bool:
    """
    序列是否**单调**（只要求方向一致，不要求是递增）。

    为什么收敛判定必须先看单调性：Richardson 外推假设的是"误差按 h^p 单调
    衰减"。一个上下跳动的序列照样能算出一个 log 比值，但那个数字没有意义
    ——它只是两个随机数之比。所以这里先拦一道，避免给出一个看起来专业的假阶数。
    """
    numbers = [float(value) for value in values]
    if len(numbers) < 2:
        return True
    changes = [b - a for a, b in zip(numbers, numbers[1:])]
    non_zero = [change for change in changes if change != 0.0]
    if not non_zero:
        return True  # 完全不动，视为单调（但下面会判定为"未变化"）
    return all(change > 0 for change in non_zero) or all(change < 0 for change in non_zero)


def observed_order(
    values: Sequence[float], ratio: float = DEFAULT_REFINEMENT_RATIO
) -> Optional[float]:
    """
    用**最后三个值**估计观测收敛阶：``p = log(|u2−u1| / |u3−u2|) / log(ratio)``。

    返回 `None` 表示"这个序列给不出阶数"，具体有两种情况：

    - 相邻差值里有 0（外推没有意义，或者已经到机器精度）；
    - **后一段差值不小于前一段**——即加密之后差异没有变小。这正是"还没收敛"
      的典型表现，此时算出来的 p 会是负数或 0，与其报一个负数不如报"无法判断"。
    """
    if len(values) < 3:
        return None
    earlier = abs(float(values[-2]) - float(values[-3]))
    later = abs(float(values[-1]) - float(values[-2]))
    if earlier <= 0.0 or later <= 0.0:
        return None
    if later >= earlier:
        return None
    return math.log(earlier / later) / math.log(float(ratio))


def richardson_limit(
    values: Sequence[float],
    order: Optional[float],
    ratio: float = DEFAULT_REFINEMENT_RATIO,
) -> Optional[float]:
    """
    Richardson 外推的极限估计：``u* ≈ u3 + (u3 − u2) / (ratio^p − 1)``。

    `order` 为 `None`（或非正）时返回 `None`：没有阶数就没法外推，
    凭两个值猜一个极限是编数字。
    """
    if len(values) < 2 or order is None:
        return None
    if not math.isfinite(order) or order <= 0.0:
        return None
    factor = float(ratio) ** float(order) - 1.0
    if factor <= 0.0:
        return None
    return float(values[-1]) + (float(values[-1]) - float(values[-2])) / factor


def _richardson_ratio_function(order: float, sizes: Sequence[float]) -> float:
    """
    Richardson 关系左边的那个函数：``f(p) = (h1^p − h2^p) / (h2^p − h3^p)``。

    它来自误差模型 ``e_k = C·h_k^p``：相邻差值之比就是分子分母之比。
    加密比均匀（`h2 = h1/q`、`h3 = h1/q²`）时 `f(p) = q^p`，于是
    `p = log(r)/log(q)`——也就是常用的那个公式。**但真实网格常常不均匀**
    （见 `generalized_order` 的说明），那时这个函数才是对的。
    """
    h1, h2, h3 = (float(size) for size in sizes[-3:])
    numerator = h1 ** order - h2 ** order
    denominator = h2 ** order - h3 ** order
    if denominator <= 0.0:
        return float("inf")
    return numerator / denominator


def generalized_order(
    values: Sequence[float], sizes: Sequence[float]
) -> Optional[float]:
    """
    用**实测**网格尺寸估计收敛阶（非均匀加密下的正确做法）。

    为什么需要它：`observed_order` 假设"每级网格尺寸减半"。但 gmsh 的
    `mesh_size` 只是**名义**目标，真实网格并不按它成比例加密——实测
    `test_part.step` 用 3/1.5/0.75 划出来的单元数是 426/1366/9462，
    折算成平均单元尺寸后加密比是 1.47 和 1.91，**不是 2**。在这种数据上
    套 `log(r)/log(2)` 会算出错的阶数。

    做法：解 ``(h1^p − h2^p)/(h2^p − h3^p) = |Δ1|/|Δ2|``（二分法）。
    `f` 关于 `p` 单调递增，所以解唯一；解不存在时返回 `None`，含义是
    "**没有任何正的阶数能解释这组数据**"——那通常是还没进入渐近区，
    而不是阶数为负。

    `sizes` 只需与 `values` 同长且递减；绝对尺度无关（只用比值）。
    """
    if len(values) < 3 or len(sizes) != len(values):
        return None
    earlier = abs(float(values[-2]) - float(values[-3]))
    later = abs(float(values[-1]) - float(values[-2]))
    if earlier <= 0.0 or later <= 0.0:
        return None
    target = earlier / later

    h1, h2, h3 = (float(size) for size in sizes[-3:])
    if not (h1 > h2 > h3 > 0.0):
        return None

    # f 的下确界（p → 0+）就是对数比值；目标比它小的话，没有任何 p > 0 能解释
    floor_value = (math.log(h1) - math.log(h2)) / (math.log(h2) - math.log(h3))
    if target <= floor_value:
        return None

    low, high = 1e-6, 20.0
    if _richardson_ratio_function(high, sizes) < target:
        return None          # 阶数高于 20 —— 不现实，视为"测不出"
    for _ in range(200):
        middle = 0.5 * (low + high)
        if _richardson_ratio_function(middle, sizes) < target:
            low = middle
        else:
            high = middle
    return 0.5 * (low + high)


def assess(
    values: Sequence[float],
    ratio: float = DEFAULT_REFINEMENT_RATIO,
    expected_order: Optional[float] = None,
    order_tolerance: float = 0.35,
    label: str = "结果",
    sizes: Optional[Sequence[float]] = None,
) -> Dict[str, object]:
    """
    给一串"随加密变化"的数值下结论。

    判定顺序是刻意的：**先看能不能判**（级数够不够、单不单调、差异有没有变小），
    再看**阶数对不对**。这样"尚未收敛"和"收敛但阶数不对"是两句不同的话——
    前者要继续加密，后者说明离散格式或实现有问题，处理方式完全不同。

    `sizes` 给出**实测**的各级网格尺寸（只需与 `values` 同长且递减，绝对尺度
    无关）时会改用 `generalized_order` 估阶，并用最后两级的真实比值做外推。
    真实网格（gmsh 的非结构网格）常常不按名义尺寸成比例加密，此时
    `log(r)/log(2)` 那种算法会给出错的阶数——所以能拿到实测尺寸就一定要给。
    """
    numbers = [float(value) for value in values]
    differences = successive_differences(numbers)
    monotone = is_monotone(numbers)

    if sizes is not None and len(sizes) == len(numbers):
        order = generalized_order(numbers, sizes)
        # 外推只用最后两级的**真实**加密比
        local_ratio = (
            float(sizes[-2]) / float(sizes[-1]) if len(sizes) >= 2 else ratio
        )
        estimator = "generalized"
    else:
        order = observed_order(numbers, ratio)
        local_ratio = float(ratio)
        estimator = "uniform-ratio"
    limit = richardson_limit(numbers, order, local_ratio)

    result: Dict[str, object] = {
        "label": label,
        "values": numbers,
        "differences": differences,
        "levels": len(numbers),
        "refinement_ratio": float(local_ratio),
        "order_estimator": estimator,
        "monotone": monotone,
        "observed_order": order,
        "expected_order": expected_order,
        "extrapolated_limit": limit,
        "converged": False,
        "verdict": "",
    }

    if len(numbers) < 3:
        result["verdict"] = (
            f"{label}：至少需要 3 级网格才能估计收敛阶，当前只有 {len(numbers)} 级。"
        )
        return result

    last_difference = differences[-1]
    scale = max(abs(numbers[-1]), 1e-300)
    relative_change = last_difference / scale
    result["last_relative_change"] = relative_change

    if last_difference == 0.0:
        # 加密到"结果完全不变"：比收敛更好的情况，但此时阶数无从谈起
        result["converged"] = True
        result["verdict"] = (
            f"{label}：最后两级结果完全相同（已达机器精度），无需再加密。"
        )
        return result

    if not monotone:
        result["verdict"] = (
            f"{label}：数值随加密上下跳动（{numbers}），没有朝一个方向收敛。"
            "此时估计收敛阶没有意义，请检查边界条件与载荷是否随网格改变。"
        )
        return result

    if order is None:
        # 这条分支的措辞很重要：**没有任何正的收敛阶能解释这组数据**，
        # 可能的原因不止一个，把人只往"奇异"上引会让他白加密好几天。
        if estimator == "generalized":
            result["verdict"] = (
                f"{label}：相邻两级差值的变化**与网格加密的幅度不匹配**"
                f"（差值 {differences[-2]:.6g} → {differences[-1]:.6g}），"
                "没有任何正的收敛阶能解释这组数据。常见原因："
                "① 还停在前渐近区（起始网格太粗，或级数不够）；"
                "② 该量本身不收敛（应力奇异：尖角、点载荷、单点约束）；"
                "③ 各级网格并没有按预期幅度加密（逐级单元数见下）。"
                "建议先加一级或用更细的起始网格，再看结论是否改变。"
            )
        else:
            result["verdict"] = (
                f"{label}：加密后相邻两级的差异**没有变小**"
                f"（{differences[-2]:.6g} → {differences[-1]:.6g}），说明尚未进入收敛区。"
                "请继续加密，或检查是否存在应力奇异（尖角、点载荷）。"
            )
        return result

    if expected_order is not None and abs(order - expected_order) > order_tolerance:
        result["verdict"] = (
            f"{label}：观测收敛阶 {order:.2f} 与理论值 {expected_order:.2f} 相差过大"
            f"（容差 {order_tolerance:.2f}）。数值在变，但**不是按理论阶数**在收敛"
            "——这通常意味着离散格式或某个实现环节有问题，而不是网格不够细。"
        )
        return result

    result["converged"] = True
    if expected_order is not None:
        result["verdict"] = (
            f"{label}：观测收敛阶 {order:.2f}（理论 {expected_order:.2f}），"
            f"外推极限 ≈ {limit:.6g}，已按理论阶数收敛。"
        )
    else:
        result["verdict"] = (
            f"{label}：观测收敛阶 {order:.2f}，外推极限 ≈ "
            f"{limit if limit is None else format(limit, '.6g')}。"
        )
    return result


# ---------------------------------------------------------------------------
# 二、制造解：T = x² − y²（调和 ⇒ 是 ∇·(k∇T)=0 的精确解，且不属于 P1 空间）
# ---------------------------------------------------------------------------

def exact_temperature(coords: np.ndarray) -> np.ndarray:
    """精确解 `T = x² − y²`（`coords` 形状为 `(3, …)`）。"""
    return coords[0] ** 2 - coords[1] ** 2


def exact_gradient(coords: np.ndarray) -> np.ndarray:
    """精确解的梯度 `(2x, −2y, 0)`。"""
    return np.stack(
        [2.0 * coords[0], -2.0 * coords[1], np.zeros_like(coords[0])]
    )


@Functional
def _l2_error_squared(w):
    return (w["u"] - exact_temperature(w.x)) ** 2


@Functional
def _h1_error_squared(w):
    difference = grad(w["u"]) - exact_gradient(w.x)
    return dot(difference, difference)


def measure_errors(mesh: MeshTet) -> Dict[str, float]:
    """
    在给定网格上求解制造解问题，返回**真实的** L2 与 H1 误差（对精确解）。

    与"看两次结果差多少"（自收敛）相比，这是更强的检验：误差是相对**已知的
    精确解**算出来的，所以得到的不只是"变化在变小"，而是"离真值还有多远"。
    """
    basis = Basis(mesh, ElementTetP1())

    # 复用生产代码的装配（见 thermal.assemble_conductivity 的注释）
    stiffness = assemble_conductivity(basis, 1.0)

    # Dirichlet：整个边界按精确解给定。体网格的内部面已被 get_dofs 排除，
    # 所以这里拿到的是全部边界节点。
    prescribed = exact_temperature(basis.doflocs)
    boundary = basis.get_dofs()
    solution = solve(
        *condense(stiffness, np.zeros(basis.N), x=prescribed, D=boundary)
    )

    l2 = float(np.sqrt(asm(_l2_error_squared, basis, u=solution)))
    h1 = float(np.sqrt(asm(_h1_error_squared, basis, u=solution)))
    return {"l2": l2, "h1": h1, "nodes": int(basis.N), "elements": int(mesh.nelements)}


def structured_levels(
    n_values: Sequence[int] = STRUCTURED_LEVELS,
) -> List[Dict[str, Any]]:
    """
    结构化张量网格上的制造解误差。

    每个方向 n 等分 ⇒ `h = 1/n`，且各级网格**自相似**，因此误差比值应当恰好
    是 `2^p`（实测 L2 比值 4.0000、H1 比值 2.0000，见 tests）。
    """
    levels: List[Dict[str, Any]] = []
    for n in n_values:
        if n < 1:
            raise ValueError("加密级数必须是正整数")
        if 6 * int(n) ** 3 > MAX_STRUCTURED_ELEMENTS:
            raise ValueError(
                f"n={n} 会产生 {6 * int(n) ** 3} 个单元，超过上限 "
                f"{MAX_STRUCTURED_ELEMENTS}；请减少级数或降低上限"
            )
        coordinates = np.linspace(0.0, 1.0, int(n) + 1)
        mesh = MeshTet.init_tensor(coordinates, coordinates, coordinates)
        errors = measure_errors(mesh)
        levels.append({"label": f"n={int(n)}", "h": 1.0 / float(n), **errors})
    return levels


def _ensure_benchmark_geometry() -> str:
    """
    准备基准用的几何文件（**独立副本**，用完即删）。

    直接对 `default_cube.step` 划分网格会覆盖它旁边的 `.msh`，而那个网格可能
    正是用户当前在用的。上一轮已经因为"为了验证而调用的写接口改了别的东西"
    吃过一次亏（`POST /api/generate-cube` 重写被 git 跟踪的 STEP），所以这里
    一律用副本。
    """
    source = resolve_upload_path("default_cube.step")
    target = resolve_upload_path(BENCHMARK_GEOMETRY)
    if not source.exists():
        raise ValueError(
            "缺少 default_cube.step：请先调用 POST /api/generate-cube 生成基准几何"
        )
    shutil.copyfile(source, target)
    return BENCHMARK_GEOMETRY


async def pipeline_levels(
    mesh_sizes: Sequence[float] = PIPELINE_MESH_SIZES,
) -> List[Dict[str, Any]]:
    """
    同一问题走**真实网格流水线**（gmsh → `load_tet_mesh_from_msh`）的误差。

    这条路径才覆盖本项目自己的网格读写。非结构网格在前渐近区比值会偏小，
    所以判定容差放宽；但"阶数随加密趋近理论值"这一点必须成立。
    """
    from geometry import generate_mesh_impl

    filename = _ensure_benchmark_geometry()
    geometry_path = resolve_upload_path(filename)
    mesh_path = str(geometry_path) + ".msh"
    levels: List[Dict[str, Any]] = []
    try:
        for size in mesh_sizes:
            info = await generate_mesh_impl(filename=filename, mesh_size=float(size))
            element_count = len(info.elements)
            if element_count > MAX_PIPELINE_ELEMENTS:
                raise ValueError(
                    f"mesh_size={size} 会产生 {element_count} 个单元，超过上限 "
                    f"{MAX_PIPELINE_ELEMENTS}；请减少级数或放大网格尺寸"
                )
            mesh, _face_triangles = load_tet_mesh_from_msh(mesh_path)
            errors = measure_errors(mesh)
            levels.append(
                {"label": f"mesh_size={size:g}", "h": float(size), **errors}
            )
    finally:
        # 用完即删：这两个文件都不该留在上传目录里（几何副本不是 .msh，
        # 不受 .gitignore 里那条 `backend/uploads/*.msh` 保护）
        for path in (str(geometry_path), mesh_path):
            if os.path.exists(path):
                try:
                    os.remove(path)
                except OSError:  # pragma: no cover - 删除失败不该让整个请求失败
                    logger.warning("基准临时文件删除失败：%s", path)
    return levels


# ---------------------------------------------------------------------------
# 三、端点
# ---------------------------------------------------------------------------

class BenchmarkRequest(BaseModel):
    #: 'structured'（默认，快且比值精确）| 'pipeline'（走真实网格路径）| 'both'
    mode: str = "structured"
    #: **结构化**模式跑多少级（流水线模式固定跑完整序列，见端点注释）。
    #: 估计一个收敛阶最少需要 3 级；默认 4 级，多一级的代价是单元数翻 8 倍。
    levels: int = Field(
        default=4, ge=3, le=max(len(STRUCTURED_LEVELS), len(PIPELINE_MESH_SIZES))
    )

    model_config = {"extra": "forbid"}


class BenchmarkLevel(BaseModel):
    label: str
    h: float
    l2: float
    h1: float
    nodes: int
    elements: int


class BenchmarkResponse(BaseModel):
    status: str
    mode: str
    exact_solution: str
    notes: List[str]
    levels: List[BenchmarkLevel]
    l2: Dict[str, object]
    h1: Dict[str, object]
    verdict: str


#: P1 四面体单元的理论收敛阶（L2 范数与 H1 半范数）
THEORY_L2_ORDER = 2.0
THEORY_H1_ORDER = 1.0

#: 各网格来源的说明（写进结论里，避免"结果对了但不知道在测什么"）
_MODE_LABELS = {
    "structured": "结构化网格（各级自相似，比值应当恰好是 2^p）",
    "pipeline": "真实流水线（gmsh 划分 → load_tet_mesh_from_msh 读回）",
}

#: 判定容差（观测阶与理论阶之差的容许值）。
#: 结构化网格自相似，比值就是 2^p，可以收得很紧；非结构网格差一些，
#: 但 4 级实测 L2 阶 1.93、H1 阶 1.00，所以也能收紧（见 tests 的实测数值）。
_ORDER_TOLERANCE = {"structured": 0.20, "pipeline": 0.25}


@router.post("/convergence/benchmark", response_model=BenchmarkResponse)
async def convergence_benchmark(request: Optional[BenchmarkRequest] = None):
    """
    制造解收敛基准：在逐级加密的网格上测**真实误差**，看是否达到理论阶数。

    这是"我们究竟能不能测出收敛阶"的自检。它不针对用户的模型；针对用户模型
    的逐级加密检查是下一步（见 docs/03）。
    """
    request = request or BenchmarkRequest()

    if request.mode not in ("structured", "pipeline", "both"):
        raise HTTPException(
            status_code=400,
            detail="mode 只能是 structured / pipeline / both",
        )

    notes: List[str] = [
        "精确解 T = x² − y² 是三维调和函数（ΔT = 0），因此是 ∇·(k∇T)=0 的精确解；"
        "取二次而不取线性是刻意的——线性解落在 P1 单元空间里，有限元解逐节点精确，"
        "误差恒为机器精度，任何网格都测不出收敛阶。",
        "算子装配复用 thermal.assemble_conductivity（生产代码），但边界条件走的是"
        "「整边界按精确解给定」，而不是 solve_thermal_impl 的面→节点映射"
        "（当前的 BC 模型只支持每个面一个常数温度）。",
        "结构化与流水线用的是**不同的网格族**，因此分别估计收敛阶，不混在一条序列里。",
        "这里考察的量是**误差本身**，它的真极限是 0。所以外推极限不是 0 并不表示"
        "有问题——它衡量的是「序列里还残留多少更高阶项」（前渐近），与观测阶的"
        "判定是两件事。",
    ]

    # 按网格来源分组收集：分组的边界由代码显式给出，不靠"猜长度"
    groups: List[Tuple[str, List[Dict[str, Any]]]] = []
    try:
        if request.mode in ("structured", "both"):
            groups.append(
                ("structured", structured_levels(STRUCTURED_LEVELS[: request.levels]))
            )
        if request.mode in ("pipeline", "both"):
            # 流水线**始终跑完整序列**，不接受截短：收敛阶只由最后两级差值估计，
            # 截短等于把最细的那一级丢掉。实测截到 3 级时 H1 阶只有 0.68
            # （最细一级仍在前渐近区），会被误判成"没收敛"。
            groups.append(("pipeline", await pipeline_levels()))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("收敛基准失败")
        raise HTTPException(status_code=500, detail=f"收敛基准失败：{exc}")

    groups = [(mode, levels) for mode, levels in groups if levels]
    if not groups:
        raise HTTPException(status_code=400, detail="没有产生任何网格级别")

    collected = [level for _mode, levels in groups for level in levels]
    l2_assessments = _assess_per_group(groups, "l2", "L2 误差")
    h1_assessments = _assess_per_group(groups, "h1", "H1 误差")

    all_converged = all(
        item["converged"] for item in l2_assessments + h1_assessments
    )
    verdict = (
        "离散格式按理论阶数收敛（P1 四面体：L2 二阶、H1 一阶）。"
        "这说明加密网格确实在把结果推向真值，也是判定用户模型"
        "「够不够细」的前提。"
        if all_converged
        else "收敛性基准**未通过**：请逐条看下面的判定。基准不过时，"
        "不要相信任何「已收敛」的结论。"
    )

    primary_l2 = l2_assessments[0]
    primary_h1 = h1_assessments[0]
    logger.info(
        "收敛基准（%s）：L2 阶=%s，H1 阶=%s，通过=%s",
        request.mode,
        _format_order(primary_l2["observed_order"]),
        _format_order(primary_h1["observed_order"]),
        all_converged,
    )

    return BenchmarkResponse(
        status="ok" if all_converged else "not-converged",
        mode=request.mode,
        exact_solution="T = x^2 - y^2",
        notes=notes,
        levels=[BenchmarkLevel(**level) for level in collected],
        l2={"assessments": l2_assessments, **primary_l2},
        h1={"assessments": h1_assessments, **primary_h1},
        verdict=verdict,
    )


def _format_order(order: Optional[float]) -> str:
    return "无法判断" if order is None else f"{order:.2f}"


def _assess_per_group(
    groups: Sequence[Tuple[str, List[Dict[str, Any]]]],
    key: str,
    label: str,
) -> List[Dict[str, object]]:
    """
    对每一组网格序列分别判定（不同网格族不能混在一条序列里估阶）。

    加密比不写死为 2：这里从 `h` 现算，将来把流水线的尺寸序列改成别的
    比例（比如 1.5 倍而非 2 倍）时，阶数估计会自动跟着正确。
    """
    assessments: List[Dict[str, object]] = []
    for mode, group in groups:
        if not group:
            continue
        ratios = [
            float(group[index]["h"]) / float(group[index + 1]["h"])
            for index in range(len(group) - 1)
        ]
        ratio = ratios[0] if ratios else DEFAULT_REFINEMENT_RATIO
        expected = THEORY_L2_ORDER if key == "l2" else THEORY_H1_ORDER
        assessment = assess(
            [group[index][key] for index in range(len(group))],
            ratio=ratio,
            expected_order=expected,
            order_tolerance=_ORDER_TOLERANCE.get(mode, 0.5),
            label=f"{_MODE_LABELS.get(mode, mode)} · {label}",
        )
        assessment["mode"] = mode
        assessments.append(assessment)
    return assessments
