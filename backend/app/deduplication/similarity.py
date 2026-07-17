"""文本相似度（轻量实现，无外部重依赖）."""

from __future__ import annotations


def char_ngrams(text: str, n: int = 2) -> set[str]:
    t = (text or "").strip()
    if len(t) < n:
        return {t} if t else set()
    return {t[i : i + n] for i in range(len(t) - n + 1)}


def jaccard(a: set[str], b: set[str]) -> float:
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    inter = len(a & b)
    union = len(a | b)
    return inter / union if union else 0.0


def title_similarity(title_a: str, title_b: str) -> float:
    from app.deduplication.normalize import normalize_title

    na, nb = normalize_title(title_a), normalize_title(title_b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0
    # 包含关系
    if na in nb or nb in na:
        shorter, longer = (na, nb) if len(na) <= len(nb) else (nb, na)
        if len(shorter) >= 6:
            return 0.92
    return jaccard(char_ngrams(na), char_ngrams(nb))


def content_similarity(text_a: str, text_b: str) -> float:
    a = (text_a or "")[:1500]
    b = (text_b or "")[:1500]
    if not a or not b:
        return 0.0
    return jaccard(char_ngrams(a, 3), char_ngrams(b, 3))
