"""示範 Dashboard 怎麼呼叫 Jetson API（docs/23「另一種方式」）：讀模擬上午的紀錄，連同一筆需求 POST 過去，印出回應。

內部試跑與給 Dashboard 負責人參考用，不是給 Dashboard 照抄的實作。token 從環境變數 RADAR_JETSON_API_TOKEN 讀。

    RADAR_JETSON_API_TOKEN=… python demo/jetson_api_client.py --base http://127.0.0.1:8443
    RADAR_JETSON_API_TOKEN=… python demo/jetson_api_client.py --base https://<Jetson 的 IP>:8443 --cafile cert.pem --ask 早上有人跌倒嗎
"""
from __future__ import annotations

import argparse
import json
import os
import ssl
import sys
import urllib.error
import urllib.request
from typing import Any

import page_data  # demo/ 底下的共用資料；直接執行或測試時 demo/ 都在 sys.path
from radar_agent.device_events import read_records
from radar_agent.jetson_api import JOBS_PATH, TOKEN_ENV
from radar_agent.time_range import in_range, parse_bound

START, END = "2026-10-14 08:00:00", "2026-10-14 12:00:00"


def job(kind: str = "report", *, question: str | None = None, question_id: str | None = None) -> dict[str, Any]:
    records = in_range(read_records(page_data.SAMPLE), parse_bound(START), parse_bound(END))
    body: dict[str, Any] = {"job_id": "demo-1", "kind": kind, "device": "DEMO-SIM-01", "place": page_data.PLACE,
                            "from": START, "to": END, "records": records,
                            "received_until": records[-1]["timestamp"] if records else None, "complete": True}
    if question is not None:
        body["question"] = question
    if question_id is not None:
        body["question_id"] = question_id
    return body


def call(base: str, token: str, body: dict[str, Any], *, cafile: str | None = None, timeout: float = 180) -> tuple[int, Any]:
    req = urllib.request.Request(base.rstrip("/") + JOBS_PATH, data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
                                 method="POST", headers={"Authorization": f"Bearer {token}",
                                                         "Content-Type": "application/json"})
    context = ssl.create_default_context(cafile=cafile) if cafile else None
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), urllib.request.HTTPSHandler(context=context))
    try:
        with opener.open(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"null")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--base", required=True, help="Jetson API 的網址，例如 http://127.0.0.1:8443")
    ap.add_argument("--cafile", help="Jetson 的自簽憑證（PEM）：只信任這一張")
    which = ap.add_mutually_exclusive_group()
    which.add_argument("--ask", help="打字提問")
    which.add_argument("--pick", help="在選單上點題的題號，例如 visits")
    args = ap.parse_args(sys.argv[1:] if argv is None else argv)
    kind = "ask" if args.ask is not None else "pick" if args.pick else "report"
    status, body = call(args.base, os.environ.get(TOKEN_ENV, ""), job(kind, question=args.ask, question_id=args.pick),
                        cafile=args.cafile)
    print(f"HTTP {status}")
    print(json.dumps(body, ensure_ascii=False, indent=1))
    return 0 if status == 200 else 1


if __name__ == "__main__":
    raise SystemExit(main())
