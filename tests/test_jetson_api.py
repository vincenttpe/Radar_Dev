"""Dashboard 直接呼叫 Jetson 的 API（docs/adr/0011，docs/23「另一種方式」）。

在本機起 radar_agent.jetson_api 的伺服器，用 HTTP（與自簽憑證的 HTTPS）實際送請求：
裝置事件放在請求裡，一次往返拿回和輪詢方式（dashboard_link）相同的 JSON。資料是模擬上午，不連真實雲端。
"""
import http.client
import json
import ssl
import subprocess
import threading

import fake_dashboard
import pytest
from fakes import FakeModel

from radar_agent import jetson_api
from radar_agent.caregiver import report_data
from radar_agent.dashboard_link import Fetched, handle
from radar_agent.device_events import read_records, summarize
from radar_agent.event_ask import pick
from radar_agent.time_range import in_range, parse_bound

RECORDS = read_records(fake_dashboard.page_data.SAMPLE)
PLACE = "1 號廁所"
TOKEN = "t" * 40
START, END = "2026-10-14 08:00:00", "2026-10-14 12:00:00"
MORNING = in_range(RECORDS, parse_bound(START), parse_bound(END))


def _job(**extra):
    return {"job_id": "d-1", "kind": "report", "device": "DEMO-SIM-01", "place": PLACE, "from": START, "to": END,
            "records": MORNING, "received_until": "2026-10-14 11:59:55", "complete": True, **extra}


@pytest.fixture
def api():
    """回傳 start(...)：起一個 Jetson API，回 (伺服器, log 清單)；測試結束時關掉。"""
    started = []

    def start(model=None, *, allow=None, tls=None, busy_wait=jetson_api.BUSY_WAIT, max_body=jetson_api.MAX_BODY):
        lines = []
        server = jetson_api.JetsonApi(("127.0.0.1", 0), token=TOKEN, model=model, allow=allow or jetson_api.LOCAL_NETS,
                                      tls=tls, busy_wait=busy_wait, max_body=max_body, log=lines.append)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        started.append(server)
        return server, lines

    yield start
    for server in started:
        server.shutdown()
        server.server_close()


def _call(server, method="POST", path=jetson_api.JOBS_PATH, body=None, *, token=TOKEN, raw=None,
          content_type="application/json", context=None):
    port = server.server_address[1]
    conn = (http.client.HTTPSConnection("127.0.0.1", port, context=context, timeout=10) if context
            else http.client.HTTPConnection("127.0.0.1", port, timeout=10))
    headers = {"Content-Type": content_type} if content_type else {}
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    data = raw if raw is not None else (json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None)
    conn.request(method, path, body=data, headers=headers)
    resp = conn.getresponse()
    payload = resp.read()
    conn.close()
    return resp.status, (json.loads(payload) if payload else None), resp


def test_a_report_request_with_records_returns_the_same_json_as_the_polling_way(api):
    server, _ = api()
    status, body, _ = _call(server, body=_job())
    assert status == 200
    expected = report_data(summarize(MORNING), place=PLACE)
    assert (body["job_id"], body["status"], body["result"], body["problems"]) == ("d-1", "done", expected, [])
    assert (body["received_until"], body["complete"]) == ("2026-10-14 11:59:55", True)
    assert "records" not in body  # 傳回的只有文字，不把裝置事件送回去

    class Polled:  # 同一筆需求走輪詢的 handle()：結果要一模一樣
        def records(self, device, start, end):
            return Fetched(MORNING, "2026-10-14 11:59:55", True)

    assert body == handle(_job(), Polled(), None)


def test_ask_uses_the_local_model_and_pick_does_not(api):
    model = FakeModel("1")
    server, _ = api(model)
    _, asked, _ = _call(server, body=_job(kind="ask", question="早上有人跌倒嗎"))
    assert (asked["status"], asked["result"]["question"]["id"]) == ("done", "falls")
    assert asked["result"]["confirm"] == "您是想問「有沒有人跌倒？」嗎？"
    assert len(model.prompts) == 1

    _, picked, _ = _call(server, body=_job(kind="pick", question_id="visits"))
    assert picked["result"]["answer"] == pick("visits", summarize(MORNING), place=PLACE).answer
    assert len(model.prompts) == 1  # 點題不經過模型


def test_records_outside_the_range_are_not_counted(api):
    """Dashboard 多給了範圍外的紀錄也不算進去：範圍含開始、不含結束，和輪詢方式相同。"""
    server, _ = api()
    _, body, _ = _call(server, body=_job(records=RECORDS))
    assert body["result"] == report_data(summarize(MORNING), place=PLACE)


def test_complete_is_true_only_when_the_dashboard_says_exactly_true(api):
    server, _ = api()
    for given in ({"complete": "true"}, {"complete": None}):
        _, body, _ = _call(server, body=_job(**given))
        assert body["complete"] is False
    no_flag = _job()
    del no_flag["complete"], no_flag["received_until"]
    _, body, _ = _call(server, body=no_flag)
    assert (body["complete"], body["received_until"]) == (False, None)


@pytest.mark.parametrize("records", ["不是清單", [1, 2], None])
def test_records_in_the_wrong_shape_fail_the_job_with_a_reason(api, records):
    server, _ = api()
    job = _job(records=records)
    if records is None:
        del job["records"]
    status, body, _ = _call(server, body=job)
    assert (status, body["status"], body["result"]) == (200, "failed", None)
    assert "records" in body["problems"][0]


def test_too_many_records_fail_the_job(api, monkeypatch):
    monkeypatch.setattr(jetson_api, "MAX_RECORDS", 10)
    server, _ = api()
    _, body, _ = _call(server, body=_job())
    assert body["status"] == "failed" and "超過 10 筆" in body["problems"][0]


def test_a_job_missing_fields_fails_with_the_job_id_returned(api):
    server, _ = api()
    _, body, _ = _call(server, body=_job(device=""))
    assert (body["job_id"], body["status"]) == ("d-1", "failed") and "device" in body["problems"][0]


@pytest.mark.parametrize("token", [None, "wrong", "t" * 39, "t" * 41])
def test_a_wrong_or_missing_token_is_refused_before_anything_is_done(api, token):
    model = FakeModel("1")
    server, lines = api(model)
    status, body, resp = _call(server, body=_job(kind="ask", question="早上有人跌倒嗎"), token=token)
    assert status == 401 and resp.getheader("WWW-Authenticate") == "Bearer"
    assert body == {"error": "token 不對"} and not model.prompts
    assert all("跌倒" not in line for line in lines)


def test_health_needs_the_token_and_says_whether_a_model_is_attached(api):
    server, _ = api(FakeModel("1"))
    assert _call(server, "GET", jetson_api.HEALTH_PATH, token=None)[0] == 401
    status, body, _ = _call(server, "GET", jetson_api.HEALTH_PATH)
    assert (status, body) == (200, {"status": "ok", "model": True})


@pytest.mark.parametrize(("method", "path", "code"), [
    ("GET", "/api/v1/jetson/jobs/next", 404),
    ("POST", "/api/v1/other", 404),
    ("GET", jetson_api.JOBS_PATH, 405),
    ("POST", jetson_api.HEALTH_PATH, 405),
])
def test_unknown_paths_and_methods(api, method, path, code):
    server, _ = api()
    assert _call(server, method, path, body={})[0] == code


def test_a_body_that_is_not_a_json_object_is_refused(api):
    server, _ = api()
    assert _call(server, raw=b"{not json")[0:2] == (400, {"error": "內容不是 JSON"})
    assert _call(server, raw=b"[1, 2]")[0:2] == (400, {"error": "內容要是一個 JSON 物件"})
    assert _call(server, raw="{\"a\": \"\udc80\"}".encode("utf-8", "surrogatepass"))[0] == 400  # 不是正確的 UTF-8


def test_an_escaped_lone_surrogate_in_the_job_id_still_gets_a_valid_answer(api):
    server, _ = api()
    raw = json.dumps(_job(job_id="d-\udc80")).encode("ascii")  # 合法的 JSON，但解出來是落單的代理字元
    status, body, _ = _call(server, raw=raw)
    assert (status, body["job_id"], body["status"]) == (200, "d-\udc80", "done")


def test_the_content_type_must_be_json(api):
    server, _ = api()
    assert _call(server, body=_job(), content_type="text/plain")[0] == 415
    assert _call(server, body=_job(), content_type="application/json; charset=utf-8")[0] == 200


def test_a_body_larger_than_the_limit_is_refused_without_reading_it(api):
    """只送標頭、不送內容：伺服器看 Content-Length 就回 413，不等內容送來。"""
    server, _ = api(max_body=1000)
    conn = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=10)
    conn.putrequest("POST", jetson_api.JOBS_PATH)
    for name, value in (("Authorization", f"Bearer {TOKEN}"), ("Content-Type", "application/json"),
                        ("Content-Length", "5000")):
        conn.putheader(name, value)
    conn.endheaders()
    resp = conn.getresponse()
    assert (resp.status, json.loads(resp.read())) == (413, {"error": "內容超過 1000 位元組"})
    conn.close()


@pytest.mark.parametrize(("length", "code"), [(None, 411), ("abc", 400), ("-1", 400)])
def test_the_content_length_must_be_given_and_valid(api, length, code):
    server, _ = api()
    conn = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=10)
    conn.putrequest("POST", jetson_api.JOBS_PATH)
    conn.putheader("Authorization", f"Bearer {TOKEN}")
    conn.putheader("Content-Type", "application/json")
    if length is not None:
        conn.putheader("Content-Length", length)
    conn.endheaders()
    assert conn.getresponse().status == code
    conn.close()


def test_a_client_outside_the_allowed_networks_is_dropped_before_http(api):
    server, lines = api(allow=jetson_api.parse_allow(["192.0.2.0/24"]))
    with pytest.raises((ConnectionError, http.client.RemoteDisconnected)):
        _call(server, body=_job())
    assert lines == ["拒絕來自 127.0.0.1 的連線（不在 --allow 裡）"]


def test_parse_allow_accepts_addresses_and_networks_and_refuses_nonsense():
    nets = jetson_api.parse_allow(["203.0.113.5", "198.51.100.0/24"])
    assert [str(n) for n in nets] == ["203.0.113.5/32", "198.51.100.0/24"]
    with pytest.raises(ValueError, match="看不懂的 --allow"):
        jetson_api.parse_allow(["dashboard.example.com"])
    with pytest.raises(ValueError, match="太大"):
        jetson_api.parse_allow(["0.0.0.0/0"])


def test_a_busy_jetson_answers_503_instead_of_queueing_forever(api):
    release = threading.Event()

    class Slow(FakeModel):
        def __call__(self, prompt):
            release.wait(10)
            return super().__call__(prompt)

    server, _ = api(Slow("1"), busy_wait=0.2)
    first = {}
    t = threading.Thread(target=lambda: first.update(r=_call(server, body=_job(kind="ask", question="早上有人跌倒嗎"))))
    t.start()
    try:
        for _ in range(100):  # 等第一筆真的進到模型
            if server.busy.locked():
                break
            threading.Event().wait(0.02)
        status, body, resp = _call(server, body=_job())
        assert (status, body, resp.getheader("Retry-After")) == (503, {"error": "正在處理另一筆需求，請稍後再送"}, "5")
    finally:
        release.set()
        t.join(10)
    assert first["r"][1]["status"] == "done"


def test_the_log_keeps_ids_and_results_but_never_the_question(api):
    server, lines = api(FakeModel("1"))
    _call(server, body=_job(kind="ask", question="早上王奶奶有跌倒嗎"))
    assert len(lines) == 1
    assert lines[0].startswith("127.0.0.1 d-1 ask done ") and lines[0].endswith(" 秒")
    assert "王奶奶" not in lines[0] and "跌倒" not in lines[0]


def test_an_unexpected_error_in_the_handler_still_answers(api, monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("內部細節")

    monkeypatch.setattr(jetson_api, "handle", boom)
    server, lines = api()
    status, body, _ = _call(server, body=_job())
    assert (status, body) == (500, {"error": "處理時發生錯誤：RuntimeError"})
    assert "內部細節" not in "".join(lines)


@pytest.fixture
def cert(tmp_path):
    key, crt = tmp_path / "key.pem", tmp_path / "cert.pem"
    subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-keyout", str(key), "-out", str(crt),
                    "-days", "1", "-subj", "/CN=127.0.0.1", "-addext", "subjectAltName=IP:127.0.0.1"],
                   check=True, capture_output=True)
    return crt, key


def test_https_with_a_self_signed_certificate_pinned_by_the_client(api, cert):
    crt, key = cert
    server, _ = api(tls=jetson_api.tls_context(str(crt), str(key)))
    client = ssl.create_default_context(cafile=str(crt))
    status, body, _ = _call(server, body=_job(), context=client)
    assert (status, body["status"]) == (200, "done")
    with pytest.raises((ConnectionError, http.client.RemoteDisconnected, http.client.BadStatusLine)):
        _call(server, body=_job())  # 對 HTTPS 埠送明文 HTTP：不會被當成請求處理


def test_a_failed_tls_handshake_is_logged_and_the_server_keeps_going(api, cert):
    crt, key = cert
    server, lines = api(tls=jetson_api.tls_context(str(crt), str(key)))
    with pytest.raises(ssl.SSLError):
        _call(server, body=_job(), context=ssl.create_default_context())  # 不信任自簽憑證的用戶端
    client = ssl.create_default_context(cafile=str(crt))
    assert _call(server, body=_job(), context=client)[0] == 200
    assert any(line.startswith("127.0.0.1 TLS 交握失敗") for line in lines)


# ---- 命令列 ----

def _args(*extra):
    return ["--port", "0", *extra]


def test_main_needs_a_long_enough_token(monkeypatch, capsys):
    monkeypatch.delenv(jetson_api.TOKEN_ENV, raising=False)
    assert jetson_api.main(_args()) == 2
    assert jetson_api.TOKEN_ENV in capsys.readouterr().err
    monkeypatch.setenv(jetson_api.TOKEN_ENV, "short")
    assert jetson_api.main(_args()) == 2
    assert "至少 32 個字元" in capsys.readouterr().err


def test_main_refuses_to_listen_beyond_this_machine_without_tls_and_allow(monkeypatch, capsys, cert):
    monkeypatch.setenv(jetson_api.TOKEN_ENV, TOKEN)
    crt, key = cert
    assert jetson_api.main(_args("--host", "0.0.0.0", "--allow", "203.0.113.5")) == 2
    assert "--cert" in capsys.readouterr().err
    assert jetson_api.main(_args("--host", "0.0.0.0", "--cert", str(crt), "--key", str(key))) == 2
    assert "--allow" in capsys.readouterr().err
    assert jetson_api.main(_args("--cert", str(crt))) == 2  # 只給一半
    assert "--cert 與 --key" in capsys.readouterr().err


def test_main_reports_a_bad_certificate_or_model_argument(monkeypatch, capsys, tmp_path):
    monkeypatch.setenv(jetson_api.TOKEN_ENV, TOKEN)
    missing = str(tmp_path / "none.pem")
    assert jetson_api.main(_args("--cert", missing, "--key", missing)) == 2
    assert "憑證" in capsys.readouterr().err
    assert jetson_api.main(_args("--endpoint", "http://127.0.0.1:11434/v1")) == 2
    assert "--model" in capsys.readouterr().err


def test_build_listens_only_where_told_and_prints_no_token(monkeypatch, capsys):
    monkeypatch.setenv(jetson_api.TOKEN_ENV, TOKEN)
    server = jetson_api.build(jetson_api.parser().parse_args(_args("--echo")))
    try:
        assert server.server_address[0] == "127.0.0.1"
        assert [str(n) for n in server.allow] == [str(n) for n in jetson_api.LOCAL_NETS]
        assert server.tls is None and server.model is not None
    finally:
        server.server_close()
    assert TOKEN not in capsys.readouterr().err


def test_the_demo_client_gets_the_same_report(api, monkeypatch, capsys):
    import jetson_api_client

    server, _ = api()
    monkeypatch.setenv(jetson_api.TOKEN_ENV, TOKEN)
    base = f"http://127.0.0.1:{server.server_address[1]}"
    assert jetson_api_client.main(["--base", base]) == 0
    out = capsys.readouterr().out
    assert out.startswith("HTTP 200\n")
    assert json.loads(out.split("\n", 1)[1])["result"] == report_data(summarize(MORNING), place=PLACE)
    assert jetson_api_client.main(["--base", base, "--pick", "visits"]) == 0
    capsys.readouterr()
    monkeypatch.setenv(jetson_api.TOKEN_ENV, "wrong")
    assert jetson_api_client.main(["--base", base, "--ask", "早上有人跌倒嗎"]) == 1
    assert capsys.readouterr().out.startswith("HTTP 401\n")
