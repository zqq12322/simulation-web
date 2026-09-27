"""
Gmsh 会话管理。

为什么需要这一层
----------------
实测发现（`gmsh.py` 源码）：

- ``gmsh.initialize()`` 会调用 ``signal.signal(SIGINT, ...)``
- ``gmsh.finalize()`` 会把它改回去

而**信号处理器只能在主线程设置**。因此工作线程里既不能 ``initialize()``
也不能 ``finalize()``（会抛 ``ValueError: signal only works in main thread``）。

于是约定如下：

1. 在**主线程**（模块导入时）初始化一次，进程内一直复用；
2. 每次操作前用 ``gmsh.clear()`` 重置模型状态，**不再** initialize/finalize；
3. 所有 gmsh 操作仍然串行执行（见 `jobs.py` 的单线程工作器），
   避免并发破坏 gmsh 的进程级全局状态。

顺带修掉一个潜在问题：以前依赖"每个请求一次 initialize"来获得干净状态，
一旦 gmsh 被复用，模型就会不断累积；现在统一由 ``start_model()`` 显式清理。
"""

import gmsh

from logging_config import get_logger

logger = get_logger(__name__)

_initialized = False


def ensure_initialized() -> bool:
    """
    在主线程初始化 gmsh（幂等）。非主线程调用会被拒绝并返回 False——
    那是设计约束，不是可以绕过的错误。
    """
    global _initialized
    if _initialized and gmsh.isInitialized():
        return True

    import threading

    if threading.current_thread() is not threading.main_thread():
        logger.debug("跳过 gmsh.initialize()：信号处理只能在主线程设置")
        return False

    gmsh.initialize()
    _initialized = True
    logger.info("Gmsh 已初始化（会话级，进程内复用）")
    return True


def start_model(name: str) -> None:
    """
    开始一次新的几何/网格操作：确保会话可用、清空旧模型、建立命名模型。

    这是所有 gmsh 操作的统一入口，替代原先"每处自己 initialize + finalize"的写法。
    """
    if not ensure_initialized():
        raise RuntimeError(
            "Gmsh 未初始化：必须在主线程先调用 gmsh_session.ensure_initialized()"
        )
    gmsh.clear()
    gmsh.model.add(name)


def open_model_file(path: str) -> None:
    """
    清空旧模型并打开一个几何/网格文件（.msh / .stp / .stl ...）。

    ``gmsh.open`` 内部等价于 clear + merge，因此不会残留上一个模型。
    """
    if not ensure_initialized():
        raise RuntimeError(
            "Gmsh 未初始化：必须在主线程先调用 gmsh_session.ensure_initialized()"
        )
    gmsh.clear()
    gmsh.open(path)
