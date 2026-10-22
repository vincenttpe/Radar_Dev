"""四種角色的 Dashboard 試做頁 demo/dashboard.html（docs/17_介面易用性評估.md）。

居服員畫面的句子必須就是 radar-caregiver、radar-ask 的輸出；家屬畫面不能出現給居服員的處置提示與照護紀錄；
系統管理人員只看裝置狀態。用語、模擬資料或頁面範本一改，沒重新產生就會失敗。
"""
import importlib.util
import json
import re

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
    place = data["caregiver"]["place"]   # 從裝置設定紀錄查到的地點
    assert caregiver_main([events, "--place", place]) == 0
    out = capsys.readouterr().out.splitlines()
    cg = data["caregiver"]
    assert cg["summary"] == out[1]
    prompts = out[out.index("【處置提示】") + 1:out.index("【照護紀錄草稿】") - 1]
    assert [f"{e['label']} {e['prompt']}" for e in cg["events"]] == prompts
    assert [e["record"] for e in cg["events"]] == out[out.index("【照護紀錄草稿】") + 1:]
    for q in cg["ask"]:
        assert ask_main([events, "--place", place, "--pick", q["id"], "--json"]) == 0
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
    assert devices["DEMO-SIM-03"]["since"] == "2026-10-14 11:40:35"
    assert devices["DEMO-SIM-03"]["unreadable"] == 1
    assert (devices["DEMO-SIM-04"]["since"], devices["DEMO-SIM-04"]["minutes_without_data"]) == ("2026-10-14 10:30:00", 89)
    text = json.dumps(data["devices"], ensure_ascii=False)
    assert "跌倒" not in text and "呼救" not in text and "請通知" not in text


def test_device_that_stops_sending_without_offline_is_not_online(build):
    """只看自己的紀錄會以為一直在線；要和其他裝置最晚收到資料的時間比。"""
    quiet = build._heartbeats("radar10/X-1", "08:00:00", "09:00:00")
    busy = build._heartbeats("radar10/X-2", "08:00:00", "11:00:00")
    ref, rows = build.device_rows([("X-1", quiet), ("X-2", busy)], {})
    assert ref == "2026-10-14 11:00:00"
    assert [r["status"] for r in rows] == ["silent", "online"]
    _, rows = build.device_rows([("X-1", quiet[:-3]), ("X-2", quiet)], {})
    assert [r["status"] for r in rows] == ["online", "online"], "落後不到斷線判斷門檻（30 秒）不算沒收到資料"


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


# 頁面 JS 裡允許直接寫的中文：只有按鈕、欄位與圖表的標籤，不陳述事實；陳述事實的句子都要在 UI_TEXT
CONTROL_LABELS = {
    "不列入", "人員處置", "備註", "儲存處置", "兩週每日使用次數", "前一週", "取消", "回報問題", "急迫程度", "接手",
    "接手處理", "時段 ", "檢視日期 ", "現場已檢查", "確認接手", "網路", "處置結果", "裝置警報", "複製回報內容",
    "記錄處置", "請選擇", "近 7 天", "送出（示意）", "電源", "狀態", "處理結果",
}


def test_script_writes_no_sentences_outside_the_checked_table(repo):
    import re

    page = (repo / "demo" / "dashboard_template.html").read_text(encoding="utf-8")
    script = page.split("<script>", 1)[1].split("</script>", 1)[0]
    script = re.sub(r"^\s*//.*$", "", script, flags=re.M)   # 註解不算（裡面的引號會讓比對錯位）
    literals = {next(x for x in m if x) for m in re.findall(
        r'"((?:[^"\\\n]|\\.)*)"|`((?:[^`\\]|\\.)*)`|\'((?:[^\'\\\n]|\\.)*)\'', script) if any(m)}
    chinese = {t for t in literals if re.search(r"[一-鿿]", t)}
    assert chinese <= CONTROL_LABELS, f"這些句子要搬進 UI_TEXT 或確認只是標籤：{chinese - CONTROL_LABELS}"


def test_device_report_carries_what_a_remote_engineer_needs_but_no_care_data(data):
    """回報單自動帶入遠端判斷要的裝置資訊；讀不懂的紀錄只留形狀，不帶 payload 內容（有沒有人等照護資訊）。"""
    diag = {d["device"]: d["diag"] for d in data["devices"]}
    third = diag["DEMO-SIM-03"]
    assert third["topic"] == "radar10/DEMO-SIM-03" and third["median_gap_s"] == 5
    # 裝置最後一筆和 broker 的斷線訊息分開：差幾秒可以核對 keepalive
    assert (third["last_device"], third["offline_msg"], third["offline_after_s"]) == (
        "2026-10-14 11:40:00", "2026-10-14 11:40:35", 35)
    assert third["expected_records"] == third["device_records"], "預期筆數算到裝置最後一筆，不含等 keepalive 的時間"
    assert json.loads(third["unreadable_sample"]) == {
        "timestamp": "2026-10-14T09:00", "topic": "radar10/DEMO-SIM-03", "payload 欄位": ["event"]}
    assert diag["DEMO-SIM-01"]["max_gap"] == ["2026-10-14 11:09:55", "2026-10-14 11:25:00", 905]
    assert diag["DEMO-SIM-02"]["max_gap"] is None, "和一般心跳差不多的間隔不列"
    text = json.dumps(diag, ensure_ascii=False)
    assert "absence" not in text and "moving" not in text and "fall" not in text


def test_diagnostics_survive_almost_no_data(build):
    from radar_agent.device_events import summarize

    one = build._heartbeats("radar10/X-1", "08:00:00", "08:00:00")
    diag = build.diagnostics(one, summarize(one))
    assert diag["median_gap_s"] is None and diag["max_gap"] is None and diag["offline_msg"] is None


def test_places_come_from_the_device_registry(build, data):
    """居服員、系統管理人員畫面與回報單上的地點都查裝置設定紀錄；移機後顯示新地點；沒有紀錄的裝置算異常。"""
    registry = build.load_registry()
    assert {d["device"]: d["place"] for d in data["devices"]} == {k: v["place"] for k, v in registry.items()}
    assert data["caregiver"]["place"] == registry["DEMO-SIM-01"]["place"]
    moved = next(d for d in data["devices"] if d["device"] == "DEMO-SIM-03")["registry"]["history"]
    assert moved[-1]["change"].startswith("移機")
    _, rows = build.device_rows([("X-9", build._heartbeats("radar10/X-9", "08:00:00", "08:10:00"))], registry)
    assert rows[0]["place"] == "未登記地點" and rows[0]["registry"] is None


def test_registry_text_goes_through_the_wording_check(build):
    registry = build.load_registry()
    registry["DEMO-SIM-01"] = {**registry["DEMO-SIM-01"], "use": "診斷室"}
    with pytest.raises(ValueError, match="用語檢查"):
        build.check_ui_text(registry)


def test_static_page_text_passes_the_wording_check(repo):
    """<script> 以外的靜態文字（說明、標題、aria-label）也是給讀者看的，一樣不能有醫療用語。"""
    import re
    from html.parser import HTMLParser

    from radar_agent.output_check import wording_problems

    page = (repo / "demo" / "dashboard_template.html").read_text(encoding="utf-8")
    page = re.sub(r"<script>.*?</script>|<style>.*?</style>", "", page, flags=re.S)
    texts: list[str] = []

    class Grab(HTMLParser):
        def handle_data(self, d):
            if d.strip():
                texts.append(d.strip())

        def handle_starttag(self, tag, attrs):
            texts.extend(v for k, v in attrs if k in ("aria-label", "placeholder", "title") and v)

    Grab().feed(page)
    assert texts and all(not wording_problems(t) for t in texts), [t for t in texts if wording_problems(t)]
    assert all(not wording_problems(t) for t in CONTROL_LABELS)


def test_family_never_sees_a_pointer_to_notes_it_cannot_read(data):
    assert all(r in data["results"] for r in data["family_results"])
    assert data["family_results"]["其他（見備註）"] == "其他處置"


@pytest.mark.parametrize(("entry", "message"), [
    ({"device": "X", "place": "甲", "area": "乙", "use": "丙", "history": []}, "缺欄位或是空的"),
    ({"device": "X", "place": "甲", "area": "乙", "history": [{"at": "1", "by": "2", "change": "3"}]}, "缺欄位或是空的"),
    ({"device": "X", "place": "甲", "area": "乙", "use": "丙", "history": [{"at": "1", "change": "3"}]}, "變更紀錄缺欄位"),
    ({"device": "X", "place": "甲", "area": "乙", "use": "丙",
      "history": [{"at": "1", "by": "2", "change": "3"}]}, "地點和變更紀錄對不上"),
    ({"device": "X", "place": "甲", "area": "乙", "use": "丙",
      "history": [{"at": "1", "by": "2", "change": "3", "place": "丁"}]}, "地點和變更紀錄對不上"),
    ({"device": "X", "place": "甲", "area": "乙", "use": "丙",
      "history": [{"at": "2", "by": "2", "change": "3", "place": "甲"},
                  {"at": "1", "by": "2", "change": "4"}]}, "沒有照時間排"),
])
def test_registry_is_checked_when_loaded(build, tmp_path, entry, message):
    path = tmp_path / "registry.json"
    path.write_text(json.dumps({"devices": [entry]}, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(ValueError, match=message):
        build.load_registry(path)


def test_registry_rejects_a_device_registered_twice(build, tmp_path):
    entry = {"device": "X", "place": "甲", "area": "乙", "use": "丙", "history": [{"at": "1", "by": "2", "change": "3", "place": "甲"}]}
    path = tmp_path / "registry.json"
    path.write_text(json.dumps({"devices": [entry, entry]}, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(ValueError, match="重複登記"):
        build.load_registry(path)


def test_care_device_without_registry_stops_the_build(build, monkeypatch):
    """居服員畫面的句子會進照護紀錄，不能寫出「未登記地點雷達偵測到…」。"""
    real = build.load_registry
    monkeypatch.setattr(build, "load_registry", lambda: {k: v for k, v in real().items() if k != build.CARE_DEVICE})
    with pytest.raises(ValueError, match="沒有裝置設定紀錄"):
        build.dashboard_data()


def test_registry_install_dates_come_before_the_two_week_trend(build, data):
    first_day = "2026-10-01"
    install = build.load_registry()[build.CARE_DEVICE]["history"][0]["at"]
    assert install < first_day, "設定紀錄的安裝時間要早於兩週趨勢的第一天"
    assert data["caregiver"]["trend"]["days"][0]["day"] == "10/1"


def test_last_seen_is_the_device_not_the_broker_offline_message(data):
    third = next(d for d in data["devices"] if d["device"] == "DEMO-SIM-03")
    assert third["last_seen"] == third["diag"]["last_device"] == "2026-10-14 11:40:00"
    assert third["diag"]["all_records"] == third["records"] == 2643
    assert (third["diag"]["device_records"], third["unreadable"], third["diag"]["offline_records"]) == (2641, 1, 1)


def test_admin_sees_only_its_own_site_tickets(data):
    assert {t["id"] for t in data["own_tickets"]} == {"TS-20261010-01", "TS-20261012-01"}
    assert all(t["site"] == "SITE-A" for t in data["own_tickets"])


def test_ticket_place_is_where_the_device_was_when_reported(build, data):
    """DEMO-SIM-03 在 10/12 移機：10/10 的回報單要顯示當時的 5 號廁所，不是現在的 3 號廁所。"""
    places = {t["id"]: t["place"] for t in data["own_tickets"]}
    assert places == {"TS-20261010-01": "5 號廁所", "TS-20261012-01": "4 號廁所"}
    registry = build.load_registry()
    assert build.place_at("DEMO-SIM-03", "2026-10-12 16:10", registry) == "3 號廁所"
    assert build.place_at("DEMO-SIM-03", "2026-09-01 00:00", registry) == "未登記地點"
    assert build.place_at("X-9", "2026-10-12 16:10", registry) == "未登記地點"


def test_registry_history_place_goes_through_the_wording_check(build):
    registry = build.load_registry()
    entry = registry["DEMO-SIM-03"]
    registry["DEMO-SIM-03"] = {**entry, "history": [{**entry["history"][0], "place": "診斷室"}, *entry["history"][1:]]}
    with pytest.raises(ValueError, match=r"history\.place"):
        build.check_ui_text(registry)


def test_ticket_text_goes_through_the_wording_check(build):
    _, tickets = build.load_maintenance()
    tickets[0] = {**tickets[0], "action": "診斷後給藥"}
    with pytest.raises(ValueError, match="沒通過用語檢查"):
        build.check_ui_text(None, tickets)


def test_ops_findings_follow_the_rules(build, data):
    findings = data["ops"]["findings"]
    assert findings[0].startswith("韌體 1.2 的「資料逾時」回報 7 件（每台 1.17 件）")
    assert any(f.startswith("硬體批次 B1 更換電源供應器 4 次") for f in findings)
    assert any(f.startswith("「無法判讀」2 件都以「重新設定時間同步」處理") for f in findings)
    # 斷線在 1.2 剛好是其他版本的 2 倍：不列（那 3 件其實是 B1 的電源問題）
    assert not any("「斷線」" in f and f.startswith("韌體") for f in findings)
    by_fw = {r["fw"]: r for r in data["ops"]["by_fw"]}
    assert by_fw["1.2"]["devices"] == 6, "用過 1.2 的裝置包含後來更新到 1.3 的兩台"
    # 參考清單只放給韌體與硬體的項目；反覆回報的裝置另外列表
    assert len(findings) == 3
    repeat = {r["device"]: r["n"] for r in data["ops"]["repeat"]}
    assert repeat == {"DEV-B01": 3, "DEV-B02": 2, "DEV-B03": 2, "DEV-C01": 2}


def test_ops_bars_use_the_same_numbers_as_the_tables(data):
    """比較圖和表格同一份資料：1.2 的資料逾時每台 1.17 件、B1 換電源供應器 4 次，一眼看得出來。"""
    fw = {g["label"]: {r["fw"]: r["r"] for r in g["rows"]} for g in data["ops"]["fw_bars"]}
    assert fw["資料逾時"] == {"1.2": "1.17", "1.3": "0.08"}
    assert fw["斷線"] == {"1.2": "0.50", "1.3": "0.25"}
    batch = {g["part"]: {r["batch"]: r["n"] for r in g["rows"]} for g in data["ops"]["batch_bars"]}
    assert batch == {"電源供應器": {"B1": 4, "B2": 0, "B3": 0}}


def _contrast(a: str, b: str) -> float:
    def lum(h: str) -> float:
        c = [int(h[i:i + 2], 16) / 255 for i in (1, 3, 5)]
        c = [x / 12.92 if x <= 0.03928 else ((x + 0.055) / 1.055) ** 2.4 for x in c]
        return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]
    hi, lo = sorted((lum(a), lum(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def test_colors_meet_wcag_contrast_in_light_and_dark(repo):
    """WCAG 2.1 AA：說明文字對背景 4.5:1；輸入框與按鈕的框線對背景 3:1（1.4.11）。深色模式另外算。"""
    css = (repo / "demo" / "dashboard_template.html").read_text(encoding="utf-8")
    blocks = re.findall(r":root(?:\[data-theme=\"dark\"\])?\s*\{([^}]*)\}", css)
    assert len(blocks) == 2
    for block in blocks:
        tokens = dict(re.findall(r"--([\w-]+):\s*(#[0-9a-fA-F]{6})", block))
        for surface in ("bg", "surface"):
            assert _contrast(tokens["muted"], tokens[surface]) >= 4.5
            assert _contrast(tokens["control"], tokens[surface]) >= 3


def test_ops_rule_text_follows_the_thresholds(build, data):
    """頁面上的列入規則照常數產生：程式用的門檻和公布的門檻要一致。"""
    rule = build.UI_TEXT["ops_rule"].format(**data["ops_rule"])
    assert f"至少 {build.SAME_ACTION_AT_LEAST} 件已結案" in rule
    assert f"{build.FINDING_RATIO} 倍且至少 {build.FINDING_AT_LEAST} 件" in rule
    assert f"{build.REPEAT_AT_LEAST} 次以上" in build.UI_TEXT["ops_repeat_rule"].format(**data["ops_rule"])
    assert "已結案" in build.UI_TEXT["ops_downtime"], "停機合計只算已結案的回報單"


def test_ops_data_has_no_place_names_mac_or_care_events(data):
    text = json.dumps(data["ops"], ensure_ascii=False)
    places = {d["place"] for d in data["devices"]}
    assert not any(p in text for p in places), "維運後台只用場域代號與裝置代號"
    assert "radar10/" not in text and "跌倒" not in text and "呼救" not in text


def test_ops_finding_with_medical_wording_stops_the_build(build, monkeypatch):
    monkeypatch.setitem(build.UI_TEXT, "f_batch", "批次 {batch} 需要診斷{part}（{n}/{d}/{o}）")
    with pytest.raises(ValueError, match="用語檢查"):
        build.dashboard_data()


def test_record_counts_add_up_even_with_unknown_events(build):
    """規格外的 event 算在無法判讀，不重複算進裝置紀錄；加總要等於收到的筆數。"""
    from radar_agent.device_events import summarize

    rows = build._heartbeats("radar10/X-1", "08:00:00", "08:01:00")
    rows.append({"timestamp": "2026-10-14 08:01:05", "topic": "radar10/X-1", "resolution": None,
                 "payload": {"event": "dance"}})
    summary = summarize(rows)
    diag = build.diagnostics(rows, summary)
    unreadable = len(summary.unreadable) + len(summary.unknown_events)
    assert unreadable == 1
    assert diag["device_records"] + unreadable + diag["offline_records"] == diag["all_records"] == len(rows)
