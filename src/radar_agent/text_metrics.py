"""LLM 文字指標（docs/08「LLM 文字生成指標」）：模型輸出對人寫參考文字的相似度，0～100。

中文以「字」為單位（空白不算）。BLEU 依整份語料計算（clipped n-gram precision 的幾何平均乘上長度懲罰，
不做平滑）；ROUGE-1、ROUGE-L 取每對的 F1 再平均。BERTScore 需要另外的語言模型，不在這裡。
參考文字要由護理人員就同一段事件撰寫（會議清單 F13）。
"""
from __future__ import annotations

import math
from collections import Counter
from collections.abc import Sequence

Pairs = Sequence[tuple[str, str]]  # （模型輸出, 參考文字）


def _chars(text: str) -> list[str]:
    return [c for c in text if not c.isspace()]


def _ngrams(chars: list[str], n: int) -> Counter[tuple[str, ...]]:
    return Counter(tuple(chars[i:i + n]) for i in range(len(chars) - n + 1))


def _check(pairs: Pairs) -> None:
    if not pairs:
        raise ValueError("沒有任何（輸出, 參考）配對")


def bleu(pairs: Pairs, max_n: int) -> float:
    """BLEU-max_n：1～max_n gram 的 clipped precision 取幾何平均，乘上長度懲罰。"""
    _check(pairs)
    matched = [0] * max_n
    total = [0] * max_n
    cand_len = ref_len = 0
    for cand, ref in pairs:
        c, r = _chars(cand), _chars(ref)
        cand_len, ref_len = cand_len + len(c), ref_len + len(r)
        for n in range(1, max_n + 1):
            cn, rn = _ngrams(c, n), _ngrams(r, n)
            matched[n - 1] += sum(min(k, rn[g]) for g, k in cn.items())
            total[n - 1] += sum(cn.values())
    if not all(matched) or not cand_len:
        return 0.0
    log_precision = sum(math.log(m / t) for m, t in zip(matched, total, strict=True)) / max_n
    brevity = 1.0 if cand_len >= ref_len else math.exp(1 - ref_len / cand_len)
    return 100 * brevity * math.exp(log_precision)


def _f1(overlap: int, cand_len: int, ref_len: int) -> float:
    if not overlap:
        return 0.0
    p, r = overlap / cand_len, overlap / ref_len
    return 2 * p * r / (p + r)


def rouge_1(pairs: Pairs) -> float:
    _check(pairs)
    scores = []
    for cand, ref in pairs:
        c, r = Counter(_chars(cand)), Counter(_chars(ref))
        scores.append(_f1(sum((c & r).values()), sum(c.values()), sum(r.values())))
    return 100 * sum(scores) / len(scores)


def _lcs(a: list[str], b: list[str]) -> int:
    prev = [0] * (len(b) + 1)
    for x in a:
        cur = [0]
        for j, y in enumerate(b, start=1):
            cur.append(prev[j - 1] + 1 if x == y else max(prev[j], cur[j - 1]))
        prev = cur
    return prev[-1]


def rouge_l(pairs: Pairs) -> float:
    _check(pairs)
    scores = []
    for cand, ref in pairs:
        c, r = _chars(cand), _chars(ref)
        scores.append(_f1(_lcs(c, r), len(c), len(r)))
    return 100 * sum(scores) / len(scores)
