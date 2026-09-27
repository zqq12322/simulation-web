"""
后端测试包。

保证无论从仓库根目录还是 ``backend/`` 目录运行，都能 import 到后端模块
（``config`` / ``geometry`` / ``solver`` ...）。
"""

import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))
