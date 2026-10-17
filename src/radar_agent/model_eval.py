"""在地模型評估：用固定題組跑 rewrite()，量採用率、延遲與 token 速度（docs/06「實測步驟」）。

題組全部由程式產生（裝置事件的各種狀況、知識圖譜各指標的等級與缺口），不含任何真實資料。
每一題都走正式的 rewrite()：權限、範本、輸出檢查、退回範本與正式產生報告時完全一樣，
所以這裡量到的「採用率」就是上線後模型輸出會被用上的比例。

radar-model-eval --echo
    照抄原文的假模型，給 CI 與第一次試跑用（應該全部採用）。
radar-model-eval --endpoint http://127.0.0.1:8080/v1 --model qwen2.5-7b-instruct-q4_k_m
    OpenAI 相容的 chat completions（llama.cpp server、Ollama 等）。只連本機或區網，不經過 proxy。
radar-model-eval --cmd "某個指令"
    任意指令，prompt 從標準輸入送進去，標準輸出當成模型的回答。

選型時對每個候選模型各跑一次，比較採用率、延遲與退回原因（會議清單 B1、F2）。
"""
from __future__ import annotations

import argparse
import json
import shlex
import statistics
import subprocess
import sys
import time
import urllib.request
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from radar_agent import kg_rules
from radar_agent.device_events import Summary, summarize
from radar_agent.kg_rules import Assessment
from radar_agent.rewrite import Rewritten, rewrite

PLACE = "1 號廁所"
DAY = "2026-10-14"
STEP = timedelta(seconds=5)
MIN_TIMED = 0.01   # 平均延遲短於這個秒數就不估算每小時改寫量（假模型）


# ── 題組 ────────────────────────────────────────────────

def _t(hms: str) -> datetime:
    return datetime.strptime(f"{DAY} {hms}", "%Y-%m-%d %H:%M:%S")


def _records(
    start: str, end: str, *, visits: tuple[tuple[str, str], ...] = (), events: dict[str, dict[str, Any]] | None = None,
    gaps: tuple[tuple[str, str], ...] = (),
) -> list[dict[str, Any]]:
    """start～end 每 5 秒一筆；visits 時段內送 presence／moving，其他送 absence；gaps 時段內沒有資料。"""
    out: list[dict[str, Any]] = []
    now = _t(start)
    while now <= _t(end):
        hms = f"{now:%H:%M:%S}"
        if not any(_t(a) <= now < _t(b) for a, b in gaps):
            state = "absence"
            for a, b in visits:
                if now == _t(a):
                    state = "presence"
                elif _t(a) < now < _t(b):
                    state = "moving"
            out.append(_record(hms, {"mode": "radar", "event": state, "trigger": "track", "userReply": False}))
        if events and hms in events:
            out.append(_record(hms, events[hms]))
        now += STEP
    return out


def _record(hms: str, payload: dict[str, Any]) -> dict[str, Any]:
    return {"timestamp": f"{DAY} {hms}", "topic": "radar10/EVAL-SIM-01", "resolution": None, "payload": payload}


FALL = {"event": "fall", "confidence": 100}


@dataclass(frozen=True)
class Case:
    id: str
    title: str
    source: Summary | Assessment


def event_cases() -> list[Case]:
    """裝置事件的各種狀況（場域摘要）。"""
    specs: list[tuple[str, str, list[dict[str, Any]]]] = [
        ("E1", "一般使用、全程在線", _records("09:00:00", "09:30:00", visits=(("09:05:00", "09:12:00"),))),
        ("E2", "疑似跌倒、回覆沒事", _records("09:00:00", "09:30:00", visits=(("09:05:00", "09:12:00"),), events={
            "09:06:00": {**FALL, "mode": "radar", "trigger": "timeout", "delaysec": 10},
            "09:06:30": {"event": "resolved", "mode": "speech", "trigger": "noNeedHelp", "userReply": True}})),
        ("E3", "回覆需要協助、起身", _records("09:00:00", "09:30:00", visits=(("09:05:00", "09:15:00"),), events={
            "09:06:00": {**FALL, "mode": "fusion", "trigger": "conversation", "userReply": True},
            "09:09:00": {"event": "resolved", "mode": "radar", "trigger": "standUp"}})),
        ("E4", "主動呼救", _records("09:00:00", "09:30:00", visits=(("09:05:00", "09:12:00"),), events={
            "09:06:00": {**FALL, "mode": "speech", "trigger": "conversation", "userReply": True},
            "09:08:00": {"event": "resolved", "mode": "radar", "trigger": "standUp"}})),
        ("E5", "久待警報", _records("09:00:00", "09:40:00", visits=(("09:05:00", "09:25:00"),), events={
            "09:15:00": {"event": "longstay", "mode": "radar", "trigger": "timeout", "delaysec": 600}})),
        ("E6", "裝置斷線一段", _records("09:00:00", "09:40:00", visits=(("09:05:00", "09:10:00"),),
                                       gaps=(("09:20:00", "09:30:00"),))),
        ("E7", "使用途中斷線", _records("09:00:00", "09:40:00", visits=(("09:05:00", "09:25:00"),),
                                       gaps=(("09:10:00", "09:20:00"),))),
        ("E8", "資料結束時仍有人", _records("09:00:00", "09:20:00", visits=(("09:15:00", "09:30:00"),))),
        ("E9", "完全沒有人使用", _records("09:00:00", "09:20:00")),
        ("E10", "跌倒與久待都有、還斷線", _records("09:00:00", "10:00:00",
                                               visits=(("09:05:00", "09:10:00"), ("09:30:00", "09:50:00")), events={
            "09:06:00": {**FALL, "mode": "radar", "trigger": "timeout", "delaysec": 10},
            "09:07:00": {"event": "resolved", "mode": "radar", "trigger": "standUp"},
            "09:45:00": {"event": "longstay", "mode": "radar", "trigger": "timeout", "delaysec": 600}},
            gaps=(("09:15:00", "09:25:00"),))),
        ("E11", "沒有收到資料", []),
    ]
    return [Case(i, title, summarize(rs)) for i, title, rs in specs]


# 指標、量測值與預期的判定（assessed：有等級；其餘：無法評估）
KG_SPECS: list[tuple[str, str, str, object]] = [
    ("K1", "下肢肌力 尚好", "下肢肌力", 15),
    ("K2", "下肢肌力 最低一級", "下肢肌力", 3),
    ("K3", "單腳站立 普通", "單腳站立", 6),
    ("K4", "來回行走 很好", "來回行走", 5.0),
    ("K5", "來回行走 稍差", "來回行走", 7.0),
    ("K6", "心肺耐力 普通", "心肺耐力", 80),
    ("K7", "心肺耐力 門檻缺口（無法評估）", "心肺耐力", 105),
    ("K8", "步行速度 尚好", "步行速度", 1.2),
    ("K9", "步行速度 邊界重疊（無法評估）", "步行速度", 0.8),
]


def kg_cases(kg: kg_rules.KnowledgeGraph | None = None) -> list[Case]:
    """知識圖譜等級與建議（個人資料，給居服員看）。"""
    kg = kg or kg_rules.load()
    return [Case(i, title, kg_rules.assess(kg, metric, value)) for i, title, metric, value in KG_SPECS]


def all_cases() -> list[Case]:
    return event_cases() + kg_cases()


# ── 模型 ────────────────────────────────────────────────

class EchoModel:
    """照抄 prompt 裡的原文：檢查一定通過，用來確認流程與量測沒問題。"""

    name = "echo"

    def __call__(self, prompt: str) -> str:
        return prompt.rsplit("原文：\n", 1)[-1]


class HttpChatModel:
    """OpenAI 相容的 /chat/completions。只給本機或區網的模型伺服器用，刻意不走 proxy。"""

    def __init__(self, endpoint: str, model: str, *, timeout: float = 120, max_tokens: int = 512):
        self.url = endpoint.rstrip("/") + "/chat/completions"
        self.model, self.timeout, self.max_tokens = model, timeout, max_tokens
        self.name = model
        self.last_tokens: int | None = None
        self._opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def __call__(self, prompt: str) -> str:
        body = json.dumps({
            "model": self.model, "messages": [{"role": "user", "content": prompt}],
            "temperature": 0, "max_tokens": self.max_tokens,
        }).encode("utf-8")
        req = urllib.request.Request(self.url, data=body, headers={"Content-Type": "application/json"})
        with self._opener.open(req, timeout=self.timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        usage = data.get("usage") or {}
        self.last_tokens = usage.get("completion_tokens")
        return str(data["choices"][0]["message"]["content"])


class CommandModel:
    """執行指令，prompt 從標準輸入送進去，標準輸出當成回答；結束碼不是 0 就算模型錯誤。"""

    def __init__(self, cmd: str, *, timeout: float = 120):
        self.argv, self.timeout = shlex.split(cmd), timeout
        self.name = cmd

    def __call__(self, prompt: str) -> str:
        done = subprocess.run(self.argv, input=prompt, capture_output=True, text=True, encoding="utf-8",
                              timeout=self.timeout, check=True)
        return done.stdout


# ── 執行與報告 ──────────────────────────────────────────

@dataclass
class Result:
    case: Case
    out: Rewritten
    seconds: float
    tokens: int | None = None


@dataclass
class Run:
    model: str
    results: list[Result] = field(default_factory=list)


def evaluate(model: Any, cases: list[Case], *, clock: Callable[[], float] = time.perf_counter) -> Run:
    run = Run(model.name)
    for case in cases:
        started = clock()
        out = rewrite(case.source, place=PLACE if isinstance(case.source, Summary) else "", model=model)
        seconds = clock() - started
        run.results.append(Result(case, out, seconds, getattr(model, "last_tokens", None)))
    return run


# 退回原因的開頭 → 統計時的類別（rewrite() 與 check_output() 的原因寫法）
REASON_KINDS = (
    ("漏掉範本裡的數字", "漏掉數字"),
    ("長度", "太長"),
    ("模型錯誤", "模型錯誤"),
    ("數字", "多出事實沒有的數字"),
    ("不能出現", "用了不能用的字"),
    ("不能寫", "用了不能用的字"),
)


def _reason_kind(reason: str) -> str:
    """把「漏掉範本裡的數字 3」這類原因歸成同一類，方便統計；認不得的照原文。"""
    for prefix, kind in REASON_KINDS:
        if reason.startswith(prefix):
            return kind
    return reason


def report_text(run: Run, *, show: bool = False) -> str:
    rs = run.results
    adopted = [r for r in rs if r.out.layer == "model"]
    lines = [f"模型：{run.model}　題數：{len(rs)}　採用 {len(adopted)}、退回範本 {len(rs) - len(adopted)}"]
    for r in rs:
        state = "採用" if r.out.layer == "model" else "退回"
        tok = f"，{r.tokens} token" if r.tokens else ""
        lines.append(f"[{state}] {r.case.id} {r.case.title}（{r.seconds:.2f} 秒{tok}）")
        for reason in r.out.rejected:
            lines.append(f"    原因：{reason}")
        if show and r.out.layer == "model":
            lines.extend("    " + line for line in r.out.text.splitlines())
    if rs:
        secs = [r.seconds for r in rs]
        lines.append("")
        lines.append(f"採用率 {len(adopted) / len(rs):.0%}；每題延遲 中位數 {statistics.median(secs):.2f} 秒、最長 {max(secs):.2f} 秒")
        timed = [r for r in rs if r.tokens and r.seconds > 0]
        if timed:
            rate = sum(r.tokens for r in timed if r.tokens) / sum(r.seconds for r in timed)
            lines.append(f"輸出速度 約 {rate:.1f} token／秒（依伺服器回報的 completion_tokens）")
        mean = statistics.mean(secs)
        if mean >= MIN_TIMED:
            lines.append(f"照這個速度，一小時約可改寫 {3600 / mean:,.0f} 份（單一請求依序執行；快取命中的不必再改寫）")
        else:
            lines.append("延遲太短（假模型），不估算一小時的改寫量")
        kinds = Counter(_reason_kind(reason) for r in rs for reason in r.out.rejected)
        if kinds:
            lines.append("退回原因：" + "、".join(f"{k} {n}" for k, n in kinds.most_common()))
    lines.append("題組是程式產生的模擬資料；採用率是上線後模型輸出會被用上的比例，沒通過的照樣用範本，不影響報告。")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="在地模型評估：固定題組跑 rewrite()，量採用率、延遲與退回原因")
    which = ap.add_mutually_exclusive_group(required=True)
    which.add_argument("--echo", action="store_true", help="照抄原文的假模型（CI、試跑用）")
    which.add_argument("--endpoint", help="OpenAI 相容 API 的網址，例如 http://127.0.0.1:8080/v1")
    which.add_argument("--cmd", help="執行的指令；prompt 從標準輸入送入")
    ap.add_argument("--model", help="--endpoint 時的模型名稱（會記進追溯紀錄）")
    ap.add_argument("--timeout", type=float, default=120, help="每題逾時秒數（預設 120）")
    ap.add_argument("--only", help="只跑這些題，逗號分隔，例如 E2,K7")
    ap.add_argument("--show", action="store_true", help="印出被採用的模型輸出")
    args = ap.parse_args(sys.argv[1:] if argv is None else argv)

    model: Any
    if args.echo:
        model = EchoModel()
    elif args.endpoint:
        if not args.model:
            print("錯誤：--endpoint 要搭配 --model", file=sys.stderr)
            return 2
        model = HttpChatModel(args.endpoint, args.model, timeout=args.timeout)
    else:
        model = CommandModel(args.cmd, timeout=args.timeout)
    cases = all_cases()
    if args.only:
        wanted = {s.strip() for s in args.only.split(",") if s.strip()}
        unknown = wanted - {c.id for c in cases}
        if unknown:
            print(f"錯誤：沒有這些題：{'、'.join(sorted(unknown))}", file=sys.stderr)
            return 2
        cases = [c for c in cases if c.id in wanted]
    print(report_text(evaluate(model, cases), show=args.show))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
