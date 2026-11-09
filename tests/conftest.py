# -*- coding: utf-8 -*-
"""pytest 公共配置：把 object_recognition/ 加进 sys.path。

这样测试里可以直接 `import library` / `import recognize`，
与运行时（在 object_recognition 目录下 python main.py）保持一致。
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PKG = os.path.join(ROOT, "object_recognition")
if PKG not in sys.path:
    sys.path.insert(0, PKG)
