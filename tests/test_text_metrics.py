"""LLM 文字指標（docs/08）：BLEU-1、BLEU-4、ROUGE-1、ROUGE-L，以字為單位，0～100。

數值用手算驗證；不需要模型或外部套件。BERTScore 要另外的語言模型，不在這裡。
"""
import math

import pytest

from radar_agent.text_metrics import bleu, rouge_1, rouge_l


def test_identical_text_scores_100():
    pairs = [("長者回覆沒事。", "長者回覆沒事。")]
    assert bleu(pairs, 1) == bleu(pairs, 4) == rouge_1(pairs) == rouge_l(pairs) == 100


def test_bleu_counts_clipped_ngram_matches():
    pairs = [("abcd", "abce")]
    assert bleu(pairs, 1) == pytest.approx(75)
    assert bleu(pairs, 4) == 0  # 沒有任何 4-gram 相同


def test_short_candidate_gets_the_brevity_penalty():
    assert bleu([("ab", "abcd")], 1) == pytest.approx(100 * math.exp(1 - 4 / 2))


def test_bleu_is_computed_over_the_whole_corpus():
    """BLEU 是整份語料一起算（不是每句平均），一句完全不同不會讓整體變 0。"""
    assert bleu([("ab", "ab"), ("cd", "xy")], 1) == pytest.approx(50)


def test_rouge_1_is_unigram_f1():
    assert rouge_1([("aab", "ab")]) == pytest.approx(80)  # P 2/3、R 1


def test_rouge_l_uses_the_longest_common_subsequence():
    assert rouge_l([("abcd", "acbd")]) == pytest.approx(75)  # LCS 3


def test_spaces_are_ignored():
    assert rouge_l([("a b c", "abc")]) == 100


def test_empty_corpus_is_rejected():
    with pytest.raises(ValueError):
        bleu([], 1)


def test_nothing_in_common_scores_zero():
    assert rouge_1([("ab", "xy")]) == rouge_l([("ab", "xy")]) == bleu([("ab", "xy")], 1) == 0
