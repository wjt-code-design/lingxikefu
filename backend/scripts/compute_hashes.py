"""计算 golden 集的 SHA256 hash（用于版本追踪）。"""
import hashlib
from pathlib import Path

files = [
    "tests/golden/answer_golden_set.txt",
    "tests/golden/route_eval_set.txt",
]

for f in files:
    p = Path(f)
    h = hashlib.sha256(p.read_bytes()).hexdigest()
    print(f"{f}: {h}")
