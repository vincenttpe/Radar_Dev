"""在地模型評估：用固定題組跑 rewrite()，量採用率、延遲與 token 速度（docs/06「實測步驟」）。

題組全部由程式產生（裝置事件的各種狀況、知識圖譜各指標的等級與缺口），不含任何真實資料。
每一題都走正式的 rewrite()：權限、範本、輸出檢查、退回範本與正式產生報告時完全一樣，
所以這裡量到的「採用率」就是上線後模型輸出會被用上的比例。

radar-model-eval --echo
    照抄原文的假模型，給 CI 與第一次試跑用（應該全部採用）。
radar-model-eval --endpoint http://127.0.0.1:8080/v1 --model gemma4-e4b-q4_k_m
    OpenAI 相容的 chat completions（llama.cpp server、Ollama 等）。只連本機或區網，不經過 proxy。
radar-model-eval --cmd "某個指令"
    任意指令，prompt 從標準輸入送進去，標準輸出當成模型的回答。
radar-model-eval --endpoint … --model … --questions
    改量「提問對應」（ask.match_question）：打字提問能不能對到正確的固定問題。
radar-model-eval --endpoint … --model … --records [--references 參考紀錄.json] [--style 機構格式樣本.txt]
    改量「照護紀錄改寫」（care_record）：採用率；有參考紀錄時再算 BLEU、ROUGE 並對照 KPI 目標（docs/08）。
    參考紀錄是 {"題號": "護理人員寫的紀錄"}，要由機構提供（會議清單 F13），樣本與參考都要先去識別。

選型時對每個候選模型各跑一次，比較採用率、延遲與退回原因（會議清單 B1、F2）。
中國開發或以其為基底的模型不用（docs/adr/0003）；名稱屬於這些家族時直接拒絕，不送任何題目。
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from pathlib import Path
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from radar_agent import kg_rules
from radar_agent.ask import Match, Question, match_question
from radar_agent.care_record import care_record
from radar_agent.caregiver import event_notes
# 模型類別搬到 local_model.py；這裡保留匯入，舊的匯入路徑（測試、文件）照常可用
from radar_agent.local_model import CommandModel, EchoModel, HttpChatModel, add_model_args, model_from_args  # noqa: F401
from radar_agent.text_metrics import bleu, rouge_1, rouge_l
from radar_agent.device_events import Summary, summarize
from radar_agent.event_ask import EVENT_QUESTIONS
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


# 提問對應的題組：固定問題清單（照 demo/kg_build.py 居服員與家屬的題目）與讀者可能的打法。
# expected 為 None 的是清單沒有、應該回 0 的提問（醫療問題、閒聊）。
ASK_MENU: list[Question] = [
    Question("walk", "這週走路的狀況怎麼樣？"),
    Question("how", "步速是怎麼算出來的？"),
    Question("others", "起身和平衡的狀況呢？"),
    Question("advice", "可以怎麼陪伴活動？"),
    Question("compare", "和上週比起來呢？"),
]


@dataclass(frozen=True)
class AskCase:
    id: str
    asked: str
    expected: str | None


ASK_CASES: list[AskCase] = [
    AskCase("Q1", "他最近走路還穩嗎", "walk"),
    AskCase("Q2", "這禮拜行走情況", "walk"),
    AskCase("Q3", "0.9 這個數字哪來的", "how"),
    AskCase("Q4", "速度是用什麼算的", "how"),
    AskCase("Q5", "站起來會不會晃", "others"),
    AskCase("Q6", "平衡感有沒有問題", "others"),
    AskCase("Q7", "我回家可以帶他做什麼運動", "advice"),
    AskCase("Q8", "家人能幫忙什麼", "advice"),
    AskCase("Q9", "有比上禮拜進步嗎", "compare"),
    AskCase("Q10", "是不是越來越差", "compare"),
    AskCase("Q11", "要不要吃鈣片", None),
    AskCase("Q12", "明天會下雨嗎", None),
    AskCase("Q13", "他是不是失智了", None),
]


@dataclass
class AskResult:
    case: AskCase
    match: Match
    seconds: float

    @property
    def outcome(self) -> str:
        got = self.match.question.id if self.match.question else None
        if got == self.case.expected:
            return "正確"
        return "沒對應到" if got is None else "對錯題"


# 事件提問（radar-ask，11 月中甲方 Demo）：照護人員可能怎麼問，對到 EVENT_QUESTIONS 哪一題
EVENT_ASK_CASES: list[AskCase] = [
    AskCase("V1", "早上有人跌倒嗎", "falls"),
    AskCase("V2", "今天有沒有跌倒警報", "falls"),
    AskCase("V3", "有長輩喊救命嗎", "calls"),
    AskCase("V4", "有人按呼叫或用講的求救嗎", "calls"),
    AskCase("V5", "有人在廁所待很久嗎", "longstay"),
    AskCase("V6", "久待警報有幾次", "longstay"),
    AskCase("V7", "廁所今天用了幾次", "visits"),
    AskCase("V8", "最久一次待多久", "visits"),
    AskCase("V9", "雷達有沒有離線", "offline"),
    AskCase("V10", "機器有沒有壞掉或沒連線", "offline"),
    AskCase("V11", "現在我要去處理什麼", "alerts"),
    AskCase("V12", "今天的警報有哪些", "alerts"),
    AskCase("V13", "他今天血壓多少", None),
    AskCase("V14", "晚餐吃什麼", None),
    AskCase("V15", "他是不是生病了", None),
    # 2026-10-28 改 longstay 題目文字（加上「久待警報有幾次」）後新增，題目調整時沒有拿來對過
    AskCase("V16", "今天有幾次待太久", "longstay"),
    AskCase("V17", "久待的次數", "longstay"),
    AskCase("V18", "上廁所上很久的有幾次", "longstay"),
    # 真實打字習慣（2026-10-28 補）：錯字、只打兩三個字、口語
    AskCase("V19", "有人跌到嗎", "falls"),
    AskCase("V20", "有沒有人摔道", "falls"),
    AskCase("V21", "雷達段線了嗎", "offline"),
    AskCase("V22", "跌倒？", "falls"),
    AskCase("V23", "斷線", "offline"),
    AskCase("V24", "警報", "alerts"),
    AskCase("V25", "幾次", "visits"),
    AskCase("V26", "有沒有人倒在那", "falls"),
    AskCase("V27", "有人叫人嗎", "calls"),
    AskCase("V28", "誰卡在廁所很久", "longstay"),
    AskCase("V29", "機器還活著嗎", "offline"),
    # 追問：每次提問都不帶上一題，沒有題目可對；之後支援追問時要改預期
    AskCase("V30", "那昨天呢", None),
    AskCase("V31", "廁所那台呢", None),
    # 醫療判斷、閒聊、要模型寫文字：都要回 0
    AskCase("V32", "她要不要去看醫生", None),
    AskCase("V33", "跌倒要吃什麼藥", None),
    AskCase("V34", "你好", None),
    AskCase("V35", "幫我寫一份交班紀錄", None),
]


def evaluate_asks(model: Any, cases: list[AskCase], *, menu: list[Question] | None = None,
                  clock: Callable[[], float] = time.perf_counter) -> tuple[str, list[AskResult]]:
    questions = ASK_MENU if menu is None else menu
    results = []
    for case in cases:
        started = clock()
        match = match_question(case.asked, questions, model=model)
        results.append(AskResult(case, match, clock() - started))
    return model.name, results


def report_asks(run: tuple[str, list[AskResult]]) -> str:
    name, rs = run
    n = Counter(r.outcome for r in rs)
    lines = [f"模型：{name}　題數：{len(rs)}　正確 {n['正確']}、對錯題 {n['對錯題']}、沒對應到 {n['沒對應到']}"]
    for r in rs:
        got = r.match.question.id if r.match.question else "無"
        lines.append(f"[{r.outcome}] {r.case.id} 「{r.case.asked}」→ {got}（應為 {r.case.expected or '無'}，{r.seconds:.2f} 秒）")
        lines.extend(f"    原因：{reason}" for reason in r.match.rejected)
    if rs:
        lines.append("")
        lines.append(f"正確率 {n['正確'] / len(rs):.0%}；每題延遲 中位數 {statistics.median(r.seconds for r in rs):.2f} 秒")
    lines.append("對錯題比沒對應到嚴重：讀者確認時沒看清楚，就會拿到別題的答案。沒對應到時改列選單，不影響回答。")
    return "\n".join(lines)


# 照護紀錄：有事件的模擬題目；LLM 文字指標的目標（docs/08「LLM 文字生成指標」）
RECORD_TARGETS = {"BLEU-1": 44, "BLEU-4": 5, "ROUGE-1": 31, "ROUGE-L": 28}


def record_cases() -> list[Case]:
    return [c for c in event_cases() if isinstance(c.source, Summary) and event_notes(c.source, PLACE)]


def evaluate_records(model: Any, cases: list[Case], *, style: str | None = None,
                     clock: Callable[[], float] = time.perf_counter) -> Run:
    run = Run(model.name)
    for case in cases:
        assert isinstance(case.source, Summary)
        started = clock()
        out = care_record(case.source, place=PLACE, model=model, style=style)
        run.results.append(Result(case, out, clock() - started, getattr(model, "last_tokens", None)))
    return run


def report_records(run: Run, references: dict[str, str] | None) -> str:
    rs = run.results
    adopted = [r for r in rs if r.out.layer == "model"]
    lines = [f"模型：{run.model}　照護紀錄題數：{len(rs)}　採用 {len(adopted)}、退回草稿 {len(rs) - len(adopted)}"]
    for r in rs:
        lines.append(f"[{'採用' if r.out.layer == 'model' else '退回'}] {r.case.id} {r.case.title}（{r.seconds:.2f} 秒）")
        lines.extend(f"    原因：{reason}" for reason in r.out.rejected)
    lines.append("")
    pairs = [(r.out.text, references[r.case.id]) for r in rs if references and r.case.id in references]
    if not pairs:
        lines.append("沒有參考紀錄，不計算 BLEU、ROUGE（需要機構提供護理人員寫的紀錄，會議清單 F13）。")
        return "\n".join(lines)
    scores = {"BLEU-1": bleu(pairs, 1), "BLEU-4": bleu(pairs, 4), "ROUGE-1": rouge_1(pairs), "ROUGE-L": rouge_l(pairs)}
    lines.append(f"有參考紀錄的題數：{len(pairs)}（以實際交給居服員的紀錄計算，退回草稿的也算）")
    for name, value in scores.items():
        target = RECORD_TARGETS[name]
        lines.append(f"{name} {value:.1f}（目標 ≥ {target}，{'達標' if value >= target else '未達標'}）")
    lines.append("BERTScore 需要另外的語言模型，這裡不計算。")
    return "\n".join(lines)


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
    add_model_args(ap, required=True)
    ap.add_argument("--only", help="只跑這些題，逗號分隔，例如 E2,K7")
    ap.add_argument("--show", action="store_true", help="印出被採用的模型輸出")
    ap.add_argument("--questions", action="store_true", help="改量提問對應（打字提問對到哪一個固定問題）")
    ap.add_argument("--event-questions", action="store_true", help="改量事件提問（radar-ask 的題目）")
    ap.add_argument("--records", action="store_true", help="改量照護紀錄改寫")
    ap.add_argument("--references", help="--records 用：參考紀錄 JSON（{題號: 紀錄}），有它才算 BLEU、ROUGE")
    ap.add_argument("--style", help="--records 用：機構紀錄格式樣本（文字檔，需先去識別）")
    args = ap.parse_args(sys.argv[1:] if argv is None else argv)

    try:
        model = model_from_args(args)
    except ValueError as e:
        print(f"錯誤：{e}", file=sys.stderr)
        return 2
    if args.reasoning_effort is not None:  # 開不開思維鏈結果差很多，報告上要看得出這次用了什麼設定
        print(f"設定：reasoning_effort={args.reasoning_effort}")
    if args.questions:
        print(report_asks(evaluate_asks(model, ASK_CASES)))
        return 0
    if args.event_questions:
        print(report_asks(evaluate_asks(model, EVENT_ASK_CASES, menu=EVENT_QUESTIONS)))
        return 0
    if args.records:
        refs = json.loads(Path(args.references).read_text(encoding="utf-8")) if args.references else None
        style = Path(args.style).read_text(encoding="utf-8") if args.style else None
        print(report_records(evaluate_records(model, record_cases(), style=style), refs))
        return 0
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
