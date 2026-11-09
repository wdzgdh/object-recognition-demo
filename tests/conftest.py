# -*- coding: utf-8 -*-
"""pytest 公共配置：把 object_recognition/ 与 tools/ 加进 sys.path。

这样测试里可以直接 `import library` / `import recognize`（与运行时保持一致），
也能 `import metrics` / `import evaluate`（tools 下的纯 Python 工具）。
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PKG = os.path.join(ROOT, "object_recognition")
TOOLS = os.path.join(ROOT, "tools")
for _p in (TOOLS, PKG):          # PKG 优先，插在最后
    if _p not in sys.path:
        sys.path.insert(0, _p)
