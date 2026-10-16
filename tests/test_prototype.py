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
    payload = html.split("const DATA = ", 1)[1].split(";\nconst sec", 1)[0]
    assert "</script>" not in payload and json.loads(payload)["summary"] == "</script><b>"
