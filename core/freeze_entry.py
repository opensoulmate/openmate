"""PyInstaller 冻结入口：把 core/download_engine 打成单文件（引擎=纯 Python，零改动）。

构建（见 build_engine.sh）：
    pyinstaller --onefile --name download-engine --paths <core> freeze_entry.py

产物 dist/download-engine 与 `python -m download_engine` 同一 CLI/REST 契约
（首行 stdout JSON 握手：{"event":"listening","port":N,"token":"..."}）。
"""

import sys

from download_engine.cli import main

if __name__ == "__main__":
    sys.exit(main())
