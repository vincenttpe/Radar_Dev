"""Jetson 內部 Demo：在 Jetson 本機開原型頁，打字提問交給同一台 Jetson 上的在地模型（docs/21）。

頁面和 demo/build.py 產生的原型頁相同（模擬資料、句子都來自 radar-caregiver 與 radar-ask），
只多了兩件事：輸入框接上 event_ask.ask_events()（模型只選題號，答案仍是固定句型並通過輸出檢查），
以及頁面角落的 Jetson 狀態（tegrastats 讀得到時才顯示）。

    python demo/jetson_serve.py --endpoint http://127.0.0.1:11434/v1 --model gemma4:e4b --reasoning-effort none
    # 再用 Jetson 上的瀏覽器開 http://127.0.0.1:8765/#replay

- 只聽 127.0.0.1，同網段的其他電腦連不進來；不提供改聽其他網卡的參數。
- --endpoint 只接受本機（127.0.0.1、localhost、::1）：提問不送到區網上的其他機器。
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
from radar_agent.event_ask import ask_events
from radar_agent.local_model import add_model_args, model_from_args
from radar_agent.rewrite import Model

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


class Demo:
    """頁面與提問共用的狀態：模擬資料的摘要、模型、Jetson 狀態。"""

    def __init__(self, model: Model, *, stats: Stats | None = None) -> None:
        self.model = model
        self.place: str = build.PLACE
        self.summary: Summary = summarize(read_records(build.REPO / "data" / "samples_demo" / "bathroom_morning.jsonl"))
        self.stats = stats or Stats()
        data = build.demo_data()
        data["live"] = {"model": model.name, "stats": self.stats.proc is not None}
        self.page = demo_page.page(build.HERE / "template.html", build.PLACEHOLDER, data).encode("utf-8")

    def ask(self, asked: str) -> dict[str, Any]:
        return ask_events(asked, self.summary, place=self.place, model=self.model).data()

    def warm_up(self) -> str:
        """先問一題，讓模型載入記憶體；現場的第一題才不會多等十幾秒。回傳給終端機看的一行結果。"""
        started = time.perf_counter()
        data = self.ask(WARMUP_ASKED)
        seconds = time.perf_counter() - started
        if data["question"] is None:  # 例如 Ollama 沒開、模型名稱打錯：頁面仍能開，打字提問會改列選單
            return f"暖機失敗（{seconds:.1f} 秒，{self.model.name}）：{'、'.join(data['rejected'])}"
        return f"暖機完成：{seconds:.1f} 秒（{self.model.name}）"


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
            if self.path.split("?", 1)[0] != "/api/ask":
                self._json(404, {"error": "找不到這個網址"})
                return
            # 別的網頁（同一台機器的瀏覽器開著的其他網站）不能借這個服務送提問
            origin = self.headers.get("Origin")
            if origin is not None and origin not in origins:
                self._json(403, {"error": "只接受這個頁面送出的提問"})
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
    ap.add_argument("--port", type=int, default=PORT, help=f"本機埠號，預設 {PORT}；只聽 {HOST}")
    ap.add_argument("--no-warmup", action="store_true", help="啟動時不先問一題暖機")
    ap.add_argument("--no-stats", action="store_true", help="不讀 tegrastats")
    args = ap.parse_args(sys.argv[1:] if argv is None else argv)
    if args.endpoint and urllib.parse.urlsplit(args.endpoint).hostname not in LOOPBACK:
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
