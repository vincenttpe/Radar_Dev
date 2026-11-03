"""Dashboard 直接呼叫 Jetson 的 API（docs/adr/0011，docs/23「另一種方式」草案 v0）。

和 dashboard_link（Jetson 主動輪詢，docs/adr/0009）並存：Dashboard 把一筆需求連同那段時間的裝置事件
POST 給 Jetson，Jetson 用同一個 handle() 產生文字、通過輸出檢查，在同一個回應裡傳回 docs/23 ③ 的 JSON。

開到機器外面（--host 不是 127.0.0.1）時一律要：
- HTTPS（--cert、--key；自簽憑證也可以，由 Dashboard 釘選）；
- --allow 列出允許的來源 IP（Dashboard 的出口 IP），其他來源在 HTTP 之前就斷線；
- 環境變數的 token（至少 32 個字元），比對用 hmac.compare_digest。
一次只處理一筆（在地模型只有一張 GPU），忙的時候等 BUSY_WAIT 秒，還是忙就回 503。

讀者打的提問原文只拿來選題號，不保存、不寫進 log；log 只記來源 IP、需求編號、種類、結果與秒數。
token 不進 repo、不寫進 log。改這裡屬單向門「個人資料的流向」。
"""
from __future__ import annotations

import argparse
import contextlib
import hmac
import ipaddress
import json
import os
import ssl
import sys
import threading
import time
from collections.abc import Callable, Sequence
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from radar_agent.dashboard_link import Fetched, _log, handle
from radar_agent.local_model import add_model_args, model_from_args
from radar_agent.rewrite import Model

TOKEN_ENV = "RADAR_JETSON_API_TOKEN"
MIN_TOKEN = 32  # 字元數：例如 openssl rand -hex 32 產生的 64 個字元
HOST, PORT = "127.0.0.1", 8443
JOBS_PATH = "/api/v1/jobs"
HEALTH_PATH = "/api/v1/health"
MAX_BODY = 8 * 1024 * 1024  # 位元組：模擬上午 4 小時約 0.45 MB，留到約 2–3 天的量
MAX_RECORDS = 100_000
BUSY_WAIT = 30.0  # 秒：前一筆還在跑模型時，這一筆最多等多久
READ_TIMEOUT = 30.0  # 秒：TLS 交握、讀標頭與內容的逾時，慢慢送的連線不會一直佔住
LOCAL_NETS: tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...] = (
    ipaddress.ip_network("127.0.0.1/32"), ipaddress.ip_network("::1/128"))
MIN_PREFIX = {4: 16, 6: 48}  # --allow 的網段最大到 /16（IPv6 /48）：0.0.0.0/0 這種等於沒擋


def parse_allow(items: Sequence[str]) -> tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...]:
    """--allow 的值 → 網段。只收 IP 或 CIDR（不收主機名稱：DNS 換了就會放錯人）。"""
    nets = []
    for item in items:
        try:
            net = ipaddress.ip_network(item.strip(), strict=False)
        except ValueError:
            raise ValueError(f"看不懂的 --allow：{item}（要是 IP 或網段，例如 203.0.113.5 或 203.0.113.0/24）") from None
        if net.prefixlen < MIN_PREFIX[net.version]:
            raise ValueError(f"--allow 的網段太大：{net}（最大 /{MIN_PREFIX[net.version]}）")
        nets.append(net)
    return tuple(nets)


def tls_context(cert: str, key: str) -> ssl.SSLContext:
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    ctx.load_cert_chain(cert, key)
    return ctx


class _Given:
    """請求裡附的裝置事件，當成 handle() 看到的 Dashboard（只用到 records）。"""

    def __init__(self, data: dict[str, Any]) -> None:
        self.data = data

    def records(self, device: str, start: str, end: str) -> Fetched:
        rows = self.data.get("records")
        if not isinstance(rows, list) or not all(isinstance(r, dict) for r in rows):
            raise ValueError("裝置事件的格式不對：records 要是一筆一個物件的清單")
        if len(rows) > MAX_RECORDS:
            raise ValueError(f"裝置事件超過 {MAX_RECORDS} 筆，請縮短時間範圍")
        until = self.data.get("received_until")
        # 只有真的 true 才算資料到齊，和輪詢方式相同
        return Fetched(rows, until if isinstance(until, str) else None, self.data.get("complete") is True)

    def next_job(self) -> dict[str, Any] | None:  # pragma: no cover  這個方式沒有佇列
        raise NotImplementedError

    def post_result(self, job_id: str, body: dict[str, Any]) -> None:  # pragma: no cover  結果直接放在回應裡
        raise NotImplementedError


class JetsonApi(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address: tuple[str, int], *, token: str, model: Model | None,
                 allow: Sequence[ipaddress.IPv4Network | ipaddress.IPv6Network] = LOCAL_NETS,
                 tls: ssl.SSLContext | None = None, busy_wait: float = BUSY_WAIT, max_body: int = MAX_BODY,
                 log: Callable[[str], None] = _log) -> None:
        self.token, self.model, self.allow, self.tls = token.encode("utf-8"), model, tuple(allow), tls
        self.busy_wait, self.max_body, self.log = busy_wait, max_body, log
        self.busy = threading.Lock()
        super().__init__(address, _handler())

    def verify_request(self, request: Any, client_address: Any) -> bool:
        """不在 --allow 裡的來源：在 TLS 與 HTTP 之前就斷線，只記 IP。"""
        try:
            ip = ipaddress.ip_address(client_address[0])
        except ValueError:  # pragma: no cover  TCP 連線的來源一定是 IP
            return False
        if any(ip in net for net in self.allow):
            return True
        self.log(f"拒絕來自 {ip} 的連線（不在 --allow 裡）")
        return False

    def finish_request(self, request: Any, client_address: Any) -> None:
        """TLS 交握放在每條連線自己的執行緒裡做：慢慢交握的連線不會卡住其他連線。"""
        if self.tls is None:
            super().finish_request(request, client_address)
            return
        request.settimeout(READ_TIMEOUT)
        try:
            wrapped = self.tls.wrap_socket(request, server_side=True)
        except (ssl.SSLError, OSError) as e:
            self.log(f"{client_address[0]} TLS 交握失敗：{type(e).__name__}")
            return
        try:
            super().finish_request(wrapped, client_address)
        finally:
            with contextlib.suppress(OSError):
                wrapped.close()


def _handler() -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        server: JetsonApi
        server_version, sys_version = "radar-jetson-api", ""
        timeout = READ_TIMEOUT

        def log_message(self, format: str, *args: Any) -> None:  # 不用預設的 log：只記下面自己寫的那一行
            pass

        def _send(self, code: int, body: Any, headers: dict[str, str] | None = None) -> None:
            try:
                raw = json.dumps(body, ensure_ascii=False).encode("utf-8")
            except UnicodeEncodeError:  # 請求裡用 \udc80 這類跳脫寫出落單的代理字元、又被帶回回應：改用全 ASCII 的 JSON
                raw = json.dumps(body).encode("ascii")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Cache-Control", "no-store")
            for name, value in (headers or {}).items():
                self.send_header(name, value)
            self.end_headers()
            self.wfile.write(raw)

        def _length(self) -> int | None:
            value = self.headers.get("Content-Length")
            if value is None or not value.isdigit():
                return None
            return int(value)

        def _refuse(self, code: int, error: str, headers: dict[str, str] | None = None) -> None:
            """回錯誤前把不大的內容讀掉：沒讀完就關掉連線，對方可能只看到 connection reset，看不到原因。"""
            length = self._length()
            if length is not None and length <= self.server.max_body:
                with contextlib.suppress(OSError):
                    self.rfile.read(length)
            self._send(code, {"error": error}, headers)

        def _authorized(self) -> bool:
            given = self.headers.get("Authorization", "")
            scheme, _, token = given.partition(" ")
            if scheme == "Bearer" and hmac.compare_digest(token.encode("utf-8"), self.server.token):
                return True
            self._refuse(401, "token 不對", {"WWW-Authenticate": "Bearer"})
            return False

        def do_GET(self) -> None:
            if not self._authorized():
                return
            if self.path == HEALTH_PATH:
                self._send(200, {"status": "ok", "model": self.server.model is not None})
            elif self.path == JOBS_PATH:
                self._refuse(405, "這個網址只收 POST", {"Allow": "POST"})
            else:
                self._refuse(404, "沒有這個網址")

        def do_POST(self) -> None:
            if not self._authorized():
                return
            if self.path == HEALTH_PATH:
                self._refuse(405, "這個網址只收 GET", {"Allow": "GET"})
                return
            if self.path != JOBS_PATH:
                self._refuse(404, "沒有這個網址")
                return
            if self.headers.get_content_type() != "application/json":
                self._refuse(415, "Content-Type 要是 application/json")
                return
            if self.headers.get("Content-Length") is None:
                self._send(411, {"error": "要有 Content-Length"})
                return
            length = self._length()
            if length is None:
                self._send(400, {"error": "Content-Length 不對"})
                return
            if length > self.server.max_body:  # 不讀內容、直接關掉連線
                self.close_connection = True
                self._send(413, {"error": f"內容超過 {self.server.max_body} 位元組"}, {"Connection": "close"})
                return
            try:
                data = json.loads(self.rfile.read(length).decode("utf-8"))  # 一定要是正確的 UTF-8（json 自己會放過代理字元）
            except (ValueError, RecursionError):  # 不是 JSON、不是 UTF-8、巢狀太深
                self._send(400, {"error": "內容不是 JSON"})
                return
            if not isinstance(data, dict):
                self._send(400, {"error": "內容要是一個 JSON 物件"})
                return
            self._job(data)

        def _job(self, data: dict[str, Any]) -> None:
            server = self.server
            if not server.busy.acquire(timeout=server.busy_wait):
                self._send(503, {"error": "正在處理另一筆需求，請稍後再送"}, {"Retry-After": "5"})
                return
            started = time.perf_counter()
            try:
                body = handle(data, _Given(data), server.model)
            except Exception as e:  # handle() 本身不丟例外；真的丟了也回應，只寫錯誤種類
                self._send(500, {"error": f"處理時發生錯誤：{type(e).__name__}"})
                server.log(f"{self.client_address[0]} 處理時發生錯誤：{type(e).__name__}")
                return
            finally:
                server.busy.release()
            self._send(200, body)
            job_id, kind = str(body["job_id"])[:40], str(data.get("kind"))[:20]  # Dashboard 給的原值，只截短，不是提問原文
            server.log(f"{self.client_address[0]} {job_id} {kind} {body['status']} {time.perf_counter() - started:.1f} 秒")

    return Handler


def parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="Dashboard 直接呼叫的 Jetson API（docs/23 草案，還沒和 Dashboard 約定）")
    ap.add_argument("--host", default=HOST, help=f"聽哪個位址，預設 {HOST}（只有這台機器連得到）；開到外面要給 --cert、--key、--allow")
    ap.add_argument("--port", type=int, default=PORT, help=f"預設 {PORT}")
    ap.add_argument("--allow", action="append", default=[],
                    help="允許的來源 IP 或網段（Dashboard 的出口 IP），可重複；本機（127.0.0.1、::1）一律允許")
    ap.add_argument("--cert", help="HTTPS 憑證（PEM）")
    ap.add_argument("--key", help="HTTPS 私鑰（PEM）")
    add_model_args(ap, required=False)
    return ap


def build(args: argparse.Namespace) -> JetsonApi:
    """依命令列參數建立伺服器（還沒開始服務）。參數不合丟 ValueError，憑證讀不到丟 OSError。"""
    token = os.environ.get(TOKEN_ENV, "")
    if not token:
        raise ValueError(f"沒有 token：請設定環境變數 {TOKEN_ENV}")
    if len(token) < MIN_TOKEN:
        raise ValueError(f"token 太短：至少 {MIN_TOKEN} 個字元（例如 openssl rand -hex 32）")
    if bool(args.cert) != bool(args.key):
        raise ValueError("--cert 與 --key 要一起給")
    local = args.host in {"127.0.0.1", "localhost", "::1"}
    if not local and not args.cert:
        raise ValueError("開到這台機器外面（--host 不是 127.0.0.1）一定要 HTTPS：請給 --cert 與 --key")
    if not local and not args.allow:
        raise ValueError("開到這台機器外面一定要用 --allow 列出允許的來源 IP（Dashboard 的出口 IP）")
    allow = LOCAL_NETS + parse_allow(args.allow)
    model = model_from_args(args)
    try:
        tls = tls_context(args.cert, args.key) if args.cert else None
    except (OSError, ssl.SSLError) as e:
        raise ValueError(f"讀不到 HTTPS 憑證或私鑰：{type(e).__name__}") from None
    server = JetsonApi((args.host, args.port), token=token, model=model, allow=allow, tls=tls)
    scheme = "https" if tls else "http"
    name = getattr(model, "name", None) or ("有" if model else "沒有（打字提問會回「目前無法用打字提問」）")
    _log(f"Jetson API：{scheme}://{args.host}:{server.server_address[1]}{JOBS_PATH}；允許來源 "
         f"{'、'.join(str(n) for n in allow)}；在地模型 {name}")
    return server


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(sys.argv[1:] if argv is None else argv)
    try:
        server = build(args)
    except (OSError, ValueError) as e:
        print(f"錯誤：{e}", file=sys.stderr)
        return 2
    with contextlib.suppress(KeyboardInterrupt), server:  # pragma: no cover  一直跑，由 Ctrl+C 或 systemd 停止
        server.serve_forever()
    return 0  # pragma: no cover


if __name__ == "__main__":
    raise SystemExit(main())
