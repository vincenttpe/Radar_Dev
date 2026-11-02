"""照 docs/23 API 草案寫的假 Dashboard：只聽 127.0.0.1，資料是模擬上午（data/samples_demo/），內部試跑與測試用。

讓 Jetson 端的連線程式（radar_agent.dashboard_link）在真實 Dashboard 約定之前，就能走一遍
「取需求 → 分頁取裝置事件 → 產生 → 傳回」。啟動時佇列裡有三筆需求（報告、提問、範圍比資料長的報告），
Jetson 傳回的結果印在終端機。不是給 Dashboard 照抄的實作，只示範草案的請求與回應長什麼樣子。

    python demo/fake_dashboard.py            # → http://127.0.0.1:8770，token 是 demo-token
"""
from __future__ import annotations

import argparse
import contextlib
import json
import threading
import urllib.parse
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import page_data  # demo/ 底下的共用資料；直接執行或測試時 demo/ 都在 sys.path
from radar_agent.device_events import HEARTBEAT_GAP, parse_timestamp, read_records
from radar_agent.time_range import in_range, parse_bound

HOST = "127.0.0.1"
PORT = 8770
TOKEN = "demo-token"
DEVICE = "DEMO-SIM-01"
SETTINGS = [{"since": "2026-10-14 00:00:00", "fallSwitch": True, "stillThreshold": 30}]  # 示意值，不是裝置真的設定
PAGE_SIZE = 500  # 一頁幾筆；模擬上午約 2,900 筆，會分成好幾頁，連線程式要接得起來
JOBS: list[dict[str, Any]] = [
    {"job_id": "j-1", "kind": "report", "device": DEVICE, "place": page_data.PLACE,
     "from": "2026-10-14 08:00:00", "to": "2026-10-14 12:00:00"},
    {"job_id": "j-2", "kind": "ask", "device": DEVICE, "place": page_data.PLACE,
     "from": "2026-10-14 08:00:00", "to": "2026-10-14 12:00:00", "question": "早上有人跌倒嗎"},
    # 要到 14:00、資料只到 11:59：傳回時 complete 是 false，畫面要寫「資料到幾點」
    {"job_id": "j-3", "kind": "report", "device": DEVICE, "place": page_data.PLACE,
     "from": "2026-10-14 11:00:00", "to": "2026-10-14 14:00:00"},
]


class FakeDashboard:
    """需求佇列、一台裝置的紀錄，以及收到的結果。"""

    def __init__(self, records: list[dict[str, Any]], jobs: list[dict[str, Any]], *, token: str = TOKEN,
                 page_size: int = PAGE_SIZE) -> None:
        self.records, self.token, self.page_size = records, token, page_size
        self.jobs: deque[dict[str, Any]] = deque(jobs)
        self.results: list[dict[str, Any]] = []
        self.lock = threading.Lock()
        stamps = [t for t in (parse_timestamp(r.get("timestamp")) for r in records) if t is not None]
        self.last = max(stamps) if stamps else None

    def next_job(self) -> dict[str, Any] | None:
        with self.lock:
            return self.jobs.popleft() if self.jobs else None

    def page(self, start: str, end: str, cursor: str | None) -> dict[str, Any]:
        kept = in_range(self.records, parse_bound(start), parse_bound(end))
        at = int(cursor or 0)
        nxt = at + self.page_size
        last = self.last
        return {"records": kept[at:nxt], "next_cursor": str(nxt) if nxt < len(kept) else None,
                "received_until": f"{last:%Y-%m-%d %H:%M:%S}" if last else None,
                # 最後一筆離結束時間不到一個心跳空檔，就當作這段時間的資料都到了
                "complete": last is not None and last + HEARTBEAT_GAP >= parse_bound(end),
                # 草案請 Dashboard 先準備的偵測設定（docs/23 ②）；這一版的 Jetson 還不讀，這裡只示範形狀
                "settings": SETTINGS}


def handler(fake: FakeDashboard, *, quiet: bool = False) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt: str, *args: Any) -> None:  # 終端機只印傳回的結果，不印每個請求
            pass

        def _send(self, code: int, body: Any = None) -> None:
            raw = b"" if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            if raw:
                self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def _authorized(self) -> bool:
            if self.headers.get("Authorization") != f"Bearer {fake.token}":
                self._send(401, {"error": "token 不對"})
                return False
            return True

        def do_GET(self) -> None:
            if not self._authorized():
                return
            url = urllib.parse.urlsplit(self.path)
            query = dict(urllib.parse.parse_qsl(url.query))
            if url.path == "/api/v1/jetson/jobs/next":
                job = fake.next_job()
                if job is None:
                    self._send(204)
                else:
                    self._send(200, job)
            elif url.path == f"/api/v1/devices/{DEVICE}/records":
                try:
                    self._send(200, fake.page(query["from"], query["to"], query.get("cursor")))
                except (KeyError, ValueError) as e:
                    self._send(400, {"error": str(e)})
            else:
                self._send(404, {"error": "沒有這個網址"})

        def do_POST(self) -> None:
            if not self._authorized():
                return
            length = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(length) or b"null")
            with fake.lock:
                fake.results.append(body)
            if not quiet:
                print(json.dumps(body, ensure_ascii=False, indent=1), flush=True)
            self._send(204)

    return Handler


def serve(fake: FakeDashboard, port: int = PORT, *, quiet: bool = False) -> ThreadingHTTPServer:
    return ThreadingHTTPServer((HOST, port), handler(fake, quiet=quiet))


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--port", type=int, default=PORT)
    args = ap.parse_args()
    httpd = serve(FakeDashboard(read_records(page_data.SAMPLE), JOBS), args.port)
    print(f"假 Dashboard：http://{HOST}:{args.port}（token：{TOKEN}；佇列 {len(JOBS)} 筆需求，Ctrl+C 結束）", flush=True)
    with contextlib.suppress(KeyboardInterrupt):
        httpd.serve_forever()
