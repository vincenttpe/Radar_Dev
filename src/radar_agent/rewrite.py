"""在地模型改寫層：範本與模型走同一個入口，模型的輸出通過檢查才採用，否則退回範本。

docs/02「在地模型負載與分流」：範本是主要路徑；模型只改寫說法，不新增或改動事實（原則 2）；
每段文字記下來自哪一層、哪一版模型與 prompt（原則 3）。快取層之後接在同一個入口。
模型本身（在 Jetson 上跑哪一個、怎麼限制時間）不在這裡：呼叫端傳入一個「prompt → 文字」的函式，
逾時要由該函式自己處理並丟出例外。
改 prompt 或檢查屬單向門「給讀者的輸出與用語」（REVIEW.md 第 2 節）。
"""
from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import Literal, Protocol

from radar_agent.audience import Profile, check_audience
from radar_agent.caregiver import caregiver_text, handover_text
from radar_agent.device_events import Summary
from radar_agent.kg_rules import Assessment
from radar_agent.output_check import check_output, numbers

PROMPT_VERSION = "caregiver-v0"
# 範本裡的這些提醒，改寫後一定要留著：拿掉就變成「沒有資料」被讀成「沒事」（REVIEW.md 3.1）
REQUIRED_PHRASES = ("無法判斷", "無法評估", "僅供測試", "請通知資訊人員")
# 暫定：改寫最多是範本的幾倍長；限制輸出長度見 docs/02
MAX_LENGTH_RATIO = 2


class Model(Protocol):
    """在地模型。name 寫模型與版本（例如量化方式），會記進追溯紀錄。"""

    name: str

    def __call__(self, prompt: str) -> str: ...


@dataclass(frozen=True)
class Rewritten:
    text: str
    layer: Literal["template", "model"]
    profile: Profile
    # 有呼叫模型就記下，即使最後退回範本，方便追查哪一版模型被擋下
    model: str | None = None
    prompt_version: str | None = None
    # 模型輸出沒通過的原因；空的表示沒有呼叫模型或模型輸出被採用
    rejected: tuple[str, ...] = ()


def build_prompt(template: str) -> str:
    """給模型的 prompt：只放範本文字，不放任何沒有出現在範本裡的資料。"""
    return (
        "你是長照機構的報告助手。請把下面這段給居服員看的文字改寫得更口語、好讀。\n"
        "規則：\n"
        "1. 只改說法，不新增、不刪除任何事實。\n"
        "2. 數字、日期、時間照原樣寫，不換算、不四捨五入。\n"
        "3. 「」裡的文字、以及「建議：」「建議量：」後面的文字一字不改照抄。\n"
        f"4. 原文有「{'」「'.join(REQUIRED_PHRASES)}」的句子一定要保留。\n"
        "5. 不使用「診斷」「治療」「處方」「保證」「誤報」等字眼。\n"
        f"6. 長度不超過原文的 {MAX_LENGTH_RATIO} 倍。只輸出改寫後的文字，不加說明。\n"
        "原文：\n"
        f"{template}"
    )


def _kept_facts(template: str, text: str, source: Summary | Assessment) -> list[str]:
    """check_output 擋「多出來的」；這裡擋「被拿掉或被改寫的」。"""
    problems = []
    missing = sorted(numbers(template) - numbers(text))  # 以數值比較：09:10 寫成 9:10 不算漏掉
    for number in missing:
        problems.append(f"漏掉範本裡的數字 {number}")
    for phrase in REQUIRED_PHRASES:
        if phrase in template and phrase not in text:
            problems.append(f"漏掉「{phrase}」")
    if not isinstance(source, Summary):
        for key in ("rank", "suggestion", "recommended_dose"):
            quoted = source[key]
            if quoted and quoted in template and quoted not in text:
                problems.append(f"知識圖譜原文（{key}）沒有照抄")
    if len(text) > MAX_LENGTH_RATIO * len(template):
        problems.append(f"長度 {len(text)} 字超過範本的 {MAX_LENGTH_RATIO} 倍")
    return problems


# 「5 次」「22 分鐘」「3 分 10 秒」「1 筆」要整組對得上：numbers() 只比數字有沒有出現過，
# 「5 次」改成「6 次」時，5 還在「10:05」裡、6 也可能是別的事實，會被放過（2026-10-29 交班重點的測試發現；rewrite() 也一起用）
# 時間長度整段比：「3 分 10 秒」「1 小時 15 分」只比「10 秒」或「15 分」時，改掉前面的分、時會被放過
# （2026-10-29 審查實測：「3 分 10 秒」改成「2 分 10 秒」被採用）。「分鐘」與「分」視為相同
_QUANTITY = re.compile(r"(?<![\d:.])\d+(?:\.\d+)? ?(?:小時|分鐘|分|秒|次|筆)(?: ?\d+ ?(?:分鐘|分|秒))*")


def _quantities(text: str) -> set[str]:
    return {q.replace(" ", "").replace("分鐘", "分") for q in _QUANTITY.findall(text)}


def _kept_quantities(template: str, text: str) -> list[str]:
    before, after = _quantities(template), _quantities(text)
    return ([f"漏掉「{q}」" for q in sorted(before - after)]
            + [f"範本沒有「{q}」" for q in sorted(after - before)])


def rewrite(
    source: Summary | Assessment, *, profile: Profile = "居服員", place: str = "", model: Model | None = None
) -> Rewritten:
    """給 profile 讀者看的文字。沒有模型就用範本；模型出錯或輸出沒通過檢查也退回範本。

    丟出 ValueError、不產生任何文字的情況：這個 Profile 不能看這份資料（先檢查，資料不進範本也不進模型）；
    這個 Profile 還沒有範本（目前只有居服員）；範本本身沒通過輸出檢查（範本或輸入的問題，不能交給模型修）。
    """
    refused = check_audience(profile, source)
    if refused:
        raise ValueError("讀者權限不符：" + "；".join(refused))
    if profile != "居服員":
        raise ValueError(f"還沒有{profile}的範本")
    template = caregiver_text(source, place=place)
    problems = check_output(template, source, place=place)
    if problems:
        raise ValueError("範本沒通過輸出檢查：" + "；".join(problems))
    if model is None:
        return Rewritten(template, "template", profile)
    return checked_rewrite(
        template, source, place=place, profile=profile, model=model,
        prompt=build_prompt(template), prompt_version=PROMPT_VERSION,
        kept=lambda text: _kept_facts(template, text, source) + _kept_quantities(template, text),
    )


def checked_rewrite(
    original: str,
    source: Summary | Assessment,
    *,
    place: str,
    profile: Profile,
    model: Model,
    prompt: str,
    prompt_version: str,
    kept: Callable[[str], list[str]],
) -> Rewritten:
    """讓模型改寫 original；採用前一定經過輸出檢查。給讀者的模型文字只從這裡出去。

    模型出錯、沒有輸出、輸出沒通過 check_output（擋多出來的事實與用詞）或 kept（擋被拿掉的，由呼叫端依文字種類決定），
    都退回 original，並記下原因與模型版本。
    """
    def fallback(*reasons: str) -> Rewritten:
        return Rewritten(original, "template", profile, model.name, prompt_version, reasons)

    try:
        text = model(prompt).strip()
    except Exception as e:  # 模型逾時、當掉都退回原文，不讓報告停住
        return fallback(f"模型錯誤：{type(e).__name__}")
    if not text:
        return fallback("模型沒有輸出")
    problems = check_output(text, source, place=place) + kept(text)
    if problems:
        return fallback(*problems)
    return Rewritten(text, "model", profile, model.name, prompt_version)


# ── 交班重點的口語改寫（草案，2026-10-29；Jetson 內部 Demo 的實驗功能，docs/21）──────────
# 原句由 caregiver.handover_text 產生（【重點】＋【事件摘要】）；模型只改寫事件摘要的說法。
# 檢查和 rewrite() 同一道（含次數與時間長度整段比對），另加兩項：每個時刻都要出現；不能加上範本沒有的時段詞與「我們」。numbers() 以集合比較，漏掉一個事件或改掉一個次數時，數字可能剛好在別處出現。
# 已知限制（docs/21）：數量只比「有沒有出現」、不比屬於哪個事件；「沒有……」改成「有……」也擋不到。
# 所以頁面一定把原句和模型版並排顯示（測試版，2026-10-29 決定）。
HANDOVER_PROMPT_VERSION = "handover-v0"
# 範本沒有、模型卻常自己加上的詞（2026-10-29 實測）：時段詞會說錯（「下午 10:03」），
# 「我們」會把裝置做的事寫成照護人員做的（「我們語音詢問」）。範本裡沒有就不能出現
HANDOVER_ADDED_WORDS = ("凌晨", "早上", "上午", "中午", "下午", "晚上", "我們")
_CLOCK = re.compile(r"(?<!\d)(\d{1,2}):(\d{2})(?!\d)")


def build_handover_prompt(template: str) -> str:
    """交班口語化的 prompt：只放範本文字。"""
    return (
        "你是長照機構的交班助手。下面是一個地點一段時間的事件摘要（地點、時段與重點另外標示，不用重寫），請改寫成一段口語的交班說明，"
        "像跟下一班同事口頭交代一樣，讀起來順、好懂。\n"
        "規則：\n"
        "1. 只改說法，不新增、不刪除任何事實；不推測原因，不推測是哪一位長者，不加建議。\n"
        "2. 依時間順序講，每一個時間都要寫出來，不合併、不省略；最後講使用情形和無法判斷的時段。\n"
        "3. 數字、日期、時間照原樣寫，不換算、不四捨五入。\n"
        f"4. 原文有「{'」「'.join(REQUIRED_PHRASES)}」的句子一定要保留。\n"
        "5. 不使用「診斷」「治療」「處方」「保證」「誤報」等字眼。\n"
        f"6. 不加「{'」「'.join(HANDOVER_ADDED_WORDS)}」這些詞：時間只寫原文的時刻；語音詢問、偵測都是裝置做的。\n"
        f"7. 長度不超過原文的 {MAX_LENGTH_RATIO} 倍。只輸出改寫後的文字，不加標題或說明。\n"
        "原文：\n"
        f"{template}"
    )


def _clock_times(text: str) -> set[tuple[int, int]]:
    """文字裡的時刻（時, 分）；09:10 和 9:10 視為同一個。"""
    return {(int(h), int(m)) for h, m in _CLOCK.findall(text)}


def _kept_times(template: str, text: str) -> list[str]:
    return [f"漏掉時間 {h:02d}:{m:02d}" for h, m in sorted(_clock_times(template) - _clock_times(text))]


def _added_words(template: str, text: str) -> list[str]:
    return [f"範本沒有「{w}」" for w in HANDOVER_ADDED_WORDS if w in text and w not in template]


def rewrite_handover(summary: Summary, *, place: str, model: Model | None = None) -> Rewritten:
    """居服員看的交班重點。沒有模型或沒有資料就用原句；模型出錯或沒通過檢查也退回原句（檢查見上方說明）。"""
    # 居服員可以看場域事件摘要（audience.ALLOWED），不另做權限檢查
    template = handover_text(summary, place=place)
    problems = check_output(template, summary, place=place)
    if problems:
        raise ValueError("範本沒通過輸出檢查：" + "；".join(problems))
    if model is None or not summary.online:  # 沒有收到資料時只有「無法判斷」，不必改寫
        return Rewritten(template, "template", "居服員")
    # 前兩行（地點與時段、【重點】）由程式固定顯示，模型只改寫下面的事件摘要：
    # 實測模型常把開頭與【重點】整行省掉，而那兩行的數字在事件摘要裡都還有，檢查擋不到（2026-10-29）
    first, highlight, body = template.split("\n", 2)
    head = f"{first}\n{highlight}"
    result = checked_rewrite(
        body, summary, place=place, profile="居服員", model=model,
        prompt=build_handover_prompt(body), prompt_version=HANDOVER_PROMPT_VERSION,
        kept=lambda text: (_kept_facts(body, text, summary) + _kept_times(body, text)
                           + _kept_quantities(body, text) + _added_words(body, text)),
    )
    return replace(result, text=f"{head}\n{result.text}")
