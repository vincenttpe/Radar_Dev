"""Jetson 與 Dashboard 的連線（docs/adr/0009 提議中，docs/23 API 草案 v0）。

用 demo/fake_dashboard.py 在本機起一個照草案運作的假 Dashboard，透過正式的 HttpDashboard 走一遍
「取需求 → 分頁取裝置事件 → 產生 → 傳回」；資料是模擬上午，不連真實雲端。
"""
import threading
import urllib.error

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


def test_a_job_without_an_id_is_logged_and_the_loop_waits_before_asking_again():
    """沒有 job_id 傳不回去；Dashboard 沒有需求時回 200 加 {} 也是這樣：先等一下再問，不會空轉。"""
    lines = []

    class OneJob(Memory):
        def next_job(self):
            return {**JOB, "job_id": ""}

    assert not run_once(OneJob(), None, log=lines.append)
    assert lines == ["收到沒有 job_id 的需求，無法傳回結果"]


@pytest.mark.parametrize("reply", [{}, []])
def test_an_odd_empty_reply_does_not_spin_the_loop(reply):
    """實測過：Dashboard 一直回 200 加 {} 或 []，舊版 1 秒內打了近千次請求。現在每次都先等 interval 再問。"""
    sleeps, lines, rounds = [], [], iter([True] * 3 + [False])

    class Odd(Memory):
        def next_job(self):
            return reply

    run(Odd(), None, log=lines.append, sleep=sleeps.append, keep_going=lambda: next(rounds))
    assert len(sleeps) == 3


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
    assert [u.body["job_id"] for u in unsent] == ["j-9"] and link.posted == []
    assert lines[0] == "j-9 結果沒送出（ConnectionResetError），下一輪再送"
    assert not run_once(link, None, log=lines.append, unsent=unsent)
    assert unsent == [] and link.posted == ["j-9"]


def test_while_the_dashboard_is_unreachable_no_new_job_is_taken():
    """補送失敗時先不取新需求：沒送出的結果不會越積越多，需求也留在 Dashboard 的佇列裡。"""
    link, lines, unsent = Flaky(), [], []
    link.fail, link.jobs = 99, [{**JOB, "job_id": "j-2"}, {**JOB, "job_id": "j-1"}]
    assert run_once(link, None, log=lines.append, unsent=unsent)
    for _ in range(3):
        assert not run_once(link, None, log=lines.append, unsent=unsent)
    assert [u.body["job_id"] for u in unsent] == ["j-1"] and [j["job_id"] for j in link.jobs] == ["j-2"]


class Refusing(Flaky):
    """傳回結果時 Dashboard 回指定的 HTTP 錯誤碼。"""

    def __init__(self, code):
        super().__init__()
        self.code, self.jobs = code, [{**JOB, "job_id": "j-2"}, {**JOB, "job_id": "j-1"}]

    def post_result(self, job_id, body):
        self.posted.append(job_id)
        raise urllib.error.HTTPError("http://x", self.code, "no", {}, None)


def test_a_result_the_dashboard_refuses_is_dropped_and_the_next_job_goes_ahead():
    """Dashboard 明確不收（例如 410 已逾時）：再送也沒用，丟掉並記下來，不擋住後面的需求。"""
    link, lines, unsent = Refusing(410), [], []
    assert run_once(link, None, log=lines.append, unsent=unsent)
    assert run_once(link, None, log=lines.append, unsent=unsent)
    assert unsent == [] and link.posted == ["j-1", "j-2"]
    assert lines[0] == "j-1 結果 Dashboard 不收（HTTP 410），丟掉"


@pytest.mark.parametrize("code", [408, 429, 503])
def test_a_temporary_refusal_is_sent_again(code):
    link, lines, unsent = Refusing(code), [], []
    assert run_once(link, None, log=lines.append, unsent=unsent)
    assert [u.body["job_id"] for u in unsent] == ["j-1"]


def test_a_job_that_is_not_an_object_is_skipped_and_the_loop_goes_on():
    lines = []

    class Odd(Memory):
        def __init__(self):
            super().__init__()
            self.queue = [["x"], dict(JOB)]

        def next_job(self):
            return self.queue.pop(0) if self.queue else None

        def post_result(self, job_id, body):
            lines.append(f"posted {job_id}")

    link = Odd()
    assert not run_once(link, None, log=lines.append)
    assert lines == ["Dashboard 回的需求不是物件（list），略過"]
    assert run_once(link, None, log=lines.append) and "posted j-9" in lines


def test_a_record_with_an_unexpected_shape_fails_the_job_instead_of_stopping():
    """payload.params 是清單時，整理裝置事件會出錯（device_events 另外處理）：這裡一樣傳回 failed、只寫錯誤種類。"""
    odd = [*RECORDS[:3], {**RECORDS[3], "payload": {**RECORDS[3]["payload"], "params": [1]}}]
    body = handle(JOB, Memory(records=odd), None)
    assert body["status"] == "failed" and body["problems"] == ["整理裝置事件時發生錯誤：AttributeError"]


def test_complete_must_be_a_real_true(monkeypatch):
    link = HttpDashboard("http://127.0.0.1:9", "t")
    monkeypatch.setattr(link, "_call", lambda method, path, body=None: (
        200, {"records": [], "next_cursor": None, "complete": "false"}))
    assert link.records("D", "2026-10-14 08:00:00", "2026-10-14 09:00:00").complete is False


class _Response:
    """假的 HTTP 回應：給 HttpDashboard._call 用。"""

    def __init__(self, raw, status=200, error=None):
        self.raw, self.status, self.error = raw, status, error

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        if self.error:
            raise self.error
        return self.raw


def test_a_truncated_response_counts_as_unreachable(monkeypatch):
    """回應傳到一半斷掉（http.client 的錯誤不是 OSError）：一樣當成連不上，下一輪再試，服務不停。"""
    import http.client

    link = HttpDashboard("http://127.0.0.1:9", "t")
    monkeypatch.setattr(link._opener, "open", lambda req, timeout: _Response(b"", error=http.client.IncompleteRead(b"")))
    with pytest.raises(OSError, match="回應不完整"):
        link.next_job()
    lines, rounds = [], iter([True, False])
    run(link, None, log=lines.append, sleep=lambda s: None, keep_going=lambda: next(rounds))
    assert lines == ["連不上 Dashboard：OSError"]


def test_a_result_is_sent_once_the_dashboard_answers_2xx_whatever_the_body(monkeypatch):
    """傳回結果時 Dashboard 回 200 加上「OK」：算送到，不解析內容，不會一直重送。"""
    link = HttpDashboard("http://127.0.0.1:9", "t")
    monkeypatch.setattr(link._opener, "open", lambda req, timeout: _Response(b"OK"))
    link.post_result("j-1", {"job_id": "j-1"})


def test_a_reply_that_is_not_json_is_logged_as_an_odd_reply(monkeypatch):
    link = HttpDashboard("http://127.0.0.1:9", "t")
    monkeypatch.setattr(link._opener, "open", lambda req, timeout: _Response(b"<html>"))
    lines, rounds = [], iter([True, False])
    run(link, None, log=lines.append, sleep=lambda s: None, keep_going=lambda: next(rounds))
    assert lines == ["Dashboard 回應異常：JSONDecodeError"]


def test_a_result_is_dropped_after_the_retry_limit_so_new_jobs_go_ahead(monkeypatch):
    monkeypatch.setattr(dashboard_link, "MAX_RETRIES", 2)
    link, lines, unsent = Refusing(503), [], []
    assert run_once(link, None, log=lines.append, unsent=unsent)  # j-1 第一次沒送出
    assert not run_once(link, None, log=lines.append, unsent=unsent)  # 補送第 1 次也失敗：先不取新需求
    assert run_once(link, None, log=lines.append, unsent=unsent)  # 補送第 2 次也失敗：丟掉，接著做 j-2
    assert "j-1 結果補送 2 次還沒送到（HTTPError），丟掉；等 Dashboard 逾時重發" in lines
    assert link.posted == ["j-1", "j-1", "j-1", "j-2"] and [u.body["job_id"] for u in unsent] == ["j-2"]


def test_an_unexpected_error_from_the_dashboard_never_stops_the_loop(monkeypatch):
    """極深的巢狀 JSON 會讓 json.loads 丟 RecursionError：輪詢迴圈攔下所有意料外的錯誤，只記種類，服務不停。"""
    link = HttpDashboard("http://127.0.0.1:9", "t")
    deep = b"[" * 100000 + b"]" * 100000
    monkeypatch.setattr(link._opener, "open", lambda req, timeout: _Response(deep))
    lines, rounds = [], iter([True, True, False])
    run(link, None, log=lines.append, sleep=lambda s: None, keep_going=lambda: next(rounds))
    assert lines == ["意料外的錯誤：RecursionError，等一下再試"] * 2


def test_a_truncated_reply_to_a_result_keeps_it_for_the_next_round(monkeypatch):
    """傳回結果時回應被截斷：不知道 Dashboard 收到沒有，留著下一輪再送（Dashboard 要能接受重複，docs/23）。"""
    import http.client

    link = HttpDashboard("http://127.0.0.1:9", "t")
    monkeypatch.setattr(link._opener, "open", lambda req, timeout: _Response(b"", error=http.client.IncompleteRead(b"")))
    lines, unsent = [], []
    item = dashboard_link.Unsent({"job_id": "j-1"})
    assert not dashboard_link._send(link, item, unsent, lines.append)
    assert [(u.body["job_id"], u.tries) for u in unsent] == [("j-1", 1)]
    assert lines == ["j-1 結果沒送出（OSError），下一輪再送"]


def test_cli_once_stops_with_a_reason_on_an_unexpected_error(dashboard, monkeypatch, capsys):
    _, base = dashboard()
    monkeypatch.setenv(dashboard_link.TOKEN_ENV, fake_dashboard.TOKEN)
    monkeypatch.setattr(dashboard_link.HttpDashboard, "next_job", lambda self: (_ for _ in ()).throw(RecursionError()))
    assert main(["--base", base, "--once"]) == 2
    assert "意料外的錯誤 RecursionError" in capsys.readouterr().err


def test_cli_refuses_to_start_before_the_clock_is_set(monkeypatch, capsys):
    """沒有 RTC 電池的 Jetson 開機後時間可能回到 1970：先校時，否則 HTTPS 憑證會驗證失敗。"""
    from datetime import UTC, datetime

    monkeypatch.setattr(dashboard_link, "CLOCK_FLOOR", datetime(2999, 1, 1, tzinfo=UTC))
    monkeypatch.setenv(dashboard_link.TOKEN_ENV, "t")
    assert main(["--base", "https://dashboard.example.com", "--once"]) == 2
    assert "NTP 校時" in capsys.readouterr().err
