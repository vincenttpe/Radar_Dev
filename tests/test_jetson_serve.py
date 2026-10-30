"""Jetson 內部 Demo 的本機服務（demo/jetson_serve.py，docs/21）。

用假模型開真的 HTTP 服務（127.0.0.1、系統挑的空埠），從外面打 HTTP 請求檢查：
頁面就是原型頁加上 live 標記、提問的答案和 radar-ask --pick 相同、讀者打的字不進 log、
別的網頁送不進提問。
"""
import importlib.util
import json
import threading
import urllib.error
import urllib.request

import pytest

from radar_agent.event_ask import pick


@pytest.fixture(scope="module")
def serve_mod(repo):
    spec = importlib.util.spec_from_file_location("jetson_serve", repo / "demo" / "jetson_serve.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class PickFirst:
    """假模型：一律回第 1 題（有沒有人跌倒）。"""

    name = "fake-model"

    def __call__(self, prompt: str) -> str:
        return "1"


class Broken:
    name = "broken-model"

    def __call__(self, prompt: str) -> str:
        raise TimeoutError


@pytest.fixture
def server(serve_mod):
    started = []

    def start(model):
        demo = serve_mod.Demo(model)
        httpd = serve_mod.serve(demo, 0)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        started.append(httpd)
        host, port = httpd.server_address[:2]
        return demo, f"http://{host}:{port}"

    yield start
    for httpd in started:
        httpd.shutdown()
        httpd.server_close()


def _post(url, body, headers=None):
    req = urllib.request.Request(url + "/api/ask", data=body, method="POST",
                                 headers={"Content-Type": "application/json", **(headers or {})})
    try:
        with urllib.request.urlopen(req) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def _ask(url, asked, headers=None):
    return _post(url, json.dumps({"asked": asked}).encode("utf-8"), headers)


def test_only_listens_on_localhost(serve_mod):
    """實際綁定的位址是 loopback，不是 0.0.0.0 或對外網卡：同網段的電腦連不進來（docs/21「安全」）。"""
    httpd = serve_mod.serve(serve_mod.Demo(PickFirst()), 0)
    try:
        assert httpd.socket.getsockname()[0] == "127.0.0.1"
    finally:
        httpd.server_close()


def test_page_is_the_prototype_marked_live(server):
    _, url = server(PickFirst())
    with urllib.request.urlopen(url + "/") as resp:
        page = resp.read().decode("utf-8")
    assert '"live": {' in page and '"model": "fake-model"' in page
    assert "模擬資料" in page and "DEMO-SIM-01" in page


def test_typed_question_gets_the_same_answer_as_picking_it(server):
    demo, url = server(PickFirst())
    code, data = _ask(url, "有人跌到嗎")
    assert code == 200
    assert data["question"]["id"] == "falls" and data["confirm"] == "您是想問「有沒有人跌倒？」嗎？"
    assert data["answer"] == pick("falls", demo.summary, place=demo.place).answer
    assert data["model"] == "fake-model" and data["text_layer"] == "template"


def test_model_failure_falls_back_to_the_menu(server):
    _, url = server(Broken())
    code, data = _ask(url, "有人跌倒嗎")
    assert code == 200 and data["question"] is None and data["answer"] is None
    assert data["rejected"] == ["模型錯誤：TimeoutError"]


def test_typed_text_never_reaches_the_log(server, capsys):
    _, url = server(PickFirst())
    _ask(url, "王小明今天有跌倒嗎")
    err = capsys.readouterr().err
    assert "POST /api/ask 200" in err
    assert "王小明" not in err


def test_other_sites_cannot_send_questions(server):
    _, url = server(PickFirst())
    code, _ = _ask(url, "有人跌倒嗎", {"Origin": "http://evil.example"})
    assert code == 403
    code, _ = _ask(url, "有人跌倒嗎", {"Origin": url})
    assert code == 200


@pytest.mark.parametrize("body, code", [
    (b"not json", 400),
    (b'{"q": "x"}', 400),
    (b'{"asked": 3}', 400),
    (b'{"asked": "' + b"x" * 5000 + b'"}', 413),
])
def test_bad_requests_are_refused(server, body, code):
    _, url = server(PickFirst())
    assert _post(url, body)[0] == code


def test_unknown_paths_are_404(server):
    _, url = server(PickFirst())
    with pytest.raises(urllib.error.HTTPError) as e:
        urllib.request.urlopen(url + "/data/samples_demo/bathroom_morning.jsonl")
    assert e.value.code == 404
    req = urllib.request.Request(url + "/api/other", data=b"{}", method="POST")
    with pytest.raises(urllib.error.HTTPError) as e:
        urllib.request.urlopen(req)
    assert e.value.code == 404


def test_stats_are_empty_without_tegrastats(server):
    _, url = server(PickFirst())
    with urllib.request.urlopen(url + "/api/stats") as resp:
        assert json.loads(resp.read()) == {"stats": None}


def test_parse_tegrastats_jetpack6_line(serve_mod):
    line = ("10-28-2026 09:00:00 RAM 6234/15656MB (lfb 2x4MB) SWAP 0/7828MB (cached 0MB) "
            "CPU [12%@1984,5%@1984] GR3D_FREQ 37% cpu@48.5C soc2@45.1C tj@50.25C VDD_IN 9876mW/9876mW")
    assert serve_mod.parse_tegrastats(line) == {
        "ram_used_mb": 6234, "ram_total_mb": 15656, "gpu_percent": 37, "temp_c": 50.25,
    }
    assert serve_mod.parse_tegrastats("看不懂的一行") is None


def test_cli_refuses_chinese_models(serve_mod, capsys):
    assert serve_mod.main(["--endpoint", "http://127.0.0.1:9/v1", "--model", "qwen2.5-3b"]) == 2
    assert "docs/adr/0003" in capsys.readouterr().err


def test_warm_up_reports_success_and_failure(serve_mod):
    assert serve_mod.Demo(PickFirst()).warm_up().startswith("暖機完成：")
    failed = serve_mod.Demo(Broken()).warm_up()
    assert failed.startswith("暖機失敗") and "模型錯誤：TimeoutError" in failed


@pytest.mark.parametrize("endpoint, local", [
    ("http://127.0.0.1:11434/v1", True),
    ("http://localhost:11434/v1", True),
    ("http://LocalHost:11434/v1", True),
    ("http://[::1]:11434/v1", True),
    ("http://192.168.1.20:11434/v1", False),
    ("http://example.com/v1", False),
    ("http://127.0.0.1@evil.example/v1", False),  # 帳號欄位假裝是本機
    ("localhost:11434", False),                    # 沒寫協定，取不到主機
    ("http://[::1", False),                        # 網址寫壞
])
def test_endpoint_is_local(serve_mod, endpoint, local):
    assert serve_mod.endpoint_is_local(endpoint) is local


@pytest.mark.parametrize("endpoint", ["http://192.168.1.20:11434/v1", "http://[::1"])
def test_cli_refuses_endpoints_off_this_machine(serve_mod, capsys, endpoint):
    assert serve_mod.main(["--endpoint", endpoint, "--model", "gemma4:e4b"]) == 2
    assert "只能是本機" in capsys.readouterr().err


def test_help_does_not_offer_cmd(serve_mod, capsys):
    with pytest.raises(SystemExit):
        serve_mod.main(["--help"])
    out = capsys.readouterr().out
    assert "--endpoint" in out and "--echo" in out
    assert "--cmd" not in out


def test_cli_refuses_command_models(serve_mod, capsys):
    """任意指令可能把提問轉送到別台機器，畫面「在這台 Jetson 上執行」就不成立。"""
    assert serve_mod.main(["--cmd", "echo 1"]) == 2
    assert "不接受 --cmd" in capsys.readouterr().err


def test_page_does_not_claim_data_never_leaves(server):
    """「資料不出這台 Jetson」要等確認 Ollama 不記錄 prompt 才能寫（docs/21 待核對）。"""
    _, url = server(PickFirst())
    with urllib.request.urlopen(url + "/") as resp:
        assert "資料不出" not in resp.read().decode("utf-8")


def test_query_string_on_ask_is_ignored(server):
    _, url = server(PickFirst())
    req = urllib.request.Request(url + "/api/ask?from=menu", data=json.dumps({"asked": "有人跌倒嗎"}).encode("utf-8"),
                                 method="POST", headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req) as resp:
        assert json.loads(resp.read())["question"]["id"] == "falls"


def _compare(url, headers=None, kind="handover"):
    req = urllib.request.Request(url + f"/api/compare/{kind}", data=b"", method="POST", headers=headers or {})
    try:
        with urllib.request.urlopen(req) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def test_page_carries_both_templates_to_show_before_the_model_finishes(server):
    from radar_agent.caregiver import caregiver_text, handover_text

    demo, url = server(PickFirst())
    with urllib.request.urlopen(url + "/") as resp:
        page = resp.read().decode("utf-8")
    assert json.dumps(handover_text(demo.summary, place=demo.place), ensure_ascii=False) in page
    assert json.dumps(caregiver_text(demo.summary, place=demo.place), ensure_ascii=False) in page


def test_handover_that_fails_the_check_keeps_the_template_and_says_why(server):
    from radar_agent.caregiver import handover_text

    demo, url = server(PickFirst())  # 回「1」：數字、時間都漏掉
    code, data = _compare(url)
    assert code == 200 and data["layer"] == "template"
    assert data["text"] == handover_text(demo.summary, place=demo.place)
    assert data["model"] == "fake-model" and "漏掉時間 08:32" in data["rejected"]
    assert data["candidate"] == "1" and data["no_reply"] is False  # 測試版：模型實際寫的也回給頁面，標「未採用」


def test_handover_model_errors_keep_the_template(server):
    _, url = server(Broken())
    code, data = _compare(url)
    assert code == 200 and data["layer"] == "template" and data["rejected"] == ["模型錯誤：TimeoutError"]
    assert data["candidate"] is None and data["no_reply"] is True  # 模型出錯：沒有輸出可以列，頁面寫「沒有回應」


def test_other_sites_cannot_trigger_a_handover(server):
    _, url = server(PickFirst())
    assert _compare(url, {"Origin": "http://evil.example"})[0] == 403


def test_handover_whose_template_fails_its_check_answers_500_with_the_reason(server):
    demo, url = server(PickFirst())
    demo.place = "治療室"  # 「治療」是不能出現的用語：原句沒通過輸出檢查，不交出任何文字
    code, data = _compare(url)
    assert code == 500 and "範本沒通過輸出檢查" in data["error"]


def test_refused_ask_still_says_question(server):
    _, url = server(PickFirst())
    code, data = _ask(url, "有人跌倒嗎", {"Origin": "http://evil.example"})
    assert code == 403 and data["error"] == "只接受這個頁面送出的提問"


class Echoing:
    """假模型：照抄 prompt 裡「原文：」後面的文字（一定通過檢查）。"""

    name = "echoing"

    def __call__(self, prompt: str) -> str:
        return prompt.rsplit("原文：\n", 1)[-1]


def test_event_summary_can_be_compared_too(server):
    from radar_agent.caregiver import caregiver_text

    demo, url = server(Echoing())
    code, data = _compare(url, kind="summary")
    assert code == 200 and (data["kind"], data["layer"]) == ("summary", "model")
    assert data["text"] == caregiver_text(demo.summary, place=demo.place) and data["candidate"] is None


def test_unknown_comparison_is_404(server):
    _, url = server(PickFirst())
    assert _compare(url, kind="kg")[0] == 404


def test_rejected_event_summary_rewrite_is_returned_as_candidate(server):
    from radar_agent.caregiver import caregiver_text

    demo, url = server(PickFirst())  # 回「1」：數字都漏掉
    code, data = _compare(url, kind="summary")
    assert code == 200 and (data["layer"], data["candidate"]) == ("template", "1")
    assert data["text"] == caregiver_text(demo.summary, place=demo.place)


def test_other_sites_cannot_trigger_a_summary_comparison(server):
    _, url = server(PickFirst())
    assert _compare(url, {"Origin": "http://evil.example"}, kind="summary")[0] == 403


def test_comparison_paths_need_the_compare_prefix(server):
    _, url = server(PickFirst())
    req = urllib.request.Request(url + "/summary", data=b"", method="POST")
    with pytest.raises(urllib.error.HTTPError) as e:
        urllib.request.urlopen(req)
    assert e.value.code == 404


def test_event_summary_without_data_is_not_sent_to_the_model(server):
    from radar_agent.device_events import summarize

    demo, url = server(Broken())  # 被呼叫就丟例外
    demo.summary = summarize([])
    code, data = _compare(url, kind="summary")
    assert code == 200 and (data["layer"], data["rejected"], data["no_reply"]) == ("template", [], False)
    assert data["text"] == "1 號廁所：沒有收到資料，無法判斷。"


class Replies:
    """假模型：回固定文字。"""

    def __init__(self, reply: str) -> None:
        self.name, self.reply = "replies", reply

    def __call__(self, prompt: str) -> str:
        return self.reply


@pytest.mark.parametrize("reply", ["", "   \n "])
def test_empty_model_output_counts_as_no_reply(server, reply):
    _, url = server(Replies(reply))
    code, data = _compare(url)
    assert code == 200 and (data["layer"], data["candidate"], data["no_reply"]) == ("template", None, True)
    assert data["rejected"] == ["模型沒有輸出"]


def test_adopted_rewrite_is_not_no_reply(server):
    _, url = server(Echoing())
    code, data = _compare(url, kind="summary")
    assert code == 200 and (data["layer"], data["no_reply"]) == ("model", False)


class PickHandover:
    """假模型：一律回第 7 題（交班重點，只在內部 Demo）。"""

    name = "fake-model"

    def __call__(self, prompt: str) -> str:
        return "7"


def test_handover_question_answers_with_the_handover_original_and_a_note(serve_mod, server):
    from radar_agent.caregiver import handover_text

    demo, url = server(PickHandover())
    code, data = _ask(url, "交接報告")
    assert code == 200 and data["question"]["id"] == "handover"
    assert data["answer"] == f"{serve_mod.HANDOVER_NOTE}\n{handover_text(demo.summary, place=demo.place)}"
    assert data["prompt_version"] == serve_mod.DEMO_ASK_PROMPT_VERSION
    assert (data["schema"], data["question"]["text"]) == ("jetson-demo-ask-v0", "幫我整理交班重點")
    assert [m["id"] for m in data["menu"]][-1] == "handover" and len(data["menu"]) == 7


def test_radar_ask_keeps_its_six_questions(serve_mod):
    """交班重點只加在內部 Demo（2026-10-30 決定）：radar-ask 與 Dashboard 的題目不動。"""
    from radar_agent.event_ask import EVENT_QUESTIONS

    assert len(EVENT_QUESTIONS) == 6 and "handover" not in {q.id for q in EVENT_QUESTIONS}
    assert serve_mod.DEMO_QUESTIONS[:6] == EVENT_QUESTIONS


def test_demo_ask_prompt_version_is_bumped_when_the_prompt_or_menu_changes(serve_mod):
    """紀錄只記 prompt_version；Demo 的選單或提示詞改了卻沒升版，就分不出模型當時看到哪一版。"""
    import hashlib

    from radar_agent.ask import build_prompt

    digest = hashlib.sha256(build_prompt("{提問}", serve_mod.DEMO_QUESTIONS).encode("utf-8")).hexdigest()[:16]
    assert (serve_mod.DEMO_ASK_PROMPT_VERSION, digest) == ("jetson-demo-ask-v0", "fb0409607c9c42ac"), \
        "DEMO_QUESTIONS 或 ask.build_prompt 改了：升 DEMO_ASK_PROMPT_VERSION，再更新這裡的版本與雜湊"


def test_handover_question_whose_template_fails_its_check_shows_no_text_and_says_why(server):
    """和前 6 題一致：原句沒通過輸出檢查時不交出文字、寫出原因（不斷線、不回 500）。"""
    demo, url = server(PickHandover())
    demo.place = "治療室"  # 「治療」是不能出現的用語
    code, data = _ask(url, "交接報告")
    assert code == 200 and data["question"]["id"] == "handover" and data["answer"] is None
    assert any("範本沒通過輸出檢查" in r for r in data["rejected"])


def test_page_carries_the_handover_button_text_and_note(serve_mod, server):
    _, url = server(PickFirst())
    with urllib.request.urlopen(url + "/") as resp:
        page = resp.read().decode("utf-8")
    assert '"handover_question": "幫我整理交班重點"' in page
    assert json.dumps(serve_mod.HANDOVER_NOTE, ensure_ascii=False) in page
