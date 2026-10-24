"""事件提問（radar-ask）：模型只選題號，答案由事件事實套固定句型，答案要通過輸出檢查。

事件用 Demo 模擬資料（data/samples_demo/bathroom_morning.jsonl）與 model_eval 的模擬題組，不含真實資料。
答案句型於 2026-10-19 確認；用語一改這裡就會失敗。
"""
import json
from pathlib import Path

import pytest

from radar_agent.device_events import read_records, summarize
from radar_agent.event_ask import EVENT_QUESTIONS, answer, ask_events, main, pick
from radar_agent.model_eval import EVENT_ASK_CASES, PLACE, event_cases
from radar_agent.output_check import check_output

DEMO = Path(__file__).resolve().parents[1] / "data" / "samples_demo" / "bathroom_morning.jsonl"
SUMMARY = summarize(read_records(DEMO))
CASES = {c.id: c.source for c in event_cases()}


class Scripted:
    def __init__(self, reply: str, name: str = "scripted"):
        self.reply, self.name = reply, name
        self.prompts: list[str] = []

    def __call__(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return self.reply


def test_falls_answer_states_disconnection_instead_of_claiming_none():
    assert answer("falls", SUMMARY, place="1 號廁所") == (
        "1 號廁所 10/14 08:00–11:59：在線期間跌倒警報 2 次（08:32、09:16）。\n"
        "語音回覆沒事 1 次。\n"
        "11:10–11:25 裝置斷線，這段時間無法判斷是否有跌倒。\n"
        "在線期間另有主動呼救 1 次（10:03）。"
    )


def test_alerts_answer_lists_the_same_prompts_as_the_report():
    text = answer("alerts", SUMMARY, place="1 號廁所")
    assert text.splitlines()[1:3] == ["08:32 跌倒警報：請立即到場確認。", "08:32 長者回覆沒事，警報已解除；可視情況前往關心。"]
    assert text.endswith("11:10–11:25 裝置斷線：這段時間無法判斷是否有跌倒，請通知資訊人員。")


def test_no_data_is_never_answered_as_nothing_happened():
    for q in EVENT_QUESTIONS:
        assert answer(q.id, CASES["E11"], place=PLACE) == f"{PLACE}：沒有收到資料，無法判斷。"


@pytest.mark.parametrize("case", sorted(CASES))
def test_every_answer_passes_the_output_check(case):
    for q in EVENT_QUESTIONS:
        text = answer(q.id, CASES[case], place=PLACE)
        assert check_output(text, CASES[case], place=PLACE) == [], (case, q.id, text)


def test_quiet_period_says_so():
    assert answer("alerts", CASES["E1"], place=PLACE).endswith("這段時間沒有警報。")
    assert answer("offline", CASES["E1"], place=PLACE).endswith("這段時間裝置都在線。")


def test_model_picks_a_number_and_the_answer_comes_from_the_facts():
    model = Scripted("3")
    reply = ask_events("有人待很久嗎", SUMMARY, place="1 號廁所", model=model)
    data = reply.data()
    assert data["question"]["id"] == "longstay" and data["confirm"] == "您是想問「有沒有人待太久？」嗎？"
    assert data["answer"].startswith("1 號廁所 10/14 08:00–11:59：在線期間久待警報 1 次（10:45）。")
    assert (data["model"], data["prompt_version"], data["rejected"]) == ("scripted", "ask-v0", [])
    assert "有人待很久嗎" not in json.dumps(data, ensure_ascii=False), "不保存讀者打的原文"


def test_model_writing_its_own_answer_is_not_used():
    reply = ask_events("有人跌倒嗎", SUMMARY, place="1 號廁所", model=Scripted("沒有人跌倒"))
    assert (reply.answer, reply.data()["question"]) == (None, None)
    assert reply.data()["rejected"] == ["模型回答不是單一編號"]
    assert len(reply.data()["menu"]) == len(EVENT_QUESTIONS)


def test_answer_failing_the_check_is_withheld(monkeypatch):
    from radar_agent import event_ask

    monkeypatch.setattr(event_ask, "check_output", lambda *a, **k: ["假的問題"])
    reply = pick("falls", SUMMARY, place="1 號廁所")
    assert reply.answer is None and reply.data()["rejected"] == ["假的問題"]


def test_eval_cases_cover_every_question_and_off_topic_ones():
    ids = {q.id for q in EVENT_QUESTIONS}
    assert {c.expected for c in EVENT_ASK_CASES} == ids | {None}


def _run(capsys, *argv):
    code = main([str(a) for a in argv])
    out, err = capsys.readouterr()
    return code, out, err


def test_cli_pick_skips_the_model(capsys):
    code, out, _ = _run(capsys, DEMO, "--place", "1 號廁所", "--pick", "visits")
    assert code == 0
    assert out == "您是想問「用了幾次？最久待多久？」嗎？\n1 號廁所 10/14 08:00–11:59：有人使用 5 次，最長一次 22 分鐘。\n11:10–11:25 裝置斷線，這段時間無法判斷使用情形。\n"


def test_cli_without_a_model_shows_the_menu(capsys):
    code, out, _ = _run(capsys, DEMO, "--place", "1 號廁所", "--question", "有人跌倒嗎")
    assert code == 0 and out.startswith("沒有對應到固定問題（沒有在地模型）。請從選單選一題：\n1. 有沒有人跌倒？")


def test_cli_with_a_command_model_and_json(capsys):
    code, out, _ = _run(capsys, DEMO, "--place", "1 號廁所", "--question", "有人跌倒嗎", "--cmd", "echo 1", "--json")
    data = json.loads(out)
    assert code == 0 and data["question"]["id"] == "falls" and data["model"] == "echo 1"


def test_cli_refuses_models_from_china(capsys):
    code, _, err = _run(capsys, DEMO, "--place", "1 號廁所", "--question", "x", "--cmd", "ollama run qwen2.5:3b")
    assert code == 2 and "docs/adr/0003" in err


# ── 2026-10-19 審查找到的情況：斷線與讀不懂的紀錄不能被答成沒事 ──────────

T = "radar10/DEVICE01"


def _rec(hms: str, event: str) -> dict:
    payload = {"mode": "radar", "event": event, "trigger": "track", "userReply": False}
    return {"timestamp": f"2026-10-14 {hms}", "topic": T, "resolution": None, "payload": payload}


# 有人進去後裝置斷線，之後沒再連上：只有一段在線時段，但其實有斷線
ENDED_OFFLINE = summarize(
    [_rec(f"09:00:{s:02d}", "absence") for s in range(0, 30, 5)]
    + [_rec("09:00:30", "presence"), _rec("09:00:35", "moving"), _rec("09:00:40", "offline")]
)
# 有讀不懂的紀錄（時間格式錯）
UNREADABLE = summarize(
    [_rec(f"09:00:{s:02d}", "absence") for s in range(0, 60, 5)]
    + [{"timestamp": "不是時間", "topic": T, "resolution": None, "payload": {"event": "absence"}}]
)


@pytest.mark.parametrize("qid", [q.id for q in EVENT_QUESTIONS])
def test_device_going_offline_before_the_data_ends_is_never_answered_as_nothing(qid):
    text = answer(qid, ENDED_OFFLINE, place=PLACE)
    assert "都在線" not in text and "這段時間沒有警報" not in text
    assert "09:00 起裝置斷線" in text
    assert check_output(text, ENDED_OFFLINE, place=PLACE) == []


def test_offline_before_the_end_scopes_counts_to_online_time():
    assert "在線期間久待警報 0 次" in answer("longstay", ENDED_OFFLINE, place=PLACE)
    assert answer("alerts", ENDED_OFFLINE, place=PLACE).startswith(f"{PLACE} 10/14 09:00：在線期間沒有警報。")


@pytest.mark.parametrize("qid", [q.id for q in EVENT_QUESTIONS])
def test_unreadable_records_are_mentioned_in_every_answer(qid):
    assert answer(qid, UNREADABLE, place=PLACE).endswith("另有 1 筆紀錄無法判讀，請通知資訊人員。")


def test_check_rejects_zero_longstay_or_all_online_when_the_device_went_offline():
    assert check_output("久待警報 0 次。", ENDED_OFFLINE, place=PLACE) == [
        "裝置有斷線，不能寫「久待警報 0 次」而不註明在線期間"
    ]
    assert check_output("這段時間裝置都在線。", ENDED_OFFLINE, place=PLACE) == ["裝置有斷線，不能寫「裝置都在線」"]


def test_falls_answer_mentions_distress_calls_without_counting_them_as_falls():
    text = answer("falls", CASES["E4"], place=PLACE)
    assert "跌倒警報 0 次" in text and "另有主動呼救 1 次（09:06）" in text


def test_reply_carries_the_period_it_covers():
    data = pick("falls", SUMMARY, place="1 號廁所").data()
    assert (data["from"], data["to"]) == ("2026-10-14 08:00:00", "2026-10-14 11:59:55")


def test_cli_reports_an_unknown_prompt_instead_of_crashing(monkeypatch, capsys):
    from radar_agent import caregiver

    monkeypatch.delitem(caregiver.NOTE_KINDS, "請立即到場確認。")
    code, _, err = _run(capsys, DEMO, "--place", "1 號廁所", "--pick", "alerts")
    assert code == 2 and "NOTE_KINDS" in err


# 斷線時沒人在用、最後一筆是 offline；以及中間斷過一次、最後又在使用途中斷線
QUIET_OFFLINE = summarize([*(_rec(f"09:00:{s:02d}", "absence") for s in range(0, 40, 5)), _rec("09:00:40", "offline")])
GAP_THEN_OFFLINE = summarize(
    [*(_rec(f"09:00:{s:02d}", "absence") for s in range(0, 30, 5)), _rec("09:00:30", "offline"),
     *(_rec(f"09:10:{s:02d}", "absence") for s in range(0, 30, 5)),
     _rec("09:10:30", "presence"), _rec("09:10:35", "moving"), _rec("09:10:40", "offline")]
)


@pytest.mark.parametrize("summary", [QUIET_OFFLINE, GAP_THEN_OFFLINE], ids=["quiet", "gap-then-offline"])
@pytest.mark.parametrize("qid", [q.id for q in EVENT_QUESTIONS])
def test_offline_at_the_end_is_always_stated(summary, qid):
    text = answer(qid, summary, place=PLACE)
    assert "09:00 起裝置斷線" in text or "09:10 起裝置斷線" in text, text
    assert "都在線" not in text and "這段時間沒有警報" not in text
    assert check_output(text, summary, place=PLACE) == []


def test_gap_and_end_offline_are_both_listed():
    text = answer("offline", GAP_THEN_OFFLINE, place=PLACE)
    assert "09:00–09:10 裝置斷線" in text and "09:10 起裝置斷線，之後無法判斷是否有跌倒。" in text


def test_repeated_offline_at_the_end_starts_at_the_first_one():
    """結尾連續兩筆 offline：斷線起點是第一筆，不是最後一筆（2026-10-19 第二輪審查）。"""
    summary = summarize([*(_rec(f"08:00:{s:02d}", "absence") for s in range(0, 10, 5)),
                         _rec("08:00:10", "offline"), _rec("08:05:00", "offline")])
    assert summary.ended_offline is not None and f"{summary.ended_offline:%H:%M:%S}" == "08:00:10"
    for q in EVENT_QUESTIONS:
        text = answer(q.id, summary, place=PLACE)
        assert "08:00 起裝置斷線" in text and "08:05" not in text
        assert check_output(text, summary, place=PLACE) == []


def test_data_after_offline_clears_the_end_offline_mark():
    summary = summarize([_rec("08:00:00", "absence"), _rec("08:00:05", "offline"), _rec("08:10:00", "absence")])
    assert summary.ended_offline is None


def test_answer_head_names_the_period_and_crosses_midnight():
    two_days = summarize([_rec("23:58:00", "absence"),
                          {"timestamp": "2026-10-15 00:00:05", "topic": T, "resolution": None,
                           "payload": {"mode": "radar", "event": "absence", "trigger": "track", "userReply": False}}])
    text = answer("visits", two_days, place=PLACE)
    assert text.startswith(f"{PLACE} 10/14 23:58–10/15 00:00：")
    assert check_output(text, two_days, place=PLACE) == []


def test_answer_head_writes_one_time_when_start_and_end_share_a_minute():
    """資料頭尾在同一分鐘時只寫一個時間，不寫「09:00–09:00」（#66）。"""
    for summary in (ENDED_OFFLINE, summarize([_rec("09:00:00", "absence")])):
        text = answer("visits", summary, place=PLACE)
        assert text.startswith(f"{PLACE} 10/14 09:00：") and "09:00–09:00" not in text
        assert check_output(text, summary, place=PLACE) == []


def test_every_question_is_a_summary_topic():
    """題號就是 caregiver 的事件摘要主題：多一題或改題號，兩邊要一起改。"""
    from radar_agent.caregiver import SUMMARY_TOPICS
    from radar_agent.event_ask import EVENT_QUESTIONS

    assert [q.id for q in EVENT_QUESTIONS] == list(SUMMARY_TOPICS)
