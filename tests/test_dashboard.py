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
    assert [e["record"] for e in cg["events"]] == out[out.index("【照護紀錄草稿】") + 1:]
    assert ask_main([events, "--place", "1 號廁所", "--pick", "falls", "--json"]) == 0
    assert next(q for q in cg["ask"] if q["id"] == "falls")["answer"] == json.loads(capsys.readouterr().out)["answer"]


def test_family_view_has_no_caregiver_prompts_records_or_site_trend_sentences(data):
    family = data["family"]
    assert family["summary"].startswith("DEMO-ELDER-01 房間的浴廁 10/14：")
    assert all(set(e) == {"at", "label", "level", "title"} for e in family["events"])
    text = json.dumps(family, ensure_ascii=False)
    assert "請立即到場" not in text and "場域" not in text and "lines" not in family
    assert len(family["days"]) == 14


def test_admin_view_shows_device_status_without_care_events(data):
    devices = {d["device"]: d for d in data["devices"]}
    assert {k: d["status"] for k, d in devices.items()} == {
        "DEMO-SIM-01": "recovered", "DEMO-SIM-02": "online", "DEMO-SIM-03": "offline"}
    assert devices["DEMO-SIM-01"]["offline"] == [["2026-10-14 11:10:00", "2026-10-14 11:25:00"]]
    assert devices["DEMO-SIM-03"]["ended_offline"] == "2026-10-14 11:40:05"
    assert devices["DEMO-SIM-03"]["problems"] == ["另有 1 筆紀錄無法判讀，請通知資訊人員。"]
    text = json.dumps(data["devices"], ensure_ascii=False)
    assert "跌倒" not in text and "呼救" not in text


def test_page_is_labelled_simulated_and_not_a_replacement(repo):
    page = (repo / "demo" / "dashboard.html").read_text(encoding="utf-8")
    assert "模擬資料" in page and "尚未接上在地模型" in page and "不是要取代官網" in page
    assert "依讀者權限家屬不能看" in page


def test_embedded_data_cannot_close_the_script_tag(build, data):
    html = build.fragment({**data, "x": "</script><b>"})
    payload = html.split("const DATA = ", 1)[1].split(";\n", 1)[0]
    assert "</script>" not in payload and json.loads(payload)["x"] == "</script><b>"
