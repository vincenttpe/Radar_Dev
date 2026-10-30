"""Jetson 內部 Demo：在 Jetson 本機開原型頁，打字提問交給同一台 Jetson 上的在地模型（docs/21）。

頁面和 demo/build.py 產生的原型頁相同（模擬資料、句子都來自 radar-caregiver 與 radar-ask），
只多了兩件事：輸入框接上在地模型（模型只選題號，答案仍是固定句型並通過輸出檢查；比 radar-ask 多一題「交班重點」），
頁面角落的 Jetson 狀態（tegrastats 讀得到時才顯示），以及「沒用模型／用模型」的比對（測試版，2026-10-29 決定）：
事件摘要（rewrite.rewrite）與交班重點（rewrite.rewrite_handover）各自並排「規則產生的原句」與「在地模型改寫」；
沒通過檢查的模型輸出也列出並標「未採用」，方便評比模型實際寫了什麼、為什麼被擋。

    python demo/jetson_serve.py --endpoint http://127.0.0.1:11434/v1 --model gemma4:e4b --reasoning-effort none
    # 再用 Jetson 上的瀏覽器開 http://127.0.0.1:8765/#replay

- 只聽 127.0.0.1，同網段的其他電腦連不進來；不提供改聽其他網卡的參數。
- 模型只能用 --endpoint 接本機的伺服器（127.0.0.1、localhost、::1），不接受 --cmd：畫面寫「在這台 Jetson 上執行」，
  任意指令可能把提問轉送到別台機器（2026-10-29 決定）。--echo（不送出任何東西）留給試跑。
- 讀者打的原文不寫進 log、不存檔：log 只記方法、路徑與狀態碼。
- 只用模擬資料（data/samples_demo/），不接受實測資料：實測資料走 build.py --events（遮罩、不發布）。
- 把讀者打的字送進模型，屬於單向門「個人資料的流向」（REVIEW.md 第 2 節）。
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import ModuleType
from typing import Any

import demo_page  # demo/ 底下的共用外殼；直接執行或測試時 demo/ 都在 sys.path
from radar_agent.device_events import Summary, read_records, summarize
from radar_agent.ask import Question, match_question
from radar_agent.event_ask import EVENT_QUESTIONS, Reply, checked_answer
from radar_agent.local_model import add_model_args, model_from_args
from radar_agent.rewrite import Model, Rewritten, rewrite, rewrite_handover

HERE = Path(__file__).resolve().parent


def _load_build() -> ModuleType:
    """demo/build.py 照檔案路徑載入：名稱 build 會和 PyPI 的 build 套件撞名。"""
    spec = importlib.util.spec_from_file_location("demo_build", HERE / "build.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


build = _load_build()

HOST = "127.0.0.1"
PORT = 8765
# 提問本身限 100 字（ask.MAX_ASKED）；這裡只擋明顯不是提問的大量資料，避免把記憶體吃光
MAX_BODY = 4096
WARMUP_ASKED = "有人跌倒嗎"
COMPARE_KINDS = ("summary", "handover")  # 事件摘要、交班重點
# 提問多一題「交班重點（交接報告）」：只在 Jetson 內部 Demo（2026-10-30 決定），radar-ask 與 Dashboard 的 6 題不動。
# 「幫我寫一份交班紀錄」也對到這一題，答案開頭註明不是正式紀錄（2026-10-30 決定）。
# 題目或 ask.build_prompt 改了要升 DEMO_ASK_PROMPT_VERSION（tests/test_jetson_serve.py 會檢查）
HANDOVER_QUESTION = Question("handover", "幫我整理交班重點（交接報告）")
DEMO_QUESTIONS = [*EVENT_QUESTIONS, HANDOVER_QUESTION]
DEMO_ASK_PROMPT_VERSION = "jetson-demo-ask-v0"
HANDOVER_NOTE = "以下是交班重點，不是機構的正式交班紀錄。"
# 畫面寫「在這台 Jetson 上執行」，所以模型伺服器只能在本機（local_model 本身允許區網）
LOOPBACK = {"127.0.0.1", "localhost", "::1"}

# tegrastats 一行的格式依 JetPack 版本不同；以下依 JetPack 6 的格式寫，Demo 機台上待核對（docs/21）
_RAM = re.compile(r"\bRAM (\d+)/(\d+)MB")
_GPU = re.compile(r"\bGR3D_FREQ (\d+)%")
_TEMP = re.compile(r"\b(?:tj|gpu|GPU)@(-?[\d.]+)C")


def parse_tegrastats(line: str) -> dict[str, float] | None:
    """從 tegrastats 的一行取出記憶體、GPU 使用率與溫度；一項都讀不到時回傳 None。"""
    out: dict[str, float] = {}
    if m := _RAM.search(line):
        out["ram_used_mb"], out["ram_total_mb"] = float(m.group(1)), float(m.group(2))
    if m := _GPU.search(line):
        out["gpu_percent"] = float(m.group(1))
    if m := _TEMP.search(line):
        out["temp_c"] = float(m.group(1))
    return out or None


class Stats:
    """背景執行 tegrastats，保留最新一筆。找不到 tegrastats（例如不在 Jetson 上）就一直是 None。"""

    def __init__(self) -> None:
        self.latest: dict[str, float] | None = None
        self.proc: subprocess.Popen[str] | None = None

    def start(self) -> bool:
        exe = shutil.which("tegrastats")
        if exe is None:
            return False
        self.proc = subprocess.Popen([exe, "--interval", "2000"], stdout=subprocess.PIPE,
                                     stderr=subprocess.DEVNULL, text=True)
        threading.Thread(target=self._read, args=(self.proc,), daemon=True).start()
        return True

    def stop(self) -> None:
        if self.proc is not None:
            self.proc.terminate()

    def _read(self, proc: subprocess.Popen[str]) -> None:
        assert proc.stdout is not None
        for line in proc.stdout:
            self.latest = parse_tegrastats(line) or self.latest


class Recorder:
    """包住模型，記下它最後一次的原始輸出：測試版要把沒通過檢查的輸出也列出來（標「未採用」）。

    每次比對各包一個，多個請求同時進來也不會互相蓋掉。只記在記憶體、回給同一個請求，不寫進 log 或檔案。
    """

    def __init__(self, model: Model) -> None:
        self.model, self.name = model, model.name
        self.last: str | None = None

    def __call__(self, prompt: str) -> str:
        self.last = None
        self.last = self.model(prompt)
        return self.last


class Demo:
    """頁面與提問共用的狀態：模擬資料的摘要、模型、Jetson 狀態。"""

    def __init__(self, model: Model, *, stats: Stats | None = None) -> None:
        self.model = model
        self.place: str = build.PLACE
        self.summary: Summary = summarize(read_records(build.REPO / "data" / "samples_demo" / "bathroom_morning.jsonl"))
        self.stats = stats or Stats()
        data = build.demo_data()
        data["live"] = {
            "model": model.name, "stats": self.stats.proc is not None,
            # 比對用的原句先顯示，模型改寫好再補上右邊那欄，讀者不必乾等
            "compare": {kind: self._rewrite(kind, None).text for kind in COMPARE_KINDS},
        }
        self.page = demo_page.page(build.HERE / "template.html", build.PLACEHOLDER, data).encode("utf-8")

    def ask(self, asked: str) -> dict[str, Any]:
        """模型從 DEMO_QUESTIONS 選一題。前 6 題的答案和 radar-ask 相同；交班重點回原句（沒經過模型），
        頁面再另外叫 /api/compare/handover 拿模型改寫的版本並排比對。"""
        started = time.perf_counter()
        match = match_question(asked, DEMO_QUESTIONS, model=self.model, prompt_version=DEMO_ASK_PROMPT_VERSION)
        seconds = time.perf_counter() - started
        online = self.summary.online
        period = (f"{online[0].start:%Y-%m-%d %H:%M:%S}", f"{online[-1].end:%Y-%m-%d %H:%M:%S}") if online else None
        q = match.question
        if q is None or q.id == HANDOVER_QUESTION.id:
            data = Reply(match, None, seconds, period=period).data()
            if q is not None:
                data["answer"] = f"{HANDOVER_NOTE}\n{self._rewrite('handover', None).text}"
        else:
            text, problems = checked_answer(q, self.summary, place=self.place)
            data = Reply(match, text, seconds, problems, period).data()
        data["menu"] = [{"id": m.id, "text": m.text} for m in DEMO_QUESTIONS]
        return data

    def _rewrite(self, kind: str, model: Model | None) -> Rewritten:
        if kind == "summary":
            # 沒有收到資料時只有「無法判斷」一句，和交班重點一樣不交給模型
            return rewrite(self.summary, place=self.place, model=model if self.summary.online else None)
        return rewrite_handover(self.summary, place=self.place, model=model)

    def compare(self, kind: str) -> dict[str, Any]:
        """沒用模型與用模型的比對。沒通過檢查時 layer 是 template、text 是原句，rejected 寫原因，
        candidate 是模型實際寫的（測試版才給，頁面標「未採用」）；模型出錯或沒有呼叫模型時 candidate 是 None。"""
        recorder = Recorder(self.model)
        started = time.perf_counter()
        result = self._rewrite(kind, recorder)
        output = (recorder.last or "").strip()
        rejected_output = output if result.layer == "template" and output else None
        # 模型逾時、出錯或沒有輸出：不是檢查擋下的，頁面要分開寫（不靠比對退回原因的字串）
        no_reply = result.layer == "template" and bool(result.rejected) and not output
        return {
            "kind": kind, "text": result.text, "layer": result.layer, "model": result.model,
            "prompt_version": result.prompt_version, "seconds": round(time.perf_counter() - started, 2),
            "rejected": list(result.rejected), "candidate": rejected_output, "no_reply": no_reply,
        }

    def warm_up(self) -> str:
        """先問一題，讓模型載入記憶體；現場的第一題才不會多等十幾秒。回傳給終端機看的一行結果。"""
        started = time.perf_counter()
        data = self.ask(WARMUP_ASKED)
        seconds = time.perf_counter() - started
        if data["question"] is None:  # 例如 Ollama 沒開、模型名稱打錯：頁面仍能開，打字提問會改列選單
            return f"暖機失敗（{seconds:.1f} 秒，{self.model.name}）：{'、'.join(data['rejected'])}"
        return f"暖機完成：{seconds:.1f} 秒（{self.model.name}）"


def endpoint_is_local(endpoint: str) -> bool:
    """模型伺服器的網址是不是本機；網址寫壞（例如 http://[::1）也當作不是。"""
    try:
        return urllib.parse.urlsplit(endpoint).hostname in LOOPBACK
    except ValueError:
        return False


def make_handler(demo: Demo, port: int) -> type[BaseHTTPRequestHandler]:
    origins = {f"http://{HOST}:{port}", f"http://localhost:{port}"}

    class Handler(BaseHTTPRequestHandler):
        server_version = "radar-jetson-demo"

        def log_message(self, format: str, *args: Any) -> None:
            pass  # 預設的 log 會帶請求內容以外的細節；只用 log_request 記方法、路徑與狀態碼

        def log_request(self, code: int | str = "-", size: int | str = "-") -> None:
            path = self.path.split("?", 1)[0]
            if path == "/api/stats" and code == 200:
                return  # 頁面每 2 秒問一次，記下來只會蓋掉其他紀錄
            print(f"{time.strftime('%H:%M:%S')} {self.command} {path} {code}", file=sys.stderr)

        def _send(self, code: int, body: bytes, content_type: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, code: int, data: dict[str, Any]) -> None:
            self._send(code, json.dumps(data, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

        def do_GET(self) -> None:
            path = self.path.split("?", 1)[0]
            if path in ("/", "/index.html"):
                self._send(200, demo.page, "text/html; charset=utf-8")
            elif path == "/api/stats":
                self._json(200, {"stats": demo.stats.latest})
            else:
                self._json(404, {"error": "找不到這個網址"})

        def do_POST(self) -> None:
            path = self.path.split("?", 1)[0]
            kind = path.removeprefix("/api/compare/") if path.startswith("/api/compare/") else None
            if path != "/api/ask" and kind not in COMPARE_KINDS:
                self._json(404, {"error": "找不到這個網址"})
                return
            # 別的網頁（同一台機器的瀏覽器開著的其他網站）不能借這個服務送提問或叫模型改寫
            origin = self.headers.get("Origin")
            if origin is not None and origin not in origins:
                what = "提問" if path == "/api/ask" else "請求"
                self._json(403, {"error": f"只接受這個頁面送出的{what}"})
                return
            if kind in COMPARE_KINDS:  # 只用模擬資料的摘要，不讀請求內容
                try:
                    self._json(200, demo.compare(kind))
                except ValueError as e:  # 原句沒通過輸出檢查：不交出任何文字，告訴頁面原因
                    self._json(500, {"error": str(e)})
                return
            try:
                length = int(self.headers.get("Content-Length", ""))
            except ValueError:
                self._json(411, {"error": "缺少 Content-Length"})
                return
            if length > MAX_BODY:
                self._json(413, {"error": "提問太長"})
                return
            try:
                asked = json.loads(self.rfile.read(length).decode("utf-8"))["asked"]
            except (ValueError, KeyError, TypeError):
                self._json(400, {"error": "格式應為 {\"asked\": \"提問\"}"})
                return
            if not isinstance(asked, str):
                self._json(400, {"error": "asked 必須是文字"})
                return
            self._json(200, demo.ask(asked))

    return Handler


def serve(demo: Demo, port: int = PORT) -> ThreadingHTTPServer:
    """建立服務（還沒開始處理請求）。port 為 0 時由系統挑一個空的埠（測試用）。"""
    httpd = ThreadingHTTPServer((HOST, port), BaseHTTPRequestHandler)
    httpd.RequestHandlerClass = make_handler(demo, httpd.server_address[1])
    return httpd


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    add_model_args(ap, required=True)
    # 共用的 add_model_args 會加上 --cmd；本服務不接受（main 會拒絕），--help 也不列出來，免得看了才用、用了才被拒
    ap._option_string_actions["--cmd"].help = argparse.SUPPRESS
    ap.add_argument("--port", type=int, default=PORT, help=f"本機埠號，預設 {PORT}；只聽 {HOST}")
    ap.add_argument("--no-warmup", action="store_true", help="啟動時不先問一題暖機")
    ap.add_argument("--no-stats", action="store_true", help="不讀 tegrastats")
    args = ap.parse_args(sys.argv[1:] if argv is None else argv)
    if args.cmd is not None:
        print("錯誤：內部 Demo 不接受 --cmd；請用 --endpoint 接這台 Jetson 上的模型伺服器", file=sys.stderr)
        return 2
    if args.endpoint and not endpoint_is_local(args.endpoint):
        print(f"錯誤：--endpoint 只能是本機（{'、'.join(sorted(LOOPBACK))}）；內部 Demo 的模型要跑在這台 Jetson 上",
              file=sys.stderr)
        return 2
    try:
        model = model_from_args(args)
    except ValueError as e:
        print(f"錯誤：{e}", file=sys.stderr)
        return 2
    stats = Stats()
    if not args.no_stats and not stats.start():
        print("找不到 tegrastats，頁面不顯示 Jetson 狀態", file=sys.stderr)
    demo = Demo(model, stats=stats)
    httpd = serve(demo, args.port)
    print(f"開啟 http://{HOST}:{httpd.server_address[1]}/#replay （Ctrl+C 結束）", file=sys.stderr)
    if not args.no_warmup:
        # 先開埠再暖機：模型第一次載入要十幾秒，這段時間頁面也要打得開
        print("暖機中（模型載入記憶體）…", file=sys.stderr)
        threading.Thread(target=lambda: print(demo.warm_up(), file=sys.stderr), daemon=True).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
        stats.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
