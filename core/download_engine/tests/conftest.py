import os
import sys

# 让 `import download_engine` 在任意 cwd 下可用（tests 在包内，core/ 入 sys.path）
sys.path.insert(
    0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
)
