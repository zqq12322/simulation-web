"""
集中配置（Central configuration）。

设计要点
--------
1. **所有路径基于本文件位置解析**，不再依赖启动时的工作目录。
   此前 ``UPLOAD_DIR = "uploads"`` 是相对路径，必须 ``cd backend`` 才能正确启动，
   否则几何文件会被写到别处——这是协作时最容易踩的坑之一。
2. **所有可调参数集中在此**，避免魔法数字散落在各个模块。
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Final

# --------------------------------------------------------------------- 路径
BASE_DIR: Final[Path] = Path(__file__).resolve().parent
UPLOAD_DIR: Final[Path] = BASE_DIR / "uploads"

# ----------------------------------------------------------------- 上传限制
#: 单个几何文件大小上限（默认 50 MB）
MAX_UPLOAD_BYTES: Final[int] = int(
    os.getenv("MAX_UPLOAD_BYTES", str(50 * 1024 * 1024))
)

#: 允许上传的几何格式（小写后缀）
ALLOWED_GEOMETRY_EXTENSIONS: Final[frozenset] = frozenset(
    {".stl", ".step", ".stp", ".iges", ".igs"}
)

# ----------------------------------------------------------------- 网格参数
MESH_SIZE_MIN: Final[float] = float(os.getenv("MESH_SIZE_MIN", "0.01"))
MESH_SIZE_MAX: Final[float] = float(os.getenv("MESH_SIZE_MAX", "100.0"))
DEFAULT_MESH_SIZE: Final[float] = float(os.getenv("DEFAULT_MESH_SIZE", "1.0"))

# --------------------------------------------------------------- 服务行为
#: 逗号分隔的允许来源；默认 "*" 便于本地开发，**生产环境务必收紧**
CORS_ALLOW_ORIGINS: Final[list] = [
    origin.strip()
    for origin in os.getenv("CORS_ALLOW_ORIGINS", "*").split(",")
    if origin.strip()
]

#: Gmsh 控制台输出（1=输出进度，0=静默）
GMSH_TERMINAL: Final[int] = int(os.getenv("GMSH_TERMINAL", "1"))


def ensure_upload_dir() -> Path:
    """确保上传目录存在并返回该目录。"""
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    return UPLOAD_DIR


def resolve_upload_path(filename: str) -> Path:
    """
    把用户提供的文件名安全地解析为上传目录内的绝对路径。

    拒绝：空文件名、任何路径分隔符、``..``、绝对路径/盘符、
    不在白名单内的扩展名，以及解析后逃逸出上传目录的情况（路径穿越）。

    Raises:
        ValueError: 文件名不合法时。
    """
    if not filename or not filename.strip():
        raise ValueError("文件名为空")

    # 只接受纯文件名：出现任何目录成分即判为非法
    if filename != Path(filename).name or filename in {".", ".."}:
        raise ValueError("文件名不合法：不允许包含目录路径")

    if "/" in filename or "\\" in filename or ":" in filename:
        raise ValueError("文件名不合法：不允许包含路径分隔符或盘符")

    if "\x00" in filename:
        raise ValueError("文件名不合法：包含非法字符")

    suffix = Path(filename).suffix.lower()
    if suffix not in ALLOWED_GEOMETRY_EXTENSIONS:
        allowed = ", ".join(sorted(ALLOWED_GEOMETRY_EXTENSIONS))
        raise ValueError(
            f"不支持的文件类型 '{suffix or '（无扩展名）'}'，仅支持：{allowed}"
        )

    ensure_upload_dir()
    candidate = (UPLOAD_DIR / filename).resolve()

    # 双保险：解析（含符号链接/相对成分）后仍必须恰好落在上传目录下
    if candidate.parent != UPLOAD_DIR.resolve():
        raise ValueError("文件名不合法：解析后超出上传目录")

    return candidate


def validate_mesh_size(mesh_size) -> float:
    """校验并归一化网格尺寸，返回 float。"""
    try:
        value = float(mesh_size)
    except (TypeError, ValueError):
        raise ValueError(f"mesh_size 必须是数字，收到 {mesh_size!r}")

    if not (MESH_SIZE_MIN <= value <= MESH_SIZE_MAX):
        raise ValueError(
            f"mesh_size 必须在 [{MESH_SIZE_MIN}, {MESH_SIZE_MAX}] 之间，收到 {value}"
        )

    return value
