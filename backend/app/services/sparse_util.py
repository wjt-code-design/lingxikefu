"""中文 sparse 向量构造（hybrid 检索，ADR-2026-08-16 §3.2）。

- 字符级 bigram + crc32 确定性哈希 → SparseVector(indices, values)。
- 为什么 bigram 不用 jieba：零依赖、无未登录词问题（"Z9 Pro"等品牌名 bigram 仍可命中）；
  bigram 召回不足时再评估 jieba（见 ADR）。
- 为什么 crc32 不用内置 hash()：Python str hash 进程级 salted（PYTHONHASHSEED），跨进程/跨重启不稳定，
  入库与检索必须同一 hash → crc32（确定性、快）。
- 去空白（空格/换行）后取 bigram：连续汉字/字母数字组合可成对，空格打断避免跨词污染。
"""
from __future__ import annotations

import zlib

from qdrant_client.http.models import SparseVector

#: 稀疏向量槽位数（2^20 ≈ 100 万），bigram 空间远小于此，碰撞可忽略
SPARSE_DIM = 2**20


def text_to_sparse(text: str, dim: int = SPARSE_DIM) -> SparseVector:
    """文本 → 稀疏向量（bigram 词频）。返回 SparseVector（indices 升序，值=词频）。"""
    compact = "".join(ch for ch in text if not ch.isspace())
    tf: dict[int, float] = {}
    for i in range(len(compact) - 1):
        gram = compact[i : i + 2]
        idx = zlib.crc32(gram.encode("utf-8")) % dim
        tf[idx] = tf.get(idx, 0) + 1.0
    indices = sorted(tf)
    return SparseVector(indices=indices, values=[tf[i] for i in indices])
