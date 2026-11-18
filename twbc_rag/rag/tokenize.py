"""jieba with a small building-code dictionary.

The dictionary is config/jieba_building.txt so terms like 建蔽率 and 私設通路
stay in one token. A built-in copy is used if that file is missing.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import jieba

from twbc_rag.paths import REPO_ROOT

_FALLBACK = """
建蔽率
容積率
樓地板面積
總樓地板面積
建築面積
私設通路
法定空地
防火間隔
防火構造
非防火構造
特別安全梯
緊急用昇降機
避難層
直通樓梯
無窗戶居室
屋頂突出物
但書
無障礙
分戶牆
防火牆
高層建築物
防災中心
停車空間
單車道
雙車道
""".strip()


@lru_cache(maxsize=1)
def _ready() -> bool:
    jieba.initialize()
    path = REPO_ROOT / "config" / "jieba_building.txt"
    if path.exists():
        jieba.load_userdict(str(path))
    else:
        tmp = Path("/tmp/twbc_jieba_userdict.txt")
        tmp.write_text(_FALLBACK + "\n", encoding="utf-8")
        jieba.load_userdict(str(tmp))
    return True


def tokenize(text: str) -> list[str]:
    _ready()
    tokens: list[str] = []
    for token in jieba.cut(text or "", cut_all=False):
        cleaned = token.strip()
        if len(cleaned) < 2:
            continue
        if cleaned.isdigit():
            tokens.append(cleaned)
            continue
        if all(not ch.isalnum() and ch not in "之－-" for ch in cleaned):
            continue
        tokens.append(cleaned)
    return tokens
