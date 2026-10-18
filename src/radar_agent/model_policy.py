"""在地模型的來源限制（docs/adr/0003）：不用中國開發、或以中國模型為基底的模型（含微調、蒸餾、量化版本）。

這裡只用名稱比對常見的模型家族，是評估工具的第一道提醒，擋不住改過名字的模型。
正式選型要看 model card 的開發者與 base model（docs/06「實測步驟」），結果記在選型紀錄。
"""
from __future__ import annotations

import re

# 模型家族 → 開發者。名稱裡出現這些字就不用；清單之外的模型仍要查 model card。
_FAMILIES: tuple[tuple[str, str], ...] = (
    (r"qwen", "Qwen（阿里巴巴）"),
    (r"deepseek", "DeepSeek"),
    (r"chatglm|(?<![a-z])glm[-_]?\d", "GLM（智譜）"),
    (r"baichuan", "百川"),
    (r"internlm", "InternLM（上海人工智慧實驗室）"),
    (r"minicpm", "MiniCPM（面壁智慧）"),
    (r"(?<![a-z])yi[-_.:]\d", "Yi（零一萬物）"),
    (r"hunyuan", "混元（騰訊）"),
)


def banned_origin(name: str) -> str | None:
    """模型名稱（或啟動指令）屬於限制的家族時，回傳原因；否則 None。"""
    lowered = name.lower()
    for pattern, family in _FAMILIES:
        if re.search(pattern, lowered):
            return f"{family}屬於中國開發的模型，依 docs/adr/0003 不使用"
    return None
