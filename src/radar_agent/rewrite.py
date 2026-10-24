"""在地模型改寫層：範本與模型走同一個入口，模型的輸出通過檢查才採用，否則退回範本。

docs/02「在地模型負載與分流」：範本是主要路徑；模型只改寫說法，不新增或改動事實（原則 2）；
每段文字記下來自哪一層、哪一版模型與 prompt（原則 3）。快取層之後接在同一個入口。
模型本身（在 Jetson 上跑哪一個、怎麼限制時間）不在這裡：呼叫端傳入一個「prompt → 文字」的函式，
逾時要由該函式自己處理並丟出例外。
改 prompt 或檢查屬單向門「給讀者的輸出與用語」（REVIEW.md 第 2 節）。
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal, Protocol

from radar_agent.audience import Profile, check_audience
from radar_agent.caregiver import caregiver_text
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
        kept=lambda text: _kept_facts(template, text, source),
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
