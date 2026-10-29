"""在地模型改寫層（docs/02「在地模型負載與分流」）：模型的輸出通過檢查才採用，否則退回範本。

模型以「prompt → 文字」的假模型代替（外部服務的邊界，REVIEW.md 3.5）；範本、檢查都用真的。
"""
import ast
import hashlib
import inspect
import textwrap

import pytest
from test_caregiver import timeline

from radar_agent import kg_rules
from radar_agent.caregiver import caregiver_text
from radar_agent.device_events import summarize
from radar_agent.rewrite import PROMPT_VERSION, rewrite

PLACE = "1 號廁所"
ALL_ONLINE = summarize(timeline("09:00:00", "09:30:00", [("09:05:00", "09:13:00"), ("09:20:00", "09:21:30")]))
# 範本：1 號廁所 10/14：有人使用 2 次，最長一次 8 分鐘。\n沒有跌倒警報、主動呼救或久待警報。
GOOD = "1 號廁所在 10/14 有人使用 2 次，最久一次待了 8 分鐘；沒有跌倒警報、主動呼救，也沒有久待警報。"


class FakeModel:
    """回固定文字，或丟出指定的例外；記下收到的 prompt。"""

    name = "fake-0.1"

    def __init__(self, reply: str = "", error: Exception | None = None):
        self.reply, self.error, self.prompts = reply, error, []

    def __call__(self, prompt: str) -> str:
        self.prompts.append(prompt)
        if self.error is not None:
            raise self.error
        return self.reply


def test_without_a_model_the_template_is_used():
    result = rewrite(ALL_ONLINE, place=PLACE)
    assert (result.text, result.layer, result.model, result.rejected) == (
        caregiver_text(ALL_ONLINE, place=PLACE), "template", None, ()
    )


def test_a_faithful_rewrite_is_used_and_traced_to_model_and_prompt_version():
    result = rewrite(ALL_ONLINE, place=PLACE, model=FakeModel(GOOD))
    assert (result.text, result.layer, result.model, result.prompt_version, result.rejected) == (
        GOOD, "model", "fake-0.1", PROMPT_VERSION, ()
    )


def test_the_prompt_carries_the_template_and_nothing_else_from_the_data():
    model = FakeModel(GOOD)
    rewrite(ALL_ONLINE, place=PLACE, model=model)
    (prompt,) = model.prompts
    template = caregiver_text(ALL_ONLINE, place=PLACE)
    assert prompt.endswith("原文：\n" + template)


def _fallback(source, reply, place=PLACE):
    result = rewrite(source, place=place, model=FakeModel(reply))
    assert result.layer == "template"
    assert result.text == caregiver_text(source, place=place)
    assert result.model == "fake-0.1"
    return result.rejected


def test_an_invented_number_falls_back_to_the_template():
    assert "數字 3 不在事實裡" in _fallback(ALL_ONLINE, GOOD.replace("使用 2 次", "使用 2 次（其中 3 人）"))


def test_dropping_the_disconnection_caveat_falls_back():
    """模型把斷線那句刪掉，讀者會以為整段時間都沒事（REVIEW.md 3.1）。"""
    records = timeline("09:00:00", "09:10:00", [("09:02:00", "09:03:00")]) + timeline("09:40:00", "09:50:00")
    summary = summarize(records)
    reply = "1 號廁所 10/14 有人使用 1 次，停留 1 分鐘。在線期間沒有跌倒警報、主動呼救或久待警報。"
    rejected = _fallback(summary, reply)
    assert "漏掉「無法判斷」" in rejected
    assert "漏掉範本裡的數字 40" in rejected


def test_leading_zeros_in_times_are_not_counted_as_dropped_numbers():
    records = timeline("09:00:00", "09:10:00", [("09:02:00", "09:03:00")]) + timeline("09:40:00", "09:50:00")
    reply = (
        "1 號廁所 10/14 有人使用 1 次，停留 1 分鐘。在線期間沒有跌倒警報、主動呼救或久待警報。"
        "9:10 到 9:40 裝置斷線，這段時間無法判斷是否有跌倒。"
    )
    assert rewrite(summarize(records), place=PLACE, model=FakeModel(reply)).layer == "model"


def test_medical_wording_falls_back():
    assert "不能出現「治療」（非醫療器材）" in _fallback(ALL_ONLINE, GOOD + "不需要治療。")


def test_overlong_output_falls_back():
    rejected = _fallback(ALL_ONLINE, GOOD * 3)
    assert any(r.startswith("長度") for r in rejected)


def test_model_errors_and_empty_output_fall_back():
    for model, reason in [
        (FakeModel(error=TimeoutError()), "模型錯誤：TimeoutError"),
        (FakeModel("   \n"), "模型沒有輸出"),
    ]:
        result = rewrite(ALL_ONLINE, place=PLACE, model=model)
        assert (result.layer, result.rejected) == ("template", (reason,))


def test_kg_suggestion_must_be_copied_verbatim():
    assessment = kg_rules.assess(kg_rules.load(), "來回行走", "5.4")
    template = caregiver_text(assessment)
    verbatim = "你的來回行走 5.4 秒（2.44 公尺），是「很好」的 4 分。\n" + template.split("\n", 1)[1]
    assert rewrite(assessment, model=FakeModel(verbatim)).layer == "model"

    paraphrased = verbatim.replace("持續規律步行", "繼續規律走路")
    assert "知識圖譜原文（suggestion）沒有照抄" in _fallback(assessment, paraphrased, place="")


def test_unapproved_kg_notice_must_be_kept():
    assessment = kg_rules.assess(kg_rules.load(), "來回行走", "5.45")
    reply = "來回行走 5.45 秒（2.44 公尺）：目前無法評估，已交由審核人確認。"
    assert "漏掉「僅供測試」" in _fallback(assessment, reply, place="")


def test_a_template_that_fails_its_own_check_is_not_handed_to_the_model():
    model = FakeModel(GOOD)
    with pytest.raises(ValueError, match="範本沒通過輸出檢查"):
        rewrite(ALL_ONLINE, place="治療室", model=model)
    assert model.prompts == []


def test_a_changed_duration_is_caught_even_when_the_new_number_is_another_fact():
    """「8 分鐘」改成「9 分鐘」：9 是時刻 09:xx 裡的數字，只比數字會放過；次數與時間長度要整段對得上。"""
    assert "範本沒有「9分」" in _fallback(ALL_ONLINE, GOOD.replace("8 分鐘", "9 分鐘"))


def test_writing_none_as_zero_counts_falls_back():
    """範本寫「沒有跌倒警報」（PR #37），模型改寫成「跌倒警報 0 次」也退回：範本沒有「0 次」，不讓模型自己換算。"""
    reply = "1 號廁所在 10/14 有人使用 2 次，最久一次待了 8 分鐘；跌倒警報 0 次，沒有主動呼救或久待警報。"
    assert "範本沒有「0次」" in _fallback(ALL_ONLINE, reply)


def rule_digest(*parts: object) -> str:
    """prompt 與採用檢查的指紋：字串照原樣，函式取語法樹還原的程式碼（不含註解、docstring 與排版）。

    用 ast.unparse 而不是 ast.dump：ast.dump 的輸出在 3.11、3.12、3.13 之間不同（CI 跑 3.11 與 3.12）。
    output_check 的 check_output、wording_problems 不在指紋裡：報告與提問也共用，改了不代表改寫規則變了。
    """
    texts = []
    for part in parts:
        if callable(part):
            tree = ast.parse(textwrap.dedent(inspect.getsource(part)))
            for node in ast.walk(tree):
                if (isinstance(node, ast.FunctionDef) and node.body and isinstance(node.body[0], ast.Expr)
                        and isinstance(node.body[0].value, ast.Constant) and isinstance(node.body[0].value.value, str)):
                    node.body = node.body[1:] or [ast.Pass()]
            texts.append(ast.unparse(tree))
        else:
            texts.append(repr(part))
    return hashlib.sha256("\n".join(texts).encode("utf-8")).hexdigest()[:16]


def test_prompt_version_is_bumped_when_the_prompt_or_checks_change():
    """紀錄只記 prompt_version；prompt 或採用的檢查改了卻沒升版，就分不出一筆退回是哪一套規則（原則 3，PR #41）。"""
    from radar_agent import rewrite as r

    digest = rule_digest(r.build_prompt("{範本}"), r.rewrite, r.checked_rewrite, r._kept_facts, r._quantities,
                         r._kept_quantities, r._added_words,
                         r._QUANTITY.pattern, r.REQUIRED_PHRASES, r.MAX_LENGTH_RATIO, r.ADDED_WORDS, r._NOT_PRONOUNS)
    assert (PROMPT_VERSION, digest) == ("caregiver-v2", "eeb831052b5ed2ac"), \
        "rewrite.build_prompt 或採用的檢查改了：升 rewrite.PROMPT_VERSION，再更新這裡的版本與雜湊"


@pytest.mark.parametrize("added, reason", [
    # 2026-10-29 PR #43 的比對畫面裡，Gemma 4 被採用的改寫實際寫過這些（#35 也回報過）
    ("我們有語音問一下。", "範本沒有「我們」"),
    ("偵測到他自己起來了。", "範本沒有「他」"),
    ("她沒事。", "範本沒有「她」"),
    ("設備一直開著。", "範本沒有「設備」"),
    ("早上一切正常。", "範本沒有「早上」"),
    ("他們都沒事。", "範本沒有「他」"),
    ("傍晚一切正常。", "範本沒有「傍晚」"),
])
def test_words_the_template_never_uses_fall_back(added, reason):
    assert reason in _fallback(ALL_ONLINE, GOOD + added)


def test_qi_ta_is_not_a_pronoun():
    """「其他」不算「他」：沒有其他警報這種寫法照樣採用。"""
    reply = GOOD.replace("也沒有久待警報。", "也沒有久待或其他警報。")
    assert rewrite(ALL_ONLINE, place=PLACE, model=FakeModel(reply)).layer == "model"
