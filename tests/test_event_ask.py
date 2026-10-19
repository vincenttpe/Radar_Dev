"""事件提問（radar-ask）：模型只選題號，答案由事件事實套固定句型，答案要通過輸出檢查。

事件用 Demo 模擬資料（data/samples_demo/bathroom_morning.jsonl）與 model_eval 的模擬題組，不含真實資料。
答案句型是 2026-10-19 的草稿；用語一改這裡就會失敗。
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
        "1 號廁所 10/14：在線期間跌倒警報 2 次（08:32、09:16）。\n"
        "語音回覆沒事 1 次。\n"
        "11:10–11:25 裝置斷線，這段時間無法判斷是否有跌倒。"
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
    assert data["answer"].startswith("1 號廁所 10/14：在線期間久待警報 1 次（10:45）。")
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
    assert out == "您是想問「用了幾次？最久待多久？」嗎？\n1 號廁所 10/14：有人使用 5 次，最長一次 22 分鐘。\n11:10–11:25 裝置斷線，這段時間無法判斷使用情形。\n"


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
