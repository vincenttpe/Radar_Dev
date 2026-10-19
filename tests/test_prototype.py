"""Demo 原型頁 demo/prototype.html（docs/11「Demo 原型頁」）。

頁面上的句子必須就是 radar-caregiver 的輸出：用語、模擬資料或頁面範本一改，沒重新產生就會失敗。
"""
import importlib.util
import json
import re

import pytest

from radar_agent.caregiver import main


@pytest.fixture(scope="module")
def build(repo):
    spec = importlib.util.spec_from_file_location("demo_build", repo / "demo" / "build.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def data(build):
    return build.demo_data()


def test_committed_page_is_exactly_what_build_py_produces(repo, build, data):
    committed = (repo / "demo" / "prototype.html").read_text(encoding="utf-8")
    assert committed == build.page(data), "改了 template.html、用語或模擬資料要重跑 python demo/build.py"


def test_page_text_is_the_radar_caregiver_output(repo, data, capsys):
    assert main([str(repo / "data" / "samples_demo" / "bathroom_morning.jsonl"), "--place", "1 號廁所"]) == 0
    out = capsys.readouterr().out.splitlines()
    prompts = out[out.index("【處置提示】") + 1:out.index("【照護紀錄草稿】") - 1]
    records = out[out.index("【照護紀錄草稿】") + 1:]
    assert data["summary"] == out[1]
    assert [f"{e['label']} {e['prompt']}" for e in data["events"]] == prompts
    assert [e["record"] for e in data["events"]] == records


def test_trend_on_page_is_the_two_week_trend_output(data):
    lines = data["trend"]["lines"]
    assert lines[1] == "每日使用次數：平均每日 7.8 次，前一週 6.9 次，差不多。"
    assert lines[2] == "凌晨（00:00–06:00）使用次數：平均每日 2.0 次，前一週 0.9 次，增加。"
    by_day = {d["day"]: d for d in data["trend"]["days"]}
    assert by_day["10/10"]["visits"] is None, "在線不足的日子不能畫成 0 次"
    assert len(by_day) == 14


def test_page_always_says_it_is_simulated_and_not_llm(repo):
    page = (repo / "demo" / "prototype.html").read_text(encoding="utf-8")
    banner = re.search(r'<div class="sim">(.*?)</div></div>', page, re.S).group(1)
    assert "模擬資料" in banner and "DEMO-SIM-01" in banner and "尚未接上在地模型" in banner


def test_unknown_prompt_wording_stops_the_build(build, monkeypatch):
    monkeypatch.delitem(build.KINDS, "請立即到場確認。")
    with pytest.raises(ValueError, match="KINDS"):
        build.demo_data()


def test_embedded_data_cannot_close_the_script_tag(build, data):
    html = build.fragment({**data, "summary": "</script><b>"})
    payload = html.split("const DATA = ", 1)[1].split(";\n", 1)[0]
    assert "</script>" not in payload and json.loads(payload)["summary"] == "</script><b>"


def test_field_data_page_is_labelled_as_field_data_and_has_no_trend(build, tmp_path):
    from radar_agent.field_data import mask

    records = [json.loads(line) for line in
               (build.REPO / "data" / "samples_demo" / "bathroom_morning.jsonl").read_text(encoding="utf-8").splitlines()]
    masked, _ = mask(records)
    src = tmp_path / "FIELD-01.jsonl"
    src.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in masked["FIELD-01"]), encoding="utf-8")
    data = build.demo_data(src, "2 號廁所")
    assert data["source"] == "field" and data["trend"] is None
    assert data["summary"].startswith("2 號廁所 10/14：")
    assert len(data["events"]) == 8
    assert [q["id"] for q in data["ask"]] == ["falls", "calls", "longstay", "visits", "offline", "alerts"]
    assert all(q["answer"].startswith("2 號廁所 10/14 ") for q in data["ask"])


def test_field_data_page_refuses_unmasked_or_empty_input(build, tmp_path):
    raw = tmp_path / "raw.jsonl"
    raw.write_text(json.dumps({"timestamp": "2026-10-19 09:00:00", "topic": "radar10/02AB34CD56EF",
                               "resolution": None, "payload": {"event": "absence"}}) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="像 MAC"):
        build.demo_data(raw)
    empty = tmp_path / "empty.jsonl"
    empty.write_text("\n", encoding="utf-8")
    with pytest.raises(ValueError, match="沒有讀得懂的紀錄"):
        build.demo_data(empty)


def test_field_data_page_without_events_has_only_the_summary(build, tmp_path):
    quiet = tmp_path / "quiet.jsonl"
    quiet.write_text("".join(
        json.dumps({"timestamp": f"2026-10-19 09:00:{s:02d}", "topic": "radar10/FIELD-01", "resolution": None,
                    "payload": {"event": "absence"}}) + "\n" for s in (0, 5, 10)), encoding="utf-8")
    data = build.demo_data(quiet)
    assert data["events"] == [] and "沒有偵測到有人使用" in data["summary"]


def test_gap_mode_is_hidden_by_default_and_every_badge_has_a_row(repo):
    """Gap 一覽是內部用的：預設隱藏（只有網址加 #gaps 才顯示），畫面上每個編號都對得到表裡的一列。"""
    page = (repo / "demo" / "prototype.html").read_text(encoding="utf-8")
    assert '<section aria-labelledby="h-gaps" id="gaps" hidden>' in page
    assert 'location.hash === "#gaps"' in page
    badges = set(re.findall(r'class="gap-badge" data-gap="(\d+)"', page))
    rows = set(re.findall(r'<tr id="gap-row-(\d+)">', page))
    assert badges == rows == {str(n) for n in range(1, 10)}


@pytest.mark.parametrize("qid", ["falls", "calls", "longstay", "visits", "offline", "alerts"])
def test_ask_answers_on_page_are_the_radar_ask_pick_output(repo, data, capsys, qid):
    from radar_agent.event_ask import main as ask_main

    assert ask_main([str(repo / "data" / "samples_demo" / "bathroom_morning.jsonl"), "--place", "1 號廁所",
                     "--pick", qid, "--json"]) == 0
    reply = json.loads(capsys.readouterr().out)
    on_page = {q["id"]: q for q in data["ask"]}
    assert on_page[qid]["answer"] == reply["answer"] and on_page[qid]["text"] == reply["question"]["text"]


def test_ask_box_says_there_is_no_local_model_and_keeps_no_typed_text(repo, data):
    page = (repo / "demo" / "prototype.html").read_text(encoding="utf-8")
    assert [q["id"] for q in data["ask"]] == ["falls", "calls", "longstay", "visits", "offline", "alerts"]
    assert "這一頁沒有接上在地模型" in page and 'maxlength="100"' in page
    assert '$("ask-input").value = "";' in page, "打字送出後要清掉原文，不保留"


def test_ask_answer_that_fails_the_output_check_stops_the_build(build, monkeypatch):
    from radar_agent.event_ask import Reply
    from radar_agent.ask import Match

    monkeypatch.setattr(build, "pick", lambda qid, summary, place: Reply(Match(None), None, 0.0, ("數字對不上",)))
    with pytest.raises(ValueError, match="事件提問的答案沒通過輸出檢查"):
        build.demo_data()
