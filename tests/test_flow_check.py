"""流程確認頁 demo/flow_check.html：③ 必須就是 radar-caregiver --json --from --to 的輸出，④ 照原文顯示。

轉換程式或模擬資料一改，沒重新產生就會失敗；資訊不夠確定的地方要列成「待決」，不能悄悄消失。
"""
import html
import importlib.util
import json
import re
import subprocess
import sys

import pytest

from radar_agent.caregiver import main as caregiver_main
from radar_agent.field_data import MAC_LIKE
from radar_agent.output_check import wording_problems


@pytest.fixture(scope="module")
def flow(repo):
    spec = importlib.util.spec_from_file_location("flow_build", repo / "demo" / "flow_build.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def scenarios(flow):
    return flow.build()


def test_committed_page_is_exactly_what_flow_build_produces(repo, flow, scenarios):
    committed = (repo / "demo" / "flow_check.html").read_text(encoding="utf-8")
    assert committed == flow.page(scenarios), "改了 caregiver.py、time_range.py 或模擬資料要重跑 python demo/flow_build.py"


def test_returned_json_is_the_cli_output_verbatim(repo, flow, scenarios, capsys):
    page = flow.page(scenarios)
    shown = [html.unescape(m) for m in re.findall(r'<pre tabindex="0" aria-label="回傳的 JSON" class="json">(.*?)</pre>', page, re.S)]
    assert len(shown) == len(flow.SCENARIOS)
    for (_, start, end), text in zip(flow.SCENARIOS, shown, strict=True):
        assert caregiver_main([str(flow.SAMPLE), "--place", flow.SAMPLE_PLACE, "--json", "--from", start, "--to", end]) == 0
        assert capsys.readouterr().out.rstrip() == text


def test_display_quotes_the_summary_and_every_prompt(flow, scenarios):
    for s in scenarios:
        part = flow.render(s, 1)
        assert html.escape(s["report"]["summary"]) in part
        assert all(html.escape(e["prompt"]) in part for e in s["report"]["events"])


def test_uncertain_spots_are_listed_as_pending(scenarios):
    by_name = {s["name"]: s for s in scenarios}
    assert by_name["一段上午"]["pending"] == []
    assert any("例如 null" in n for n in by_name["跨過斷線"]["pending"])
    longer = by_name["要的範圍比資料長"]["pending"]
    assert any("資料只到 11:59:55" in n and "data_until" in n for n in longer)
    empty = by_name["範圍內沒有資料"]
    assert empty["side"]["count"] == 0 and empty["report"]["summary"].endswith("沒有收到資料，無法判斷。")
    assert any("例如 null" in n for n in empty["pending"])


def test_records_the_converter_cannot_read_are_called_out(flow):
    """② 有紀錄、③ 卻是「沒有收到資料」：格式讀不懂，不能讓居服員以為裝置沒送資料。"""
    report = {"from": None, "summary": "1 號廁所：沒有收到資料，無法判斷。", "counts": {"fall_alerts": 0}}
    side = {"count": 561, "first": None, "last": None}
    assert any("561 筆" in n and "讀不懂" in n for n in flow.pending(report, side, "2026-10-22 20:00"))


def test_page_is_marked_simulated_and_passes_the_wording_check(flow, scenarios):
    page = flow.page(scenarios)
    assert "模擬資料：非真實裝置紀錄" in page and "API 是草案" in page
    assert not MAC_LIKE.search(page)
    text = re.sub(r"<[^>]+>", " ", re.sub(r"<(style|pre)[^>]*>.*?</\1>", " ", page, flags=re.S))
    assert not wording_problems(text)


def test_field_version_refuses_unmasked_data(repo, tmp_path):
    raw = tmp_path / "raw.jsonl"
    raw.write_text(json.dumps({"timestamp": "2026-10-22 07:00:00", "topic": "radar10/AA:BB:CC:DD:EE:FF",
                               "resolution": None, "payload": {"event": "absence"}}) + "\n", encoding="utf-8")
    done = subprocess.run([sys.executable, str(repo / "demo" / "flow_build.py"), "--events", str(raw),
                           "--range", "2026-10-22 07:00", "2026-10-22 08:00"], capture_output=True, text=True, check=False)
    assert done.returncode != 0 and "請先用 radar-field-data mask 遮罩" in done.stderr
