"""
统一的日志配置。

此前项目各处直接用 ``print()``：无法分级、无法关闭、也没有模块来源，
协作排查问题时噪音很大。现在统一走 ``logging``：

- 全部挂在 ``simcloud.*`` 命名空间下，与 uvicorn 自己的日志互不干扰；
- 级别由 ``LOG_LEVEL`` 环境变量控制（默认 INFO，排查时设 DEBUG）；
- 输出格式带时间、级别与模块名，便于定位是哪一步出的问题。

用法::

    from logging_config import get_logger
    logger = get_logger(__name__)
    logger.info("...")
"""

import logging
import sys

from config import LOG_LEVEL

LOGGER_NAMESPACE = "simcloud"

_configured = False


def configure_logging() -> None:
    """初始化根 logger（幂等，重复调用无副作用）。"""
    global _configured
    if _configured:
        return

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        logging.Formatter(
            fmt="%(asctime)s %(levelname)-7s [%(name)s] %(message)s",
            datefmt="%H:%M:%S",
        )
    )

    root = logging.getLogger(LOGGER_NAMESPACE)
    root.setLevel(LOG_LEVEL)
    root.addHandler(handler)
    # 不要向上冒泡到 Python 的 root logger，避免与 uvicorn 的输出重复
    root.propagate = False

    _configured = True


def get_logger(name: str) -> logging.Logger:
    """取得 ``simcloud.<name>`` 命名空间下的 logger。"""
    configure_logging()
    suffix = name.split(".")[-1] if name else "app"
    return logging.getLogger(f"{LOGGER_NAMESPACE}.{suffix}")
