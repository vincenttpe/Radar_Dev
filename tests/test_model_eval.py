"""在地模型評估（radar_agent.model_eval，docs/06「實測步驟」）。

模型以假模型、本機 HTTP 伺服器與子行程代替；題組、範本、檢查都用真的。
"""
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from itertools import count
from typing import ClassVar

import pytest

from radar_agent.caregiver import caregiver_text, check_output
from radar_agent.device_events import Summary
from radar_agent.model_eval import (
    ASK_CASES,
    ASK_MENU,
    PLACE,
    CommandModel,
    EchoModel,
    HttpChatModel,
    _reason_kind,
    all_cases,
    evaluate,
    evaluate_asks,
    main,
    report_asks,
    report_text,
)


def ticking(step: float):
    """每呼叫一次就前進 step 秒：一題（開始、結束各呼叫一次）剛好量到 step 秒。"""
    c = count()
    return lambda: next(c) * step


def test_cases_are_unique_and_every_template_passes_the_output_check():
    cases = all_cases()
    assert len(cases) == 20 and len({c.id for c in cases}) == 20
    for c in cases:
        place = PLACE if isinstance(c.source, Summary) else ""
        text = caregiver_text(c.source, place=place)
        assert check_output(text, c.source, place=place) == [], c.id
    texts = {c.id: caregiver_text(c.source, place=PLACE if isinstance(c.source, Summary) else "") for c in cases}
    assert "無法判斷是否有跌倒" in texts["E6"] and "主動呼救 1 次" in texts["E4"]
    assert "無法評估" in texts["K7"] and "無法評估" in texts["K9"] and "「尚好」" in texts["K1"]


def test_echo_model_is_adopted_everywhere():
    run = evaluate(EchoModel(), all_cases())
    assert all(r.out.layer == "model" for r in run.results)
    text = report_text(run)
    assert text.startswith("模型：echo　題數：20　採用 20、退回範本 0")
    assert "採用率 100%" in text and "延遲太短（假模型）" in text and "退回原因" not in text


class Padding:
    """在原文後面加一句含數字的話：每題都會被擋下。"""

    name = "padding-0.1"

    def __call__(self, prompt: str) -> str:
        return EchoModel()(prompt) + "另外提醒：每 3 小時巡視一次。"


def test_rejections_are_listed_and_counted_and_throughput_is_estimated():
    run = evaluate(Padding(), all_cases()[:3], clock=ticking(2.0))
    assert all(r.out.layer == "template" for r in run.results)
    text = report_text(run)
    assert "[退回] E1 一般使用、全程在線（2.00 秒）" in text
    assert "    原因：" in text and "退回原因：" in text
    assert "採用率 0%；每題延遲 中位數 2.00 秒、最長 2.00 秒" in text
    assert "一小時約可改寫 1,800 份" in text


def test_show_prints_adopted_output():
    run = evaluate(EchoModel(), [c for c in all_cases() if c.id == "K1"])
    assert "    建議：下肢肌力良好" in report_text(run, show=True)
    assert "    建議：" not in report_text(run)


@pytest.mark.parametrize("reason,kind", [
    ("漏掉範本裡的數字 3", "漏掉數字"), ("長度 300 字超過範本的 2 倍", "太長"),
    ("模型錯誤：TimeoutError", "模型錯誤"), ("數字 3 不在事實裡", "多出事實沒有的數字"),
    ("不能出現「治療」（非醫療器材）", "用了不能用的字"), ("不能寫「誤報」（C2）", "用了不能用的字"),
    ("漏掉「無法判斷」", "漏掉「無法判斷」"),
])
def test_reason_kinds(reason, kind):
    assert _reason_kind(reason) == kind


class _Handler(BaseHTTPRequestHandler):
    reply = ""
    status = 200
    seen: ClassVar[list] = []

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        _Handler.seen.append((self.path, body))
        prompt = body["messages"][0]["content"]
        content = _Handler.reply or prompt.rsplit("原文：\n", 1)[-1]
        data = json.dumps({"choices": [{"message": {"content": content}}], "usage": {"completion_tokens": 40}}).encode()
        self.send_response(_Handler.status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):
        pass


@pytest.fixture
def server():
    _Handler.reply, _Handler.status, _Handler.seen = "", 200, []
    httpd = HTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_port}/v1/"
    httpd.shutdown()


def test_openai_compatible_endpoint_with_token_rate(server, monkeypatch):
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:9")   # 不能走 proxy
    model = HttpChatModel(server, "qwen-test-q4")
    run = evaluate(model, all_cases()[:2], clock=ticking(4.0))
    assert [r.out.layer for r in run.results] == ["model", "model"]
    assert [r.tokens for r in run.results] == [40, 40]
    path, body = _Handler.seen[0]
    assert path == "/v1/chat/completions" and body["model"] == "qwen-test-q4" and body["temperature"] == 0
    text = report_text(run)
    assert text.startswith("模型：qwen-test-q4") and "（4.00 秒，40 token）" in text
    assert "輸出速度 約 10.0 token／秒" in text


def test_server_errors_fall_back_to_the_template(server):
    _Handler.status = 500
    run = evaluate(HttpChatModel(server, "broken"), all_cases()[:1])
    assert run.results[0].out.layer == "template"
    assert run.results[0].out.rejected == ("模型錯誤：HTTPError",)


def test_command_model(tmp_path):
    script = tmp_path / "echo_model.py"
    script.write_text("import sys\nprint(sys.stdin.read().rsplit('原文：\\n', 1)[-1])\n", encoding="utf-8")
    run = evaluate(CommandModel(f"{sys.executable} {script}"), all_cases()[:2])
    assert [r.out.layer for r in run.results] == ["model", "model"]
    bad = evaluate(CommandModel(f"{sys.executable} -c 'raise SystemExit(3)'"), all_cases()[:1])
    assert bad.results[0].out.rejected == ("模型錯誤：CalledProcessError",)


def test_cli(server, capsys):
    assert main(["--echo", "--only", "E2,K7"]) == 0
    out = capsys.readouterr().out
    assert "題數：2" in out and "[採用] E2" in out and "[採用] K7" in out
    assert main(["--endpoint", server, "--model", "m", "--only", "E1", "--show"]) == 0
    assert "1 號廁所 10/14" in capsys.readouterr().out
    assert main(["--cmd", f"{sys.executable} -c 'print(1)'", "--only", "E11"]) == 0
    assert "[退回] E11" in capsys.readouterr().out
    assert main(["--endpoint", server]) == 2
    assert "--model" in capsys.readouterr().err
    assert main(["--echo", "--only", "E1,X9"]) == 2
    assert "X9" in capsys.readouterr().err


# ── 提問對應（ask.match_question）────────────────────────



class Oracle:
    """照題組的標準答案回答：每題都對。"""

    name = "oracle"

    def __call__(self, prompt: str) -> str:
        asked = prompt.rsplit("使用者的提問：", 1)[-1]
        expected = next(c.expected for c in ASK_CASES if c.asked == asked)
        ids = [q.id for q in ASK_MENU]
        return str(ids.index(expected) + 1) if expected else "0"


class AlwaysFirst:
    name = "always-1"

    def __call__(self, prompt: str) -> str:
        return "1"


def test_ask_cases_point_at_menu_questions_and_include_off_topic_ones():
    ids = {q.id for q in ASK_MENU}
    assert len({c.id for c in ASK_CASES}) == len(ASK_CASES)
    assert all(c.expected is None or c.expected in ids for c in ASK_CASES)
    assert {c.expected for c in ASK_CASES} == ids | {None}, "每一題、以及「不該對應」都要有題目"


def test_perfect_model_scores_every_ask():
    text = report_asks(evaluate_asks(Oracle(), ASK_CASES, clock=ticking(0.5)))
    assert text.startswith(f"模型：oracle　題數：{len(ASK_CASES)}　正確 {len(ASK_CASES)}、對錯題 0、沒對應到 0")
    assert "[對錯題]" not in text


def test_wrong_question_is_counted_separately_from_no_match():
    """對錯題比沒對應到嚴重：讀者若沒看清楚就按確認，會拿到別題的答案。"""
    run = evaluate_asks(AlwaysFirst(), ASK_CASES, clock=ticking(0.5))
    first = ASK_MENU[0].id
    wrong = sum(1 for c in ASK_CASES if c.expected != first)
    text = report_asks(run)
    assert f"正確 {len(ASK_CASES) - wrong}、對錯題 {wrong}、沒對應到 0" in text
    assert "[對錯題]" in text


def test_cli_questions(capsys):
    assert main(["--echo", "--questions"]) == 0
    out = capsys.readouterr().out
    assert out.startswith(f"模型：echo　題數：{len(ASK_CASES)}　正確 ")
    assert "模型回答不是單一編號" in out
