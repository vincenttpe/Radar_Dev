"""Jetson 與 Dashboard 的連線（docs/adr/0009 提議中，docs/23 API 草案 v0）。

用 demo/fake_dashboard.py 在本機起一個照草案運作的假 Dashboard，透過正式的 HttpDashboard 走一遍
「取需求 → 分頁取裝置事件 → 產生 → 傳回」；資料是模擬上午，不連真實雲端。
"""
import threading

import fake_dashboard
import pytest
from fakes import FakeModel

from radar_agent import dashboard_link
from radar_agent.caregiver import report_data
from radar_agent.dashboard_link import Fetched, HttpDashboard, Job, handle, main, run, run_once
from radar_agent.device_events import read_records, summarize
from radar_agent.time_range import in_range, parse_bound

RECORDS = read_records(fake_dashboard.page_data.SAMPLE)
PLACE = "1 號廁所"


@pytest.fixture
def dashboard():
    """回傳 (假 Dashboard, 網址)；每個測試一份新的佇列。"""
    started = []

    def start(jobs=fake_dashboard.JOBS, page_size=fake_dashboard.PAGE_SIZE):
        fake = fake_dashboard.FakeDashboard(RECORDS, [dict(j) for j in jobs], page_size=page_size)
        httpd = fake_dashboard.serve(fake, 0, quiet=True)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        started.append(httpd)
        return fake, f"http://127.0.0.1:{httpd.server_address[1]}"

    yield start
    for httpd in started:
        httpd.shutdown()
        httpd.server_close()


def _drain(base, model, token=fake_dashboard.TOKEN):
    lines = []
    link = HttpDashboard(base, token)
    while run_once(link, model, log=lines.append):
        pass
    return lines


def test_every_job_in_the_queue_is_answered_and_sent_back(dashboard):
    fake, base = dashboard()
    _drain(base, FakeModel("1"))
    by_id = {r["job_id"]: r for r in fake.results}
    assert set(by_id) == {"j-1", "j-2", "j-3"} and not fake.jobs

    report = by_id["j-1"]
    expected = report_data(summarize(in_range(RECORDS, parse_bound("2026-10-14 08:00:00"),
                                              parse_bound("2026-10-14 12:00:00"))), place=PLACE)
    assert (report["status"], report["result"], report["problems"]) == ("done", expected, [])
    assert (report["received_until"], report["complete"]) == ("2026-10-14 11:59:55", True)

    ask = by_id["j-2"]["result"]
    assert (by_id["j-2"]["status"], ask["schema"], ask["question"]["id"]) == ("done", "event-ask-v0", "falls")
    assert ask["answer"].startswith("1 號廁所 10/14 08:00–11:59：")


def test_a_range_longer_than_the_data_says_how_far_the_data_goes(dashboard):
    """要到 14:00、資料只到 11:59：照樣產生，並附上資料到哪裡為止（docs/23 ②）。"""
    fake, base = dashboard()
    _drain(base, FakeModel("1"))
    late = next(r for r in fake.results if r["job_id"] == "j-3")
    assert (late["status"], late["complete"], late["received_until"]) == ("done", False, "2026-10-14 11:59:55")


def test_records_are_fetched_page_by_page_until_the_last_page(dashboard):
    _, base = dashboard(jobs=fake_dashboard.JOBS[:1], page_size=7)
    fetched = HttpDashboard(base, fake_dashboard.TOKEN).records("DEMO-SIM-01", "2026-10-14 08:00:00", "2026-10-14 08:05:00")
    assert fetched.records == in_range(RECORDS, parse_bound("2026-10-14 08:00:00"), parse_bound("2026-10-14 08:05:00"))
    assert len(fetched.records) > 7


def test_the_asked_text_is_never_logged(dashboard):
    _, base = dashboard()
    lines = _drain(base, FakeModel("1"))
    assert len(lines) == 3 and all("跌倒" not in line for line in lines), "提問原文不寫進 log"
    assert lines[1].startswith("j-2 ask done ")


def test_a_wrong_token_is_refused_and_the_loop_keeps_going(dashboard):
    _, base = dashboard()
    with pytest.raises(OSError):
        HttpDashboard(base, "wrong").next_job()
    lines, rounds = [], iter([True, False])
    run(HttpDashboard(base, "wrong"), None, log=lines.append, sleep=lambda s: None, keep_going=lambda: next(rounds))
    assert lines == ["連不上 Dashboard：HTTPError"]


def test_only_https_or_a_local_fake_dashboard_is_accepted():
    with pytest.raises(ValueError, match="HTTPS"):
        HttpDashboard("http://dashboard.example.com", "t")
    with pytest.raises(ValueError, match="RADAR_DASHBOARD_TOKEN"):
        HttpDashboard("https://dashboard.example.com", "")
    HttpDashboard("https://dashboard.example.com", "t")
    HttpDashboard("http://127.0.0.1:8770", "t")


class Memory:
    """記憶體裡的 Dashboard：測 handle 的各種失敗，不必起伺服器。"""

    def __init__(self, records=RECORDS, error=None):
        self.fetched, self.error = Fetched(records, "2026-10-14 11:59:55", True), error

    def next_job(self):
        return None

    def records(self, device, start, end):
        if self.error:
            raise self.error
        return self.fetched

    def post_result(self, job_id, body):
        raise AssertionError("handle 不傳回結果")


JOB = {"job_id": "j-9", "kind": "report", "device": "DEMO-SIM-01", "place": PLACE,
       "from": "2026-10-14 08:00:00", "to": "2026-10-14 12:00:00"}


@pytest.mark.parametrize("job, problem", [
    ({**JOB, "kind": "trend"}, "不認得的需求種類：trend"),
    ({**JOB, "place": ""}, "需求缺少欄位：place"),
    ({**JOB, "kind": "ask"}, "提問需求沒有提問"),
    ({**JOB, "to": "明天"}, "看不懂"),
])
def test_a_malformed_job_fails_and_says_why(job, problem):
    body = handle(job, Memory(), None)
    assert (body["job_id"], body["status"], body["result"]) == ("j-9", "failed", None)
    assert problem in body["problems"][0]


def test_records_that_cannot_be_fetched_fail_the_job_instead_of_stopping():
    body = handle(JOB, Memory(error=TimeoutError()), None)
    assert body["status"] == "failed" and body["problems"] == ["TimeoutError："]


def test_a_report_that_fails_the_output_check_sends_no_text():
    """和 radar-caregiver 一樣：沒通過輸出檢查就不交出任何文字，原因寫在 problems。"""
    body = handle({**JOB, "place": "治療室"}, Memory(), None)
    assert (body["status"], body["result"]) == ("rejected", None) and body["problems"]


def test_records_outside_the_range_are_not_counted():
    """Dashboard 多給了範圍外的紀錄：照範圍挑，和 radar-caregiver --from --to 相同。"""
    body = handle({**JOB, "from": "2026-10-14 08:00:00", "to": "2026-10-14 09:00:00"}, Memory(), None)
    expected = report_data(summarize(in_range(RECORDS, parse_bound("2026-10-14 08:00:00"),
                                              parse_bound("2026-10-14 09:00:00"))), place=PLACE)
    assert body["result"] == expected


def test_a_job_without_an_id_is_logged_and_not_sent_back():
    lines = []

    class OneJob(Memory):
        def next_job(self):
            return {**JOB, "job_id": ""}

    assert run_once(OneJob(), None, log=lines.append)
    assert lines[0] == "收到沒有 job_id 的需求，無法傳回結果" and lines[1].startswith("- report failed ")


def test_job_keeps_the_fields_of_the_draft():
    job = Job.from_json({**JOB, "kind": "ask", "question": "有人跌倒嗎"})
    assert (job.job_id, job.kind, job.start, job.end, job.question) == (
        "j-9", "ask", "2026-10-14 08:00:00", "2026-10-14 12:00:00", "有人跌倒嗎")


def test_cli_once_answers_the_queue_and_exits(dashboard, monkeypatch, capsys):
    fake, base = dashboard()
    monkeypatch.setenv(dashboard_link.TOKEN_ENV, fake_dashboard.TOKEN)
    assert main(["--base", base, "--once", "--echo"]) == 0
    assert {r["job_id"] for r in fake.results} == {"j-1", "j-2", "j-3"}
    assert "早上有人跌倒嗎" not in capsys.readouterr().err


def test_cli_without_a_token_stops_before_connecting(monkeypatch, capsys):
    monkeypatch.delenv(dashboard_link.TOKEN_ENV, raising=False)
    assert main(["--base", "https://dashboard.example.com", "--once"]) == 2
    assert "RADAR_DASHBOARD_TOKEN" in capsys.readouterr().err


# ── 2026-11-02 審查後補：轉址、格式不對的資料、送不出去的結果、系統時間 ─────────

def test_redirects_are_not_followed_so_the_token_stays_put(dashboard):
    """轉址時 urllib 會把 Authorization 一起帶去新位置：一律不跟，token 不會送到別的主機。"""
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    seen = []

    class Elsewhere(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            seen.append(self.headers.get("Authorization"))
            self.send_response(204)
            self.end_headers()

    other = ThreadingHTTPServer(("127.0.0.1", 0), Elsewhere)
    threading.Thread(target=other.serve_forever, daemon=True).start()

    class Redirect(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            self.send_response(302)
            self.send_header("Location", f"http://127.0.0.1:{other.server_address[1]}/api/v1/jetson/jobs/next")
            self.end_headers()

    source = ThreadingHTTPServer(("127.0.0.1", 0), Redirect)
    threading.Thread(target=source.serve_forever, daemon=True).start()
    try:
        with pytest.raises(OSError):
            HttpDashboard(f"http://127.0.0.1:{source.server_address[1]}", "secret-token").next_job()
        assert seen == []
    finally:
        for server in (source, other):
            server.shutdown()
            server.server_close()


@pytest.mark.parametrize("records", [{"a": 1}, ["不是物件"], None])
def test_records_in_the_wrong_shape_fail_the_job_instead_of_stopping(records, monkeypatch):
    link = HttpDashboard("http://127.0.0.1:9", "t")
    monkeypatch.setattr(link, "_call", lambda method, path, body=None: (200, {"records": records, "next_cursor": None}))
    body = handle(JOB, link, None)
    assert body["status"] == "failed" and "records 要是一筆一個物件的清單" in body["problems"][0]


def test_a_cursor_that_never_ends_stops_at_the_page_limit(monkeypatch):
    link = HttpDashboard("http://127.0.0.1:9", "t")
    monkeypatch.setattr(dashboard_link, "MAX_PAGES", 3)
    monkeypatch.setattr(link, "_call", lambda method, path, body=None: (200, {"records": [], "next_cursor": "same"}))
    body = handle(JOB, link, None)
    assert body["status"] == "failed" and "超過 3 頁" in body["problems"][0]


def test_a_question_that_is_not_text_fails_the_job():
    body = handle({**JOB, "kind": "ask", "question": 123}, Memory(), None)
    assert body["status"] == "failed" and "提問不是文字" in body["problems"][0]


def test_an_unexpected_error_while_generating_is_sent_back_without_details(monkeypatch):
    """意料外的錯誤也傳回 failed，不讓服務停掉；只寫錯誤種類，訊息可能帶出提問原文所以不寫。"""
    def boom(*args, **kwargs):
        raise RuntimeError("早上有人跌倒嗎")

    monkeypatch.setattr(dashboard_link, "ask_events", boom)
    body = handle({**JOB, "kind": "ask", "question": "早上有人跌倒嗎"}, Memory(), None)
    assert (body["status"], body["problems"]) == ("failed", ["產生時發生錯誤：RuntimeError"])


class Flaky(Memory):
    """第一次傳回結果時連不上，之後就好了；佇列裡只有一筆需求。"""

    def __init__(self):
        super().__init__()
        self.jobs, self.posted, self.fail = [dict(JOB)], [], 1

    def next_job(self):
        return self.jobs.pop() if self.jobs else None

    def post_result(self, job_id, body):
        if self.fail:
            self.fail -= 1
            raise ConnectionResetError
        self.posted.append(job_id)


def test_a_result_that_could_not_be_sent_is_sent_on_the_next_round():
    link, lines, unsent = Flaky(), [], []
    assert run_once(link, None, log=lines.append, unsent=unsent)
    assert [b["job_id"] for b in unsent] == ["j-9"] and link.posted == []
    assert lines[0] == "j-9 結果沒送出（ConnectionResetError），下一輪再送"
    assert not run_once(link, None, log=lines.append, unsent=unsent)
    assert unsent == [] and link.posted == ["j-9"]


def test_unsent_results_are_capped(monkeypatch):
    monkeypatch.setattr(dashboard_link, "MAX_UNSENT", 1)
    link, lines = Flaky(), []
    link.fail = 99
    unsent = [{**JOB, "job_id": "old"}]
    assert not run_once(link, None, log=lines.append, unsent=unsent)
    assert lines == ["old 結果沒送出（ConnectionResetError），下一輪再送"]
    link.jobs = [dict(JOB)]
    unsent.clear()
    run_once(link, None, log=lines.append, unsent=unsent)
    unsent.append({**JOB, "job_id": "older"})
    run_once(link, None, log=lines.append, unsent=unsent)
    assert any("留太久還沒送出，丟掉" in line for line in lines) and len(unsent) == 1


def test_cli_refuses_to_start_before_the_clock_is_set(monkeypatch, capsys):
    """沒有 RTC 電池的 Jetson 開機後時間可能回到 1970：先校時，否則 HTTPS 憑證會驗證失敗。"""
    from datetime import UTC, datetime

    monkeypatch.setattr(dashboard_link, "CLOCK_FLOOR", datetime(2999, 1, 1, tzinfo=UTC))
    monkeypatch.setenv(dashboard_link.TOKEN_ENV, "t")
    assert main(["--base", "https://dashboard.example.com", "--once"]) == 2
    assert "NTP 校時" in capsys.readouterr().err
