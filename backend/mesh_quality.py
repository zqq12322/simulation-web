"""
单元质量度量（网格质量检查的第一半）。

为什么需要它
------------
有限元结果的可信度取决于网格，而"网格够不够好"有两个独立的方面：

1. **单元形状**（本模块）：畸形单元（尤其压扁的"刀片"四面体）会让刚度矩阵
   病态，应力在这些单元里出现毫无意义的尖峰；
2. **单元密度**（下一轮做收敛性检查）：形状再好，网格太粗结果也不准。

用户最常问的正是"我的网格行不行"。在此之前这个项目只能回答"生成了 N 个单元"
——那不是答案。

度量选什么，以及为什么它可以被验证
----------------------------------
对每个四面体，取 6 条棱长的均方根 `L`，与体积 `V`，定义::

    形状质量 q = V / (L³ / (6√2))  ∈ (0, 1]

分母是"棱长为 L 的正四面体的体积"，因此 **q = 1 当且仅当单元是正四面体**，
越扁越小，退化（体积为零）时为 0。

选它的理由是**可解析验证**：

- 正四面体 → q 恰好为 1；
- 共面（退化）→ q 恰好为 0；
- 直角四面体（三条互相垂直的单位棱）→ q = (1/6)·6√2 / (3/2)^{3/2} ≈ 0.7698，
  可以手算出来对照（见 `tests/test_mesh_quality.py`）。

对比之下，"看起来合理"的度量（例如只报最小/最大棱长比）无法用解析解钉住，
而本项目已经吃过"只断言'结果有限'所以漏掉数量级错误"的亏。

单位无关性
----------
`q` 是纯几何比值，**与长度单位无关**（分子分母都是长度的三次方）。
这一点有测试钉住：同一网格按 m / mm 解释时质量分布必须完全相同。
"""

from __future__ import annotations

import os
from typing import Dict, List, Optional

import numpy as np
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from auth import require_user
from config import resolve_upload_path
from fe_utils import load_tet_mesh_from_msh
from jobs import run_in_worker
from logging_config import get_logger

logger = get_logger(__name__)

#: 整个 router 都要求登录（与其余计算类端点一致，见 docs/04）
router = APIRouter(dependencies=[Depends(require_user)])

#: 直方图分箱数（质量区间 [0, 1] 等分）
HISTOGRAM_BINS = 10

#: 判定阈值。"差"的门槛选 0.1：线性四面体的经验做法里，q < 0.1 的单元通常
#: 已经明显影响应力结果的局部精度。这里**只做提示**，不阻止求解——
#: 是否可接受最终取决于收敛性（下一轮的检查）。
POOR_QUALITY_THRESHOLD = 0.1

#: 一个网格最多返回多少个"最差单元"的明细
MAX_WORST_ELEMENTS = 10


def tet_volumes(points: np.ndarray, cells: np.ndarray) -> np.ndarray:
    """
    每个四面体的**有符号**体积（正 = 节点顺序右手，负 = 左手）。

    保留符号是有意的：负体积说明单元朝向反了（雅可比为负），那是比"形状差"
    更严重的问题，必须能被数出来而不是取个绝对值掩盖掉。
    """
    v0, v1, v2, v3 = (points[:, cells[i]] for i in range(4))
    # NumPy >= 2.0 的 np.cross 沿末轴计算，因此先转置成 (n_elements, 3)
    det = np.einsum("ij,ij->i", np.cross((v1 - v0).T, (v2 - v0).T), (v3 - v0).T)
    return det / 6.0


def edge_lengths(points: np.ndarray, cells: np.ndarray) -> np.ndarray:
    """每个四面体的 6 条棱长，形状 ``(n_elements, 6)``。"""
    # 6 条棱：(0,1) (0,2) (0,3) (1,2) (1,3) (2,3)
    pairs = ((0, 1), (0, 2), (0, 3), (1, 2), (1, 3), (2, 3))
    lengths = []
    for a, b in pairs:
        # **必须转置**：`points[:, cells[a]]` 的形状是 (3, n_elements)，
        # 而 `einsum("ij,ij->i")` 沿末轴求和。不转置的话它会沿着**元素**轴求和，
        # 得到一个 (3,) 的"每条棱一个坐标分量"的怪东西——单元数为 1 时还看不出
        # 问题，单元一多就全错。（这个 bug 是被"正四面体质量必须恰好为 1"
        # 的解析断言抓到的：实测 5.196 = 3√3。）
        delta = (points[:, cells[a]] - points[:, cells[b]]).T
        lengths.append(np.sqrt(np.einsum("ij,ij->i", delta, delta)))
    return np.stack(lengths, axis=1)


def shape_quality(points: np.ndarray, cells: np.ndarray) -> np.ndarray:
    """
    形状质量 ``q`` ∈ [0, 1]（定义见模块文档）。

    正四面体为 1，退化（共面）为 0。**不做绝对值**：体积为负时 q 为负，
    调用方据此单独统计"朝向错误的单元"。
    """
    volumes = tet_volumes(points, cells)
    lengths = edge_lengths(points, cells)
    rms = np.sqrt(np.mean(lengths ** 2, axis=1))
    # 防止除零：RMS 为 0 只可能出现在退化到"所有顶点重合"的病态数据里
    safe = np.where(rms > 0, rms, 1.0)
    ideal = (safe ** 3) / (6.0 * np.sqrt(2.0))
    return volumes / ideal


def edge_ratios(points: np.ndarray, cells: np.ndarray) -> np.ndarray:
    """最长棱 / 最短棱；1 表示各棱等长，越大越扁。"""
    lengths = edge_lengths(points, cells)
    longest = lengths.max(axis=1)
    shortest = lengths.min(axis=1)
    safe = np.where(shortest > 0, shortest, 1.0)
    return longest / safe


def build_histogram(values: np.ndarray, bins: int = HISTOGRAM_BINS) -> List[dict]:
    """
    把质量值统计成 ``[0, 1]`` 上的等宽直方图。

    **负值与越界值单独归入首尾箱**，而不是被 numpy 丢弃：
    直方图的所有箱计数之和必须等于单元总数，否则界面上看起来"少了一些单元"。
    """
    edges = np.linspace(0.0, 1.0, bins + 1)
    counts, _ = np.histogram(np.clip(values, 0.0, 1.0), bins=edges)
    return [
        {
            "lo": float(edges[index]),
            "hi": float(edges[index + 1]),
            "count": int(counts[index]),
        }
        for index in range(bins)
    ]


def summarise(
    points: np.ndarray, cells: np.ndarray
) -> Dict[str, object]:
    """
    汇总一个网格的质量：统计量 + 直方图 + 最差单元明细。

    纯函数（只吃 numpy 数组），因此可以脱离 gmsh / 文件系统单独测试。
    """
    if cells.size == 0:
        raise ValueError("网格里没有四面体单元")

    volumes = tet_volumes(points, cells)
    quality = shape_quality(points, cells)
    ratios = edge_ratios(points, cells)
    finite = np.isfinite(quality)

    # 非正体积 = 退化或朝向错误。两者都让刚度矩阵病态，必须如实报出来，
    # 而不是靠绝对值把它们混进"质量不错"的统计里。
    non_positive = int(np.count_nonzero(volumes <= 0))
    non_finite = int(np.count_nonzero(~finite))
    usable = quality[finite]

    worst_order = np.argsort(quality)[:MAX_WORST_ELEMENTS]
    worst = [
        {
            "index": int(index),
            "quality": float(quality[index]),
            "volume": float(volumes[index]),
            "edgeRatio": float(ratios[index]),
        }
        for index in worst_order
    ]

    return {
        "elements": int(cells.shape[1]),
        "nodes": int(points.shape[1]),
        "total_volume": float(volumes[np.isfinite(volumes)].sum()),
        "quality": {
            "min": float(usable.min()) if usable.size else 0.0,
            "max": float(usable.max()) if usable.size else 0.0,
            "mean": float(usable.mean()) if usable.size else 0.0,
            # 用中位数与 5% 分位而不是只给最小值：单个坏单元不该掩盖整体分布
            "median": float(np.median(usable)) if usable.size else 0.0,
            "p05": float(np.percentile(usable, 5)) if usable.size else 0.0,
        },
        "edge_ratio_max": float(ratios.max()) if ratios.size else 1.0,
        "poor_count": int(np.count_nonzero(usable < POOR_QUALITY_THRESHOLD)),
        "non_positive_volume_count": non_positive,
        "non_finite_count": non_finite,
        "histogram": build_histogram(quality),
        "worst_elements": worst,
        "poor_threshold": POOR_QUALITY_THRESHOLD,
    }


class MeshQualityResponse(BaseModel):
    status: str
    filename: str
    elements: int
    nodes: int
    total_volume: float
    quality: Dict[str, float]
    edge_ratio_max: float
    poor_count: int
    non_positive_volume_count: int
    non_finite_count: int
    histogram: List[Dict[str, float]]
    worst_elements: List[Dict[str, float]]
    poor_threshold: float
    #: 供界面直接显示的结论（避免前端自己再编一套判据）
    verdict: str


def _verdict(summary: Dict[str, object]) -> str:
    """
    给一句可直接显示的结论。

    措辞刻意保守：**形状质量只说明网格"干净不干净"，不说明"够不够细"**。
    后者要等收敛性检查（下一轮）。把两者混为一谈会让用户以为"质量直方图好看"
    就等于"结果可信"。
    """
    if summary["non_positive_volume_count"]:  # type: ignore[index]
        return (
            f"发现 {summary['non_positive_volume_count']} 个体积非正的单元"  # type: ignore[index]
            "（退化或朝向错误），请加密网格或检查几何。"
        )
    if summary["poor_count"]:  # type: ignore[index]
        return (
            f"有 {summary['poor_count']} 个单元形状质量低于"  # type: ignore[index]
            f" {POOR_QUALITY_THRESHOLD}，局部应力可能不可靠。"
            "注意：形状好也不代表网格够细，密度是否足够要看收敛性。"
        )
    return "单元形状质量良好。注意：形状好不等于网格够细，密度是否足够要看收敛性。"


@router.get("/mesh-quality", response_model=MeshQualityResponse)
async def mesh_quality(filename: str):
    """
    网格质量检查（同步接口）。

    需要已经生成过网格（`POST /api/generate-mesh`）。返回形状质量的统计量、
    直方图与最差单元明细；**不判断"够不够细"**——那是收敛性检查的事。
    """
    return await run_in_worker(mesh_quality_impl, filename=filename)


async def mesh_quality_impl(filename: str) -> MeshQualityResponse:
    """网格质量检查的实现（在工作线程里执行）。"""
    try:
        file_path = str(resolve_upload_path(filename))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"文件名不合法：{exc}")

    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="Geometry file not found")

    msh_path = file_path + ".msh"
    if not os.path.exists(msh_path):
        raise HTTPException(
            status_code=409,
            detail="尚未生成网格：请先调用 POST /api/generate-mesh",
        )

    try:
        mesh, _face_triangles = load_tet_mesh_from_msh(msh_path)
        summary = summarise(mesh.p, mesh.t)
    except ValueError as exc:
        # 空网格/读不出单元：是可诊断的输入问题，不是服务端崩溃
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        logger.exception("网格质量检查失败")
        raise HTTPException(status_code=500, detail=f"网格质量检查失败：{exc}")

    logger.info(
        "网格质量：%s，%d 单元，q_min=%.4g q_mean=%.4g，较差 %d 个",
        filename, summary["elements"], summary["quality"]["min"],  # type: ignore[index]
        summary["quality"]["mean"], summary["poor_count"],  # type: ignore[index]
    )

    return MeshQualityResponse(
        status="ok",
        filename=filename,
        verdict=_verdict(summary),
        **summary,  # type: ignore[arg-type]
    )
