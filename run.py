#!/usr/bin/env python
"""ai-code-reviewer 启动脚本。

直接执行：
    python run.py
    python run.py --port 9000
    python run.py --migrate

所有参数与 `python -m app` 一致。
本脚本自动把脚本所在目录加到 sys.path，
无论从哪个目录执行都能正确 import app 包。
"""

from __future__ import annotations

import sys
from pathlib import Path

# 把脚本所在目录加入 Python 路径，确保能 import app 包
SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from app.__main__ import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
