"""Jetson 與 Dashboard 的連線：主動往外取需求、取裝置事件、傳回文字（docs/adr/0009 提議中，docs/23 API 草案 v0）。

Jetson 不開任何對外埠：每隔幾秒向 Dashboard 問一次有沒有需求，取走後再分頁取那段時間的裝置事件，
報告用 report_data、提問用 ask_events（在地模型只選題號），通過輸出檢查後把 JSON 傳回，Dashboard 照原文顯示。
網址與欄位名稱都還沒和 Dashboard 負責人約定，全部集中在 HttpDashboard；測試與本機試跑用 demo/fake_dashboard.py。

讀者打的提問原文只拿來選題號，不保存、不寫進 log；log 只記需求編號、種類、結果與秒數。
token 從環境變數讀，不進 repo、不寫進 log。改這裡屬單向門「個人資料的流向」。
"""
from __future__ import annotations

import argparse
import contextlib
import http.client
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

from radar_agent.caregiver import OutputRejected, report_data
from radar_agent.device_events import summarize
from radar_agent.event_ask import EVENT_QUESTIONS, ask_events, pick
from radar_agent.local_model import add_model_args, model_from_args
from radar_agent.rewrite import Model
from radar_agent.time_range import in_range, parse_bound

TOKEN_ENV = "RADAR_DASHBOARD_TOKEN"
POLL_SECONDS = 5.0  # 暫定（docs/23）：延遲以模型秒數為主，輪詢間隔不是瓶頸
# 第一階段：報告、打字提問（ask，在地模型選題號）、在選單上點題（pick，不經過模型）；趨勢要 14 天資料，之後再加
KINDS = ("report", "ask", "pick")
# 只有本機的假 Dashboard 可以用 http；其他一律 HTTPS（docs/adr/0009）
LOOPBACK = {"127.0.0.1", "localhost", "::1"}
MAX_PAGES = 1000  # 一段時間最多取幾頁：Dashboard 一直回同一個 next_cursor 時不會無限迴圈
RETRY_CODES = (408, 429)  # 4xx 裡只有逾時與太多請求值得再送；其他 4xx 是 Dashboard 明確不收
# 一筆結果最多補送幾次（每輪一次，間隔 POLL_SECONDS，約 1 分鐘）：Dashboard 一直異常時丟掉這筆、記下來，
# 讓後面的需求能繼續；那筆需求靠 Dashboard 的逾時重發再做一次（docs/23「要確認」）
MAX_RETRIES = 12
# 這個版本寫成的時間：系統時間比它早，多半是開機後還沒 NTP 校時（很多 Jetson 載板沒有 RTC 電池），
# 這時 TLS 會以「憑證還沒生效」失敗，先停下來講清楚
CLOCK_FLOOR = datetime(2026, 11, 1, tzinfo=UTC)


@dataclass(frozen=True)
class Job:
    """一筆需求（docs/23 ①）。question 只有 kind 為 ask 時才有，question_id 只有 kind 為 pick 時才有。"""

    job_id: str
    kind: str
    device: str
    place: str
    start: str
    end: str
    question: str | None = None
    question_id: str | None = None

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> Job:
        """欄位不齊、種類不認得、時間看不懂都丟 ValueError：不猜需求要什麼。"""
        missing = [k for k in ("job_id", "kind", "device", "place", "from", "to") if not data.get(k)]
        if missing:
            raise ValueError(f"需求缺少欄位：{'、'.join(missing)}")
        if data["kind"] not in KINDS:
            raise ValueError(f"不認得的需求種類：{data['kind']}")
        question = data.get("question")
        if question is not None and not isinstance(question, str):
            raise ValueError("提問不是文字")
        if data["kind"] == "ask" and not (question or "").strip():
            raise ValueError("提問需求沒有提問")
        question_id = data.get("question_id")
        if data["kind"] == "pick" and not (isinstance(question_id, str) and question_id in {q.id for q in EVENT_QUESTIONS}):
            raise ValueError("點題需求的題號不在選單裡")
        parse_bound(str(data["from"]))
        parse_bound(str(data["to"]))
        return cls(str(data["job_id"]), data["kind"], str(data["device"]), str(data["place"]),
                   str(data["from"]), str(data["to"]), question, question_id if data["kind"] == "pick" else None)


@dataclass
class Unsent:
    """還沒送到 Dashboard 的一筆結果，和已經補送過幾次。"""

    body: dict[str, Any]
    tries: int = 0


@dataclass(frozen=True)
class Fetched:
    """一段時間的裝置事件（docs/23 ②）：每一頁接起來的紀錄，和 Dashboard 收到哪個時間點為止。"""

    records: list[dict[str, Any]]
    received_until: str | None
    complete: bool


class RecordSource(Protocol):
    """handle() 取裝置事件的地方：輪詢時是 Dashboard，Dashboard 直接呼叫時是請求本身（jetson_api）。"""

    def records(self, device: str, start: str, end: str) -> Fetched: ...


class Dashboard(RecordSource, Protocol):
    """Jetson 看到的 Dashboard：正式是 HttpDashboard，測試可以換成記憶體裡的替身。"""

    def next_job(self) -> dict[str, Any] | None: ...

    def post_result(self, job_id: str, body: dict[str, Any]) -> None: ...


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """不跟轉址：轉址時 urllib 會把 Authorization 一起帶去新的位置，token 可能送到別的主機或 http。
    草案的三支 API 都不需要轉址，收到 3xx 就當成錯誤（HTTPError），下一輪再試。"""

    def redirect_request(self, *args: Any, **kwargs: Any) -> None:
        return None


class HttpDashboard:
    """照 docs/23 草案的 HTTPS 連線。網址或欄位名稱和 Dashboard 約定後有變，只改這個類別。"""

    def __init__(self, base: str, token: str, *, timeout: float = 30) -> None:
        parts = urllib.parse.urlsplit(base)
        local = parts.hostname in LOOPBACK
        if parts.scheme != "https" and not (parts.scheme == "http" and local):
            raise ValueError("Dashboard 的網址要用 HTTPS（只有本機的假 Dashboard 可以用 http）")
        if not token:
            raise ValueError(f"沒有 Dashboard 的 token：請設定環境變數 {TOKEN_ENV}")
        self.base, self.timeout = base.rstrip("/"), timeout
        self._headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
        # 本機的假 Dashboard 不走 proxy；真實的 Dashboard 照系統設定（機構網路可能要經 proxy）
        handlers: list[Any] = [_NoRedirect()] + ([urllib.request.ProxyHandler({})] if local else [])
        self._opener = urllib.request.build_opener(*handlers)

    def _call(self, method: str, path: str, body: dict[str, Any] | None = None, *, parse: bool = True) -> tuple[int, Any]:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
        headers = {**self._headers, **({"Content-Type": "application/json"} if data is not None else {})}
        req = urllib.request.Request(self.base + path, data=data, method=method, headers=headers)
        try:
            with self._opener.open(req, timeout=self.timeout) as resp:  # 3xx、4xx、5xx 丟 HTTPError（OSError）
                raw = resp.read()
                return resp.status, json.loads(raw) if raw and parse else None
        except http.client.HTTPException as e:  # 回應被截斷、狀態列壞掉：和連不上一樣，下一輪再試
            raise OSError(f"Dashboard 的回應不完整：{type(e).__name__}") from e

    def next_job(self) -> dict[str, Any] | None:
        status, data = self._call("GET", "/api/v1/jetson/jobs/next")
        return None if status == 204 else data

    def records(self, device: str, start: str, end: str) -> Fetched:
        records: list[dict[str, Any]] = []
        cursor: str | None = None
        for _ in range(MAX_PAGES):
            query = {"from": start, "to": end, **({"cursor": cursor} if cursor else {})}
            path = f"/api/v1/devices/{urllib.parse.quote(device, safe='')}/records?{urllib.parse.urlencode(query)}"
            _, page = self._call("GET", path)
            rows = page.get("records") if isinstance(page, dict) else None
            if not isinstance(rows, list) or not all(isinstance(r, dict) for r in rows):
                raise ValueError("裝置事件的格式不對：records 要是一筆一個物件的清單")
            records += rows
            cursor = page.get("next_cursor")
            if not cursor:
                # 只有真的 true 才算資料到齊：字串 "false" 之類一律當成沒到齊
                return Fetched(records, page.get("received_until"), page.get("complete") is True)
        raise ValueError(f"裝置事件超過 {MAX_PAGES} 頁還沒取完")

    def post_result(self, job_id: str, body: dict[str, Any]) -> None:
        # 收到 2xx 就算送到，不解析回應內容（docs/23 ③：成功回 204）
        self._call("POST", f"/api/v1/jetson/jobs/{urllib.parse.quote(job_id, safe='')}/result", body, parse=False)


def handle(data: dict[str, Any], dashboard: RecordSource, model: Model | None) -> dict[str, Any]:
    """一筆需求 → 要傳回的 JSON（docs/23 ③）。不丟例外：失敗也要傳回，Dashboard 才不會一直等。"""
    body: dict[str, Any] = {"job_id": str(data.get("job_id") or ""), "status": "failed", "received_until": None,
                            "complete": None, "result": None, "problems": []}
    try:
        job = Job.from_json(data)
        fetched = dashboard.records(job.device, job.start, job.end)
        body.update(received_until=fetched.received_until, complete=fetched.complete)
        # Dashboard 多給了範圍外的紀錄也不算進去：範圍含開始、不含結束，和 radar-caregiver --from --to 相同
        summary = summarize(in_range(fetched.records, parse_bound(job.start), parse_bound(job.end)))
    except (OSError, ValueError, KeyError, TypeError) as e:  # 需求寫錯、取資料失敗、資料格式不對
        body["problems"] = [f"{type(e).__name__}：{e}"]
        return body
    except Exception as e:  # 紀錄裡某個欄位的形狀不對等意料外的錯誤：一樣傳回，只寫錯誤種類
        body["problems"] = [f"整理裝置事件時發生錯誤：{type(e).__name__}"]
        return body
    try:
        if job.kind == "report":
            body.update(status="done", result=report_data(summary, place=job.place))
        elif job.kind == "pick":  # 讀者在選單上點題：不經過模型，直接回固定句型的答案
            body.update(status="done", result=pick(job.question_id or "", summary, place=job.place).data())
        else:
            body.update(status="done", result=ask_events(job.question or "", summary, place=job.place, model=model).data())
    except OutputRejected as e:  # 沒通過輸出檢查：不交出任何文字（radar-caregiver 也一樣）
        body.update(status="rejected", problems=list(e.problems))
    except Exception as e:  # 意料外的錯誤也傳回，不讓服務停掉、Dashboard 空等；只寫錯誤種類，不帶出提問原文
        body.update(status="failed", result=None, problems=[f"產生時發生錯誤：{type(e).__name__}"])
    return body


def printable(value: object, limit: int) -> str:
    """寫進 log 的外來值（需求編號、種類）：只留可見字元再截短，換行之類不能用來偽造 log 行。"""
    return "".join(c for c in str(value) if c.isprintable())[:limit]


def job_line(job_id: object, kind: object, status: str, seconds: float) -> str:
    """一筆需求處理完的 log：編號、種類、結果與秒數，不含提問原文（輪詢與 jetson_api 共用）。"""
    return f"{printable(job_id, 40)} {printable(kind, 20)} {status} {seconds:.1f} 秒"


def _send(dashboard: Dashboard, item: Unsent, unsent: list[Unsent], log: Callable[[str], None]) -> bool:
    """傳回一筆結果。連不上、5xx、408、429 放回 unsent 下一輪再送（只在記憶體），回 False；
    送到了、Dashboard 明確不收（其他 4xx）、或已補送 MAX_RETRIES 次，都回 True（後兩者丟掉並記下來），不擋住後面的需求。"""
    job_id = item.body["job_id"]  # 傳回結果用 Dashboard 給的原值；只有寫進 log 時才過濾、截短
    shown = printable(job_id, 40)
    try:
        dashboard.post_result(job_id, item.body)
        return True
    except urllib.error.HTTPError as e:
        if 400 <= e.code < 500 and e.code not in RETRY_CODES:  # Dashboard 明確不收（例如已逾時、查無此需求）：再送也沒用
            log(f"{shown} 結果 Dashboard 不收（HTTP {e.code}），丟掉")
            return True
        error: Exception = e
    except Exception as e:  # 連不上、回應不完整，或其他意料外的錯誤：這筆都留著下一輪再送，不會因此丟掉
        error = e
    if item.tries >= MAX_RETRIES:
        log(f"{shown} 結果補送 {MAX_RETRIES} 次還沒送到（{type(error).__name__}），丟掉；等 Dashboard 逾時重發")
        return True
    unsent.append(Unsent(item.body, item.tries + 1))
    log(f"{shown} 結果沒送出（{type(error).__name__}），下一輪再送")
    return False


def run_once(dashboard: Dashboard, model: Model | None, *, log: Callable[[str], None],
             unsent: list[Unsent] | None = None) -> bool:
    """先補送上一輪沒送出的結果，再處理一筆需求；沒有需求回 False。log 只記編號、種類、結果與秒數，不記提問原文。
    補送失敗時不取新需求，所以 unsent 最多只有一筆，不會越積越多。"""
    unsent = [] if unsent is None else unsent
    for item in [*unsent]:
        unsent.remove(item)
        if not _send(dashboard, item, unsent, log):
            return False  # Dashboard 還連不上：先不取新需求，等下一輪
    data = dashboard.next_job()
    if data is None:
        return False
    if not isinstance(data, dict):  # 不合草案：沒有編號可以傳回，記下來、略過；回 False 先等一下再問，不會空轉
        log(f"Dashboard 回的需求不是物件（{type(data).__name__}），略過")
        return False
    started = time.perf_counter()
    if not data.get("job_id"):
        # 沒有編號就傳不回去：只記下來。Dashboard 沒有需求時回 200 加 {} 而不是 204 也會走到這裡，
        # 回 False 先等一下再問，不會一直空轉
        log("收到沒有 job_id 的需求，無法傳回結果")
        return False
    body = handle(data, dashboard, model)
    _send(dashboard, Unsent(body), unsent, log)
    log(job_line(body["job_id"], data.get("kind"), body["status"], time.perf_counter() - started))
    return True


def run(dashboard: Dashboard, model: Model | None, *, interval: float = POLL_SECONDS,
        log: Callable[[str], None], sleep: Callable[[float], None] = time.sleep,
        keep_going: Callable[[], bool] = lambda: True) -> None:
    """一直輪詢：有需求就連著處理，沒有就等 interval 秒。連不上 Dashboard 時記下來、等一下再試，不停下來。"""
    unsent: list[Unsent] = []
    while keep_going():
        try:
            if run_once(dashboard, model, log=log, unsent=unsent):
                continue
        except OSError as e:  # 網路斷了、Dashboard 回錯誤碼、回應不完整
            log(f"連不上 Dashboard：{type(e).__name__}")
        except ValueError as e:  # 回應不是 JSON 或格式不對
            log(f"Dashboard 回應異常：{type(e).__name__}")
        except Exception as e:  # 其他意料外的錯誤（例如極深的巢狀 JSON 造成 RecursionError）：服務不停，只記種類
            log(f"意料外的錯誤：{type(e).__name__}，等一下再試")
        sleep(interval)


def log_stderr(line: str) -> None:
    with contextlib.suppress(OSError, ValueError):  # stderr 斷了（BrokenPipe）或被關閉（ValueError）也不讓服務停掉
        print(f"{time.strftime('%H:%M:%S')} {line}", file=sys.stderr, flush=True)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Jetson 主動向 Dashboard 取需求、產生文字後傳回（docs/23 草案，還沒和 Dashboard 約定）")
    ap.add_argument("--base", required=True, help="Dashboard 的網址（HTTPS；本機的假 Dashboard 可用 http://127.0.0.1:…）")
    ap.add_argument("--interval", type=float, default=POLL_SECONDS, help=f"沒有需求時隔幾秒再問，預設 {POLL_SECONDS:g}")
    ap.add_argument("--once", action="store_true",
                    help="把目前佇列裡的需求處理完就結束（試跑用；遇到沒有編號的需求或 Dashboard 連不上時也會提早結束）")
    add_model_args(ap, required=False)
    args = ap.parse_args(sys.argv[1:] if argv is None else argv)
    now = datetime.now(UTC)
    if now < CLOCK_FLOOR:
        print(f"錯誤：系統時間 {now:%Y-%m-%d %H:%M}（UTC）早於 {CLOCK_FLOOR:%Y-%m-%d}，看起來還沒校時；"
              "請先完成 NTP 校時（systemd 設 After=time-sync.target），否則 HTTPS 憑證會驗證失敗", file=sys.stderr)
        return 2
    try:
        model = model_from_args(args)
        dashboard = HttpDashboard(args.base, os.environ.get(TOKEN_ENV, ""))
        if args.once:
            unsent: list[Unsent] = []
            while run_once(dashboard, model, log=log_stderr, unsent=unsent):
                pass
            return 0 if not unsent else 1
    except (OSError, ValueError) as e:
        print(f"錯誤：{e}", file=sys.stderr)
        return 2
    except Exception as e:  # 試跑時遇到意料外的錯誤：結束並說明種類，不印內容
        print(f"錯誤：意料外的錯誤 {type(e).__name__}", file=sys.stderr)
        return 2
    run(dashboard, model, interval=args.interval, log=log_stderr)  # pragma: no cover  一直跑，由 Ctrl+C 或 systemd 停止
    return 0  # pragma: no cover


if __name__ == "__main__":
    raise SystemExit(main())
