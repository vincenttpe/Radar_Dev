"""三種讀者的 Dashboard 試做頁 demo/dashboard.html（docs/17_介面易用性評估.md）。

居服員畫面的句子必須就是 radar-caregiver、radar-ask 的輸出；家屬畫面不能出現給居服員的處置提示與照護紀錄；
系統管理人員只看裝置狀態。用語、模擬資料或頁面範本一改，沒重新產生就會失敗。
"""
import importlib.util
import json

import pytest

from radar_agent.caregiver import main as caregiver_main
from radar_agent.event_ask import main as ask_main


@pytest.fixture(scope="module")
def build(repo):
    spec = importlib.util.spec_from_file_location("dashboard_build", repo / "demo" / "dashboard_build.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def data(build):
    return build.dashboard_data()


def test_committed_page_is_exactly_what_dashboard_build_produces(repo, build, data):
    committed = (repo / "demo" / "dashboard.html").read_text(encoding="utf-8")
    assert committed == build.page(data), "改了 dashboard_template.html、用語或模擬資料要重跑 python demo/dashboard_build.py"


def test_caregiver_view_uses_the_radar_caregiver_and_radar_ask_output(repo, data, capsys):
    events = str(repo / "data" / "samples_demo" / "bathroom_morning.jsonl")
    assert caregiver_main([events, "--place", "1 號廁所"]) == 0
    out = capsys.readouterr().out.splitlines()
    cg = data["caregiver"]
    assert cg["summary"] == out[1]
    prompts = out[out.index("【處置提示】") + 1:out.index("【照護紀錄草稿】") - 1]
    assert [f"{e['label']} {e['prompt']}" for e in cg["events"]] == prompts
    assert [e["record"] for e in cg["events"]] == out[out.index("【照護紀錄草稿】") + 1:]
    for q in cg["ask"]:
        assert ask_main([events, "--place", "1 號廁所", "--pick", q["id"], "--json"]) == 0
        assert q["answer"] == json.loads(capsys.readouterr().out)["answer"], q["id"]
    assert cg["offline_sentences"] == ["11:10–11:25 裝置斷線，這段時間無法判斷是否有跌倒。"]


def test_incidents_keep_device_state_and_human_state_apart(data):
    """裝置解除不等於有人處理：久待警報沒有解除紀錄；這份資料沒有人員處置，一律是尚未確認。"""
    cards = data["caregiver"]["incidents"]
    assert [(c["label"], c["title"], c["cleared"] and c["cleared"]["label"]) for c in cards] == [
        ("08:32", "跌倒警報", "08:32"), ("09:16", "跌倒警報", "09:19"),
        ("10:03", "主動呼救", "10:05"), ("10:45", "久待警報", None)]
    assert [c["id"] for c in cards] == ["e1", "e2", "e3", "e4"]
    assert cards[1]["cleared"]["cause"] == "偵測到起身" and data["caregiver"]["orphans"] == []


def test_incidents_never_pair_a_resolution_with_a_longstay(build):
    events = [{"at": "a", "label": "10:00", "level": "check", "title": "久待警報", "prompt": "p"},
              {"at": "b", "label": "10:01", "level": "resolved", "title": "起身解除", "prompt": "q"}]
    cards, orphans = build.incidents(events)
    assert cards[0]["cleared"] is None
    assert orphans == [{"label": "10:01", "title": "起身解除"}], "配不到警報的解除不能丟掉"


def test_family_view_is_simulated_and_has_no_caregiver_prompts_or_records(data):
    family = data["family"]
    assert family["source"] == "sim"
    assert family["summary"].startswith("DEMO-ELDER-01 房間的浴廁 10/14：")
    assert all(set(c) == {"id", "at", "label", "level", "title", "cleared"} for c in family["incidents"])
    assert [c["id"] for c in family["incidents"]] == [c["id"] for c in data["caregiver"]["incidents"]], "處置狀態要能同步"
    text = json.dumps(family, ensure_ascii=False)
    assert "請立即到場" not in text and "請前往查看" not in text and "場域" not in text
    assert len(family["days"]) == 14


def test_family_view_only_reads_the_simulated_morning(build, monkeypatch):
    """家屬畫面不能被拿去跑實測資料：dashboard_data 不接受資料路徑，demo_data 也只用預設的模擬資料。"""
    import inspect

    assert not inspect.signature(build.dashboard_data).parameters
    seen = []
    real = build._load_build

    def spy():
        module = real()
        original = module.demo_data
        module.demo_data = lambda events=None, place=module.PLACE: seen.append(events) or original(events, place)
        return module

    monkeypatch.setattr(build, "_load_build", spy)
    build.dashboard_data()
    assert seen and all(e is None for e in seen)


def test_admin_view_shows_device_status_without_care_events(data):
    devices = {d["device"]: d for d in data["devices"]}
    assert {k: d["status"] for k, d in devices.items()} == {
        "DEMO-SIM-01": "recovered", "DEMO-SIM-02": "online", "DEMO-SIM-03": "offline", "DEMO-SIM-04": "silent"}
    assert devices["DEMO-SIM-01"]["offline"] == [["2026-10-14 11:10:00", "2026-10-14 11:25:00"]]
    assert devices["DEMO-SIM-03"]["since"] == "2026-10-14 11:40:05"
    assert devices["DEMO-SIM-03"]["unreadable"] == 1
    assert (devices["DEMO-SIM-04"]["since"], devices["DEMO-SIM-04"]["minutes_without_data"]) == ("2026-10-14 10:30:00", 89)
    text = json.dumps(data["devices"], ensure_ascii=False)
    assert "跌倒" not in text and "呼救" not in text and "請通知" not in text


def test_device_that_stops_sending_without_offline_is_not_online(build):
    """只看自己的紀錄會以為一直在線；要和其他裝置最晚收到資料的時間比。"""
    quiet = build._heartbeats("radar10/X-1", "08:00:00", "09:00:00")
    busy = build._heartbeats("radar10/X-2", "08:00:00", "11:00:00")
    ref, rows = build.device_rows([("X-1", "甲", quiet), ("X-2", "乙", busy)])
    assert ref == "2026-10-14 11:00:00"
    assert [r["status"] for r in rows] == ["silent", "online"]
    _, rows = build.device_rows([("X-1", "甲", quiet[:-3]), ("X-2", "乙", quiet)])
    assert [r["status"] for r in rows] == ["online", "online"], "落後不到心跳間隔（30 秒）不算沒收到資料"


def test_trend_rule_follows_the_caregiver_constants(data):
    assert data["caregiver"]["trend_rule"].startswith("判定方式：兩週各至少要有 5 天可比較（當天在線 23 小時以上）")
    assert data["caregiver"]["trend"]["lines"][0].startswith("【使用趨勢】1 號廁所 10/8–10/14 與前一週")


def test_page_is_labelled_simulated_and_not_a_replacement(repo):
    page = (repo / "demo" / "dashboard.html").read_text(encoding="utf-8")
    assert "模擬資料" in page and "模擬歷史資料" in page and "不是要取代官網" in page
    assert "家屬不能看" in page and "尚未確認" in page


def test_ui_sentences_come_from_the_checked_table(repo, build, data):
    """頁面固定句型都在 UI_TEXT、通過用語檢查；範本用到的每個 key 都要存在。"""
    import re

    from radar_agent.output_check import wording_problems

    assert all(not wording_problems(t) for t in [*data["text"].values(), *data["results"]])
    template = (repo / "demo" / "dashboard_template.html").read_text(encoding="utf-8")
    used = set(re.findall(r'say\("(\w+)"[,)]', template))
    used |= {f"status_{s}" for s in ("online", "recovered", "offline", "silent")}
    assert used <= set(data["text"]), used - set(data["text"])
    assert data["text"]["device_unknown"] == "未收到解除紀錄，狀態待確認"


def test_ui_text_that_fails_the_wording_check_stops_the_build(build, monkeypatch):
    monkeypatch.setitem(build.UI_TEXT, "head_none", "長者已經沒事，不必診斷")
    with pytest.raises(ValueError, match="用語檢查"):
        build.dashboard_data()


def test_embedded_data_cannot_close_the_script_tag(build, data):
    html = build.fragment({**data, "x": "</script><b>"})
    payload = html.split("const DATA = ", 1)[1].split(";\n", 1)[0]
    assert "</script>" not in payload and json.loads(payload)["x"] == "</script><b>"
