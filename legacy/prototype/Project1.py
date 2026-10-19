# -*- coding: utf-8 -*-
# 單檔整合版：Project1 + KG/external evidence 合併 + 連續動作 + 彈性健康網頁外部搜尋 v8
# 預設一個指令跑到底：python Project1.py --start "YYYY/MM/DD HH:MM" --end "YYYY/MM/DD HH:MM"

import os
import re
import json
import sys
import argparse
import torch
import numpy as np
import pandas as pd
from datetime import datetime
from pathlib import Path
from functools import lru_cache
from concurrent.futures import ThreadPoolExecutor, as_completed
from transformers import AutoTokenizer, AutoModelForCausalLM
from sentence_transformers import SentenceTransformer
from neo4j import GraphDatabase



# ============================================================
# 外部搜尋模組（已內嵌，不需要 external_search.py）
# ============================================================
import os
import re
import json
import requests
from pathlib import Path
from bs4 import BeautifulSoup
from typing import List, Dict, Optional

# 優先使用 ddgs；若沒有則退回 duckduckgo_search
try:
    from ddgs import DDGS
except ImportError:
    from duckduckgo_search import DDGS

# ============================================================
# 0. 外部搜尋安全設定
# ============================================================
SEARCH_TIMEOUT_SEC = float(os.getenv("SEARCH_TIMEOUT_SEC", "4"))
ENABLE_LIVE_SEARCH = os.getenv("ENABLE_LIVE_SEARCH", "1") == "1"
CACHE_PATH = Path(__file__).resolve().parent / "external_search_cache.json"

# ============================================================
# 1. 搜尋關鍵字設定
# ============================================================
ASSESSMENT_KEYWORDS = {
    "下肢肌力": [
        "older adults lower limb strength exercise recommendation",
        "older adults sit to stand exercise safety",
        "elderly leg strength training fall prevention",
    ],
    "步行速度": [
        "older adults walking speed gait training recommendation",
        "elderly gait speed exercise intervention",
        "older adults walking exercise safety",
    ],
    "站立平衡": [
        "older adults balance exercise fall prevention",
        "elderly standing balance training recommendation",
        "balance exercises for seniors safety",
    ],
    "單腳站立": [
        "single leg balance exercise older adults fall prevention",
        "older adults one leg standing balance training",
        "elderly balance training safety",
    ],
    "心肺耐力": [
        "older adults cardiovascular endurance exercise recommendation",
        "elderly aerobic exercise safety recommendation",
        "older adults endurance exercise walking",
    ],
    "上肢肌力": [
        "older adults upper limb strength exercise recommendation",
        "elderly arm strength exercise safety",
        "senior upper body strength training",
    ],
    "上肢柔軟度": [
        "older adults upper limb flexibility exercise recommendation",
        "elderly shoulder flexibility exercise safety",
        "senior arm stretching recommendation",
    ],
    "下肢柔軟度": [
        "older adults lower limb flexibility exercise recommendation",
        "elderly leg stretching safety",
        "senior lower body flexibility training",
    ],
    "空間方位位移": [
        "older adults balance spatial orientation movement safety",
        "older adults weight shifting balance fall prevention",
        "elderly movement direction balance training",
    ],
    "上肢與軀幹活動": [
        "older adults upper body trunk mobility exercise safety",
        "elderly trunk mobility arm movement exercise",
        "senior upper body movement safety",
    ],
    "來回行走": [
        "older adults forward backward walking balance exercise",
        "elderly walking direction change fall prevention",
        "older adults gait training forward backward walking",
    ],
}

# 本專案只處理「動作難度 / 功能性動作 / 復健動作建議」。
# 因此外部搜尋不能查補水、營養、疾病等非動作主題。
# 例如喝水只可視為「握杯、抬杯、送至口部」的功能性上肢動作，不可查 hydration / water intake。
FUNCTIONAL_ACTION_HINTS = [
    "drink", "water", "cup", "wave", "read", "book", "scratch",
    "clap", "watch", "door", "towel", "phone", "face",
    "glasses", "point", "fold", "open", "hold", "grasp", "reach",
]

# 舊函式仍會呼叫 DAILY_ACTION_HINTS，保留別名避免破壞其他流程。
DAILY_ACTION_HINTS = FUNCTIONAL_ACTION_HINTS

VAGUE_MOTION_HINTS = [
    "subtle", "slight", "slightly", "brief", "briefly", "unclear",
    "small movement", "moves a little", "changes position", "reposition",
    "short motion", "without obvious", "exact action is unclear",
    "transitions briefly", "appears to", "a little before stopping",
]

# 舊版曾使用 FUNCTIONAL_MOTION_RULES 逐條對應固定動作。
# v6 已改成 primary movement group 動態判斷，不再依賴固定句子規則；
# 保留空 list 只為相容舊函式，不參與主要搜尋流程。
FUNCTIONAL_MOTION_RULES: list[dict] = []

# 專業網站優先順序：
# 1) 優先使用醫院、政府健康教育、物理/職能治療、復健科、專業協會等「實務建議」網站。
# 2) 預設排除 PubMed/PMC/期刊論文頁，避免使用者報告變成論文摘要。
#    若真的要允許論文來源，可用 ALLOW_RESEARCH_EVIDENCE=1 開啟。
PREFERRED_PRACTICAL_DOMAINS = [
    # 政府 / 公共健康教育
    "nhs.uk",
    "nia.nih.gov",
    "cdc.gov",
    "medlineplus.gov",
    "betterhealth.vic.gov.au",
    "healthlinkbc.ca",
    "myhealth.alberta.ca",
    "alberta.ca",

    # 醫院 / 醫療系統 / 專業病人衛教
    "mayoclinic.org",
    "clevelandclinic.org",
    "mskcc.org",
    "hss.edu",
    "kaiserpermanente.org",
    "health.harvard.edu",

    # 物理治療 / 職能治療 / 復健專業
    "choosept.com",
    "apta.org",
    "aota.org",
    "physio-pedia.com",
    "versusarthritis.org",
]

GENERAL_HEALTH_FALLBACK_DOMAINS = [
    # 只作為最後備援；使用者報告仍優先採用上方專業/醫療/復健網站
    "verywellhealth.com",
    "healthline.com",
    "webmd.com",
]

STRICT_PROFESSIONAL_SOURCES = os.getenv("STRICT_PROFESSIONAL_SOURCES", "1") == "1"
SOURCE_WEIGHT = float(os.getenv("SOURCE_WEIGHT", "1.2"))
ACTION_WEIGHT = float(os.getenv("ACTION_WEIGHT", "2.5"))
TEACHING_WEIGHT = float(os.getenv("TEACHING_WEIGHT", "2.0"))
RISK_WEIGHT = float(os.getenv("RISK_WEIGHT", "2.0"))

# 外部 evidence 只採用中文且有實際內容的網頁。
# 避免搜尋結果點進去變英文首頁、科部首頁，或抓到沒有內文的頁面。
CHINESE_ONLY_EVIDENCE = os.getenv("CHINESE_ONLY_EVIDENCE", "1") == "1"
MIN_CHINESE_CHARS = int(os.getenv("MIN_CHINESE_CHARS", "12"))
MIN_PAGE_TEXT_CHINESE_CHARS = int(os.getenv("MIN_PAGE_TEXT_CHINESE_CHARS", "30"))
SEARCH_VARIANT_LIMIT = int(os.getenv("SEARCH_VARIANT_LIMIT", "4"))

REHAB_CLINIC_DOMAIN_HINTS = [
    "rehab", "physio", "physicaltherapy", "physical-therapy",
    "occupationaltherapy", "occupational-therapy", "hospital", "clinic",
]

RESEARCH_SOURCE_HINTS = [
    "pubmed.ncbi.nlm.nih.gov",
    "pmc.ncbi.nlm.nih.gov",
    "ncbi.nlm.nih.gov/pmc",
    "doi.org",
    "researchgate",
    "sciencedirect",
    "springer",
    "frontiersin",
    "mdpi",
    "tandfonline",
    "wiley",
    "sagepub",
    "jamanetwork",
    "bmjopen",
]

ALLOW_RESEARCH_EVIDENCE = os.getenv("ALLOW_RESEARCH_EVIDENCE", "0") == "1"

TRUSTED_DOMAINS = [
    *PREFERRED_PRACTICAL_DOMAINS,
    ".gov",
]

BLOCKED_SOURCE_HINTS = [
    "startup", "fastercapital", "advertisement", "product", "shopping",
    "pinterest", "reddit", "quora", "facebook", "instagram", "tiktok",
    "youtube", "youtu.be", "m.youtube",
    "yahoo", "news", "新聞",
    "blog", "部落格",
]

# 非動作主題：外部資料若只是在講營養、水分、疾病、認知、生活常識，不能作為本專案 evidence。
NON_MOTION_TOPIC_HINTS = [
    "hydration", "water intake", "dehydration", "fluid intake", "drink more water",
    "nutrition", "diet", "dementia", "brain health", "cognitive", "memory", "mental health",
]

MOTION_REHAB_HINTS = [
    "rehabilitation", "occupational", "therapy", "exercise", "range of motion",
    "upper limb", "upper extremity", "arm", "hand", "shoulder", "balance",
    "functional", "activities of daily living", "adl", "motor", "mobility", "coordination",
    "patient advice", "exercise instructions", "home exercise", "patient education",
]

# ============================================================
# 2. 基本文字處理
# ============================================================
def ext_normalize_text(text: str) -> str:
    text = re.sub(r"\s+", " ", str(text))
    return text.strip()


def ext_simplify_motion_for_query(motion: str) -> str:
    """
    將雷達輸出的英文動作句變成比較適合搜尋的 query phrase。
    例如：A person drinks water from a cup. -> drinks water from a cup
    """
    text = ext_normalize_text(motion).lower()

    remove_phrases = [
        "a person", "the person", "the subject", "the individual",
        "person", "subject", "individual",
    ]

    for phrase in remove_phrases:
        text = text.replace(phrase, "")

    text = re.sub(r"\s+", " ", text).strip()
    return text.strip(" .")


def ext_is_daily_non_assessment_motion(motion: str) -> bool:
    m = motion.lower()
    return any(k in m for k in DAILY_ACTION_HINTS)


def ext_is_vague_motion(motion: str) -> bool:
    m = motion.lower()
    return any(k in m for k in VAGUE_MOTION_HINTS)


def ext_get_functional_motion_rule(motion: str):
    """
    根據輸入動作句，找到對應的「動作難度 / 復健功能動作」搜尋規則。
    注意：這裡不處理補水、營養、疾病等非動作主題。
    """
    m = motion.lower()
    for rule in FUNCTIONAL_MOTION_RULES:
        keywords_all = rule.get("keywords_all") or []
        keywords_any = rule.get("keywords_any") or []
        if keywords_all and all(k in m for k in keywords_all):
            return rule
        if keywords_any and any(k in m for k in keywords_any):
            return rule
    return None


def ext_unique_keep_order(items: list[str]) -> list[str]:
    """保留順序去重，避免 query 變得太長。"""
    out = []
    seen = set()
    for item in items:
        item = str(item).strip()
        if not item:
            continue
        key = item.lower()
        if key not in seen:
            seen.add(key)
            out.append(item)
    return out


def ext_contains_any(text: str, patterns: list[str]) -> bool:
    text = text.lower()
    return any(p.lower() in text for p in patterns)



# ============================================================
# 2.1 動態動作群組：不是針對固定句子硬寫，而是用「動詞/方向/物件/速度/KG 類別」
#     估計主要功能性動作群組。未來新句子只要包含可辨識動作線索，就能泛化。
# ============================================================
MOVEMENT_GROUP_PROFILES = {
    "single_leg_balance": {
        "title": "單腳站立與平衡控制",
        "query_terms": ["single leg stance", "one leg balance", "balance exercise"],
        "required_any": ["single leg", "one leg", "balance", "fall", "rehabilitation", "exercise", "physical therapy"],
        "components_zh": ["單腳承重", "站姿平衡", "下肢控制", "軀幹穩定"],
        "status": "本次紀錄包含單腳承重、單腳站立或單腳跳相關動作，建議以平衡控制、下肢穩定與跌倒風險作為主要觀察重點。",
        "action": "扶持下單腳站立、短時間單腳承重、腳跟腳尖走或低風險站姿平衡控制練習。",
        "how": "先在牆邊、扶手旁或照護者在旁的環境進行；觀察軀幹是否晃動、骨盆是否歪斜、支撐腳是否穩定，以及是否能安全恢復平衡。",
        "caution": "若出現明顯晃動、無法自行恢復平衡、膝踝疼痛、頭暈或有跌倒風險，應停止並由復健專業人員評估。",
    },
    "sit_to_stand": {
        "title": "坐站轉移與下肢肌力",
        "query_terms": ["sit to stand", "chair rise", "lower limb strength"],
        "required_any": ["sit", "stand", "chair", "lower", "strength", "rehabilitation", "exercise", "physical therapy"],
        "components_zh": ["坐站轉移", "髖膝伸展", "下肢承重", "姿勢控制"],
        "status": "本次紀錄包含坐下、站起或坐站轉移，建議以髖膝伸展、下肢承重與姿勢控制作為觀察重點。",
        "action": "坐站訓練、椅子起立訓練，或穩固椅子上的低負荷下肢功能訓練。",
        "how": "使用穩固椅子，雙腳踩穩地面，必要時扶著扶手或桌面；觀察站起時是否需要借助手臂、膝蓋是否內夾、坐下是否能慢速控制。",
        "caution": "若出現膝蓋疼痛、頭暈、站立不穩、明顯喘不過氣或需要高度扶持，應停止並由醫師、物理治療師或相關專業人員評估。",
    },
    "jump_landing": {
        "title": "跳躍與落地控制",
        "query_terms": ["low impact jumping", "landing control", "lower limb power", "balance training"],
        "required_any": ["jump", "landing", "lower", "balance", "strength", "rehabilitation", "physical therapy", "exercise"],
        "components_zh": ["跳躍", "落地控制", "下肢肌力", "動態平衡"],
        "status": "本次紀錄包含跳躍或較高負荷下肢動作，建議以落地控制、下肢肌力與動態平衡作為主要觀察重點。",
        "action": "低衝擊下肢肌力與落地控制觀察，例如小幅度踏步、低高度重心控制或專業人員指導下的下肢功能訓練。",
        "how": "先降低動作幅度與速度，觀察落地是否穩定、膝蓋是否內夾、是否出現疼痛或失衡；不建議在無扶持或無專業評估下自行增加跳躍強度。",
        "caution": "若有膝踝疼痛、骨質疏鬆、跌倒風險、落地不穩或心肺不適，應停止跳躍類活動並由復健專業人員評估。",
    },
    "bend_squat": {
        "title": "彎身下蹲與下肢控制",
        "query_terms": ["bending down", "squat movement", "functional movement", "lower limb control"],
        "required_any": ["squat", "bending", "lower", "balance", "functional", "rehabilitation", "physical therapy"],
        "components_zh": ["彎身下蹲", "髖膝控制", "軀幹穩定", "下肢承重"],
        "status": "本次紀錄包含彎身、下蹲或接近地面的功能性動作，建議觀察軀幹穩定、髖膝控制與下肢承重能力。",
        "action": "扶持下彎身取物、半蹲控制、髖膝彎曲控制與軀幹穩定練習。",
        "how": "先降低動作幅度，在穩固支撐物旁進行；觀察彎身時背部是否過度代償、膝蓋是否疼痛、起身是否需要扶持。",
        "caution": "若出現腰膝疼痛、起身困難、頭暈或失衡，應停止動作並交由醫師或物理治療師評估。",
    },
    "gait_direction": {
        "title": "步態與方向控制",
        "query_terms": ["gait training", "direction change walking", "turning", "lateral stepping", "balance training"],
        "required_any": ["walking", "gait", "turning", "lateral", "balance", "rehabilitation", "physical therapy", "exercise"],
        "components_zh": ["步態穩定", "方向控制", "動態平衡", "轉身控制"],
        "status": "本次紀錄出現行走、前後左右方向變化或轉身動作，建議以步態穩定、方向控制與動態平衡作為主要觀察重點。",
        "action": "短距離前後行走、左右側步、轉身控制、停走控制與步態穩定練習。",
        "how": "在平坦、無障礙物且可扶持的環境下進行；觀察步伐是否對稱、轉身是否停頓或晃動、方向切換時是否偏離路徑，以及是否需要扶牆或他人協助。",
        "caution": "若出現拖步、突然失衡、轉身困難、頭暈、膝髖疼痛或需要高度扶持，應停止並由物理治療師或復健專業人員評估。",
    },
    "upper_trunk": {
        "title": "上肢與軀幹控制",
        "query_terms": ["upper limb exercise", "shoulder range of motion", "trunk control", "occupational therapy"],
        "required_any": ["upper", "arm", "shoulder", "trunk", "range of motion", "rehabilitation", "occupational", "exercise"],
        "components_zh": ["肩關節抬舉", "上肢活動度", "手臂控制", "軀幹穩定"],
        "status": "本次紀錄包含抬手、手臂移動或軀幹控制動作，建議以上肢活動度、肩肘控制與軀幹穩定為主要觀察重點。",
        "action": "肩關節活動度、抬手控制、手臂前後移動與軀幹穩定下的上肢動作練習。",
        "how": "先從小範圍、低速度動作開始；觀察是否聳肩、身體歪斜、手臂活動角度受限，或需要軀幹大幅代償才能完成。",
        "caution": "若肩頸疼痛、手臂麻木、活動角度明顯受限或動作需要身體大幅代償，應停止並由醫師、物理治療師或職能治療師評估。",
    },
    "hand_function": {
        "title": "手部功能與日常操作",
        "query_terms": ["hand function", "grasping task", "activities of daily living", "occupational therapy"],
        "required_any": ["hand", "grasp", "functional", "occupational", "therapy", "adl", "upper", "rehabilitation"],
        "components_zh": ["抓握", "手眼協調", "手部精細動作", "日常功能性任務"],
        "status": "本次紀錄包含抓握、持物、閱讀、喝水或日常物品操作，建議以手部功能、上肢協調與坐姿控制作為觀察重點。",
        "action": "握持物品、抬手至目標位置、手眼協調、抓握控制與日常功能性上肢任務練習。",
        "how": "觀察是否能穩定握持物品、手部是否抖動、是否能把物品移到目標位置、坐姿是否穩定，以及是否需要他人協助。",
        "caution": "若出現抓握困難、手抖明顯、肩肘疼痛、姿勢不穩或吞嚥安全疑慮，應由職能治療師、物理治療師或相關專業人員進一步評估。",
    },
    "endurance": {
        "title": "耐力與活動耐受度",
        "query_terms": ["low intensity endurance exercise", "marching in place", "aerobic exercise tolerance"],
        "required_any": ["endurance", "aerobic", "walking", "exercise", "older adults", "rehabilitation"],
        "components_zh": ["連續活動", "活動耐受度", "呼吸與疲勞觀察"],
        "status": "本次紀錄包含較快或連續性的活動，建議以活動耐受度、疲勞程度與恢復情形作為觀察重點。",
        "action": "低強度連續活動觀察，例如原地踏步、短距離步行或可中止的低負荷耐力活動。",
        "how": "以短時間、低強度、可立即停止的方式觀察；注意呼吸、疲勞程度、速度是否明顯下降，以及活動後恢復時間。",
        "caution": "若出現胸悶、明顯喘不過氣、頭暈、臉色蒼白或異常疲勞，應立即停止並尋求專業協助。",
    },
    "functional_movement": {
        "title": "功能性動作控制",
        "query_terms": ["functional movement", "motor control", "balance", "rehabilitation"],
        "required_any": ["functional", "movement", "motor", "balance", "rehabilitation", "exercise"],
        "components_zh": ["功能性動作控制", "姿勢穩定", "動作協調"],
        "status": "本次紀錄包含可觀察的功能性動作，但目前無法穩定歸入單一能力類別，建議先以動作品質與安全性作為觀察重點。",
        "action": "功能性動作穩定度、姿勢控制與動作協調觀察練習。",
        "how": "在安全環境下觀察動作是否能完整、穩定且連續完成；確認是否需要扶持、是否出現代償、速度是否過慢或路徑是否不穩。",
        "caution": "若動作無法辨識或安全性不足，不建議產生正式復健建議，應先人工複核原始影像或由專業人員評估。",
    },
}

GROUP_PRIORITY = [
    "single_leg_balance",
    "sit_to_stand",
    "jump_landing",
    "bend_squat",
    "gait_direction",
    "upper_trunk",
    "hand_function",
    "endurance",
    "functional_movement",
]

GROUP_KEYWORDS = {
    "single_leg_balance": [r"\bone\s+(foot|leg)\b", r"\bsingle[-\s]?leg\b", r"\bhop(s|ping)?\b"],
    "sit_to_stand": [r"\bsit(s|ting)?\b", r"\bsit\s+down\b", r"\bstand(s|ing)?\s+up\b", r"\bchair\s+rise\b"],
    "jump_landing": [r"\bjump(s|ing|ed)?\b", r"\bleap(s|ing|ed)?\b"],
    "bend_squat": [r"\bbend(s|ing)?\b", r"\bsquat(s|ting)?\b", r"\bcrouch\b", r"\bpick(s)?\s+up\b"],
    "gait_direction": [r"\bwalk(s|ing|ed)?\b", r"\bstep(s|ping)?\b", r"\bforward\b", r"\bbackward\b", r"\bleft\b", r"\bright\b", r"\bturn(s|ing)?\b", r"\bsideways\b", r"\blateral\b"],
    "upper_trunk": [r"\braise(s|d)?\b", r"\barm(s)?\b", r"\bhand(s)?\b", r"\breach(es|ing)?\b", r"\bwave(s|ing)?\b", r"\bpoint(s|ing)?\b", r"\btrunk\b", r"\btorso\b"],
    "hand_function": [r"\bhold(s|ing)?\b", r"\bgrasp(s|ing)?\b", r"\bgrab(s|bing)?\b", r"\bcup\b", r"\bdrink(s|ing)?\b", r"\bbook\b", r"\bphone\b", r"\btowel\b", r"\bdoor\b", r"\bopen(s|ing)?\b", r"\bclap(s|ping)?\b", r"\bobject\b"],
    "endurance": [r"\brun(s|ning)?\b", r"\bjog(s|ging)?\b", r"\bmarch(es|ing)?\b"],
}

METRIC_TO_GROUP = {
    "下肢肌力": "sit_to_stand",
    "單腳站立": "single_leg_balance",
    "空間方位位移": "gait_direction",
    "來回行走": "gait_direction",
    "步行速度": "gait_direction",
    "上肢與軀幹活動": "upper_trunk",
    "心肺耐力": "endurance",
}


def ext_keyword_score(text: str, patterns: list[str]) -> int:
    score = 0
    for pat in patterns:
        if re.search(pat, text, flags=re.I):
            score += 1
    return score


def ext_groups_in_text(text: str) -> list[str]:
    """回傳某段文字命中的 movement groups。"""
    groups = []
    for key in GROUP_PRIORITY:
        if key == "functional_movement":
            continue
        if ext_keyword_score(text, GROUP_KEYWORDS.get(key, [])) > 0:
            groups.append(key)
    return groups


def ext_first_meaningful_segment(motion: str) -> str:
    """
    取第一個真正有動作意義的片段。
    用於複合動作判斷主類別，避免後面的 walking/方向詞把前面的抬手、單腳等主動作蓋掉。
    """
    m = ext_normalize_text(motion).lower()
    parts = re.split(r"(?:->|;|,|\bthen\b|\bfollowed by\b|\band then\b)", m)
    skip_terms = ["stay", "stays", "still", "stationary", "pause", "pauses", "remain", "remains"]
    for part in parts:
        part = part.strip(" .")
        if not part:
            continue
        if any(k in part for k in skip_terms) and not ext_groups_in_text(part):
            continue
        if ext_groups_in_text(part):
            return part
    return m


def ext_select_primary_group(motion: str, metric: Optional[str] = None, speed: Optional[str] = None) -> dict:
    """
    選出「主要」功能性動作群組。
    v6.1：先看第一個有意義的動作片段，再看整句和 KG metric。
    這樣「raises one arm -> moves backward」會以「上肢」為主，不會被後面的方向詞帶成步態。
    """
    m = ext_normalize_text(motion).lower()
    first_seg = ext_first_meaningful_segment(m)
    metric = (metric or "").strip()
    speed = (speed or "").strip().lower()

    raw_scores = {key: ext_keyword_score(m, pats) for key, pats in GROUP_KEYWORDS.items()}
    first_groups = ext_groups_in_text(first_seg)

    if speed == "fast":
        raw_scores["endurance"] = raw_scores.get("endurance", 0) + 1

    metric_group = METRIC_TO_GROUP.get(metric)
    if metric_group:
        raw_scores[metric_group] = raw_scores.get(metric_group, 0) + 1

    # 第一個有意義動作片段優先。若第一段是上肢/單腳/坐站等，避免後續移動詞把 query 帶偏。
    if first_groups:
        # 在第一段中仍依 priority 排，避免 one foot + hop 被 jump_landing 搶走。
        for key in GROUP_PRIORITY:
            if key in first_groups:
                primary = key
                break
    else:
        # fallback：依任務特異性，而不是單純分數，避免 gait 太常出現。
        if raw_scores.get("single_leg_balance", 0) > 0:
            primary = "single_leg_balance"
        elif raw_scores.get("sit_to_stand", 0) > 0:
            primary = "sit_to_stand"
        elif raw_scores.get("jump_landing", 0) > 0:
            primary = "jump_landing"
        elif raw_scores.get("bend_squat", 0) > 0:
            primary = "bend_squat"
        elif raw_scores.get("upper_trunk", 0) > 0:
            primary = "upper_trunk"
        elif raw_scores.get("hand_function", 0) > 0:
            primary = "hand_function"
        elif raw_scores.get("gait_direction", 0) > 0:
            primary = "gait_direction"
        elif raw_scores.get("endurance", 0) > 0:
            primary = "endurance"
        elif metric_group:
            primary = metric_group
        else:
            primary = "functional_movement"

    matched_groups = [k for k in GROUP_PRIORITY if raw_scores.get(k, 0) > 0]
    if primary not in matched_groups and primary != "functional_movement":
        matched_groups.insert(0, primary)

    profile = MOVEMENT_GROUP_PROFILES[primary]
    secondary = [k for k in matched_groups if k != primary]

    return {
        "group_key": primary,
        "group_title": profile["title"],
        "profile": profile,
        "matched_groups": matched_groups,
        "secondary_groups": secondary,
        "scores": raw_scores,
        "first_segment": first_seg,
    }


def ext_extract_dynamic_motion_concepts(
    motion: str,
    metric: Optional[str] = None,
    speed: Optional[str] = None,
) -> Dict:
    """
    動態外部搜尋核心 v6：
    - 不是對固定句子寫死規則。
    - 使用 broad movement group 估計主要動作群。
    - 查詢只用 primary group，避免所有詞混在一起造成搜尋主題漂移。
    """
    selected = ext_select_primary_group(motion, metric=metric, speed=speed)
    profile = selected["profile"]

    reject_terms = ext_unique_keep_order(list(NON_MOTION_TOPIC_HINTS))
    m = ext_normalize_text(motion).lower()
    if any(k in m for k in ["drink", "water", "cup", "book", "phone", "screen"]):
        reject_terms += [
            "hydration", "water intake", "dehydration", "fluid intake", "nutrition",
            "dementia", "brain health", "cognitive", "memory", "smartphone addiction",
            "office chair", "furniture", "cleaning tips",
        ]

    # 完全沒有可辨識動作且 metric 也無法補時，才視為不可搜尋。
    has_signal = selected["group_key"] != "functional_movement" or any(selected["scores"].values()) or (metric in METRIC_TO_GROUP)

    return {
        "group_key": selected["group_key"],
        "group_title": selected["group_title"],
        "matched_groups": selected["matched_groups"],
        "secondary_groups": selected["secondary_groups"],
        "action_terms": profile["query_terms"],
        "component_terms": profile["required_any"],
        "components_zh": profile["components_zh"],
        "topic_parts": [profile["title"]],
        "reject_terms": ext_unique_keep_order(reject_terms),
        "has_signal": has_signal,
    }


def ext_build_dynamic_query_plan(
    motion: str,
    metric: Optional[str] = None,
    speed: Optional[str] = None,
) -> Dict:
    """
    動態 query 生成 v6：
    - 主群組搜尋，不把複合動作所有詞全部塞進 query。
    - 優先產生專業/醫療/復健網站查詢變體。
    """
    motion_clean = ext_normalize_text(motion)
    concepts = ext_extract_dynamic_motion_concepts(motion_clean, metric=metric, speed=speed)

    if not concepts.get("has_signal"):
        return {
            "should_search": False,
            "query": "",
            "query_variants": [],
            "reason": "無法從原始句子抽出明確功能性動作或動作組成，避免採用不相關外部資料",
            "required_terms": [],
            "required_any": [],
            "action_any": [],
            "reject_terms": concepts["reject_terms"],
            "movement_components": "",
            "topic": "未知動作難度",
            "group_key": "functional_movement",
            "secondary_groups": [],
        }

    profile = MOVEMENT_GROUP_PROFILES[concepts["group_key"]]
    base_terms = ext_unique_keep_order(profile["query_terms"] + [
        "older adults",
        "physical therapy",
        "occupational therapy",
        "rehabilitation",
        "exercise instructions",
        "patient education",
    ])
    base_query = " ".join(base_terms)

    # 專業來源優先；最後才用一般查詢。不要一次塞太多 site，避免 DDG 誤判。
    preferred_sites = [
        "choosept.com",
        "nhs.uk",
        "physio-pedia.com",
        "apta.org",
        "aota.org",
        "mayoclinic.org",
        "clevelandclinic.org",
        "betterhealth.vic.gov.au",
        "nia.nih.gov",
    ]
    query_variants = [f"{base_query} site:{site}" for site in preferred_sites]
    query_variants.append(base_query)
    query_variants = query_variants[:max(1, SEARCH_VARIANT_LIMIT)]
    if base_query not in query_variants:
        query_variants.append(base_query)

    return {
        "should_search": True,
        "query": base_query,
        "query_variants": query_variants,
        "reason": "由主要功能性動作群組自動生成外部搜尋 query，避免固定句子規則與複合動作混查",
        "required_terms": profile["required_any"],
        "required_any": profile["required_any"],
        "action_any": profile["query_terms"] + profile["required_any"],
        "reject_terms": concepts["reject_terms"],
        "movement_components": "、".join(profile["components_zh"]),
        "topic": profile["title"],
        "group_key": concepts["group_key"],
        "secondary_groups": concepts["secondary_groups"],
    }

def ext_should_skip_non_motion_topic(motion: str) -> bool:
    """
    若句子看起來完全沒有可分析的肢體動作，才跳過。
    一般日常動作不再直接跳過，而是嘗試轉成功能性動作難度搜尋。
    """
    m = motion.lower()
    no_motion_terms = ["looks", "seems", "stays", "still", "no action", "nothing about movement"]
    return any(k in m for k in no_motion_terms) and not ext_get_functional_motion_rule(motion)


def ext_is_trusted_domain(url: str) -> bool:
    url = url.lower()
    return any(domain in url for domain in TRUSTED_DOMAINS)


def ext_is_blocked_source(title: str, url: str, snippet: str) -> bool:
    text = f"{title} {url} {snippet}".lower()
    return any(k in text for k in BLOCKED_SOURCE_HINTS)


def ext_is_research_source(title: str, url: str, snippet: str = "") -> bool:
    """
    判斷來源是否偏論文/研究資料。
    使用者報告預設要優先採用醫療、醫學、復健科、物理/職能治療等實務建議網站，
    所以 PubMed/PMC/期刊頁預設不當作主要 external evidence。
    """
    text = f"{title} {url} {snippet}".lower()
    if any(h in text for h in RESEARCH_SOURCE_HINTS):
        return True
    research_words = [
        "journal", "systematic review", "meta-analysis", "randomized trial",
        "clinical trial", "study protocol", "abstract", "publication",
    ]
    return any(w in text for w in research_words)


def ext_source_priority(title: str, url: str, snippet: str = "") -> int:
    """
    來源排序分數，越高越優先。
    5：專業協會/政府/醫院/復健科/物理治療/職能治療實務網站
    4：可信醫療健康教育網站
    3：復健/物理治療/職能治療診所或醫療機構文章
    2：一般健康網站備援
    1：研究/論文來源（預設不用，除非 ALLOW_RESEARCH_EVIDENCE=1）
    0：其他
    """
    u = str(url).lower()
    text_all = f"{title} {url} {snippet}".lower()

    if ext_is_english_or_empty_source(title, url, snippet):
        return 0

    if ext_is_research_source(title, url, snippet):
        return 1

    # 專業協會/政府/醫院/復健系統優先
    top_domains = [
        "choosept.com", "apta.org", "aota.org", "nhs.uk", "nia.nih.gov", "cdc.gov",
        "medlineplus.gov", "physio-pedia.com", "hss.edu", "mskcc.org",
        "mayoclinic.org", "clevelandclinic.org", "betterhealth.vic.gov.au",
        "myhealth.alberta.ca", "healthlinkbc.ca", "kaiserpermanente.org",
    ]
    if any(d in u for d in top_domains):
        return 5

    if any(d in u for d in PREFERRED_PRACTICAL_DOMAINS):
        return 4

    if any(h in u for h in REHAB_CLINIC_DOMAIN_HINTS) or any(
        h in text_all for h in ["physical therapy", "occupational therapy", "physiotherapy", "rehabilitation", "patient education"]
    ):
        return 3

    if any(d in u for d in GENERAL_HEALTH_FALLBACK_DOMAINS):
        return 2

    if any(d in u for d in [".gov", "hospital", "clinic"]):
        return 2

    return 0


# ============================================================
# 3. Cache
# ============================================================
def ext_load_search_cache() -> dict:
    if not CACHE_PATH.exists():
        return {}
    try:
        with open(CACHE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def ext_save_search_cache(cache: dict) -> None:
    try:
        with open(CACHE_PATH, "w", encoding="utf-8") as f:
            json.dump(cache, f, ensure_ascii=False, indent=2)
    except Exception:
        pass

# ============================================================
# 4. Search plan：先判斷要不要搜
# ============================================================
def ext_build_search_plan(
    motion: str,
    metric: Optional[str] = None,
    speed: Optional[str] = None,
) -> Dict:
    """
    決定是否要外部搜尋。

    v4 優化重點：
    1. 不再把外部搜尋綁死在固定 FUNCTIONAL_MOTION_RULES。
    2. 先從「原始句子 + KG 能力項目 + 速度」抽取功能性動作詞與動作組成詞。
    3. 用抽出的詞自動生成 query，讓新動作也能搜尋。
    4. 只有在完全抽不出明確動作，且句子又很模糊時，才跳過外部搜尋。
    """
    motion_clean = ext_normalize_text(motion)

    # 先用動態 query builder，避免未來新動作都要手寫規則。
    dynamic_plan = ext_build_dynamic_query_plan(
        motion=motion_clean,
        metric=metric,
        speed=speed,
    )

    if dynamic_plan["should_search"]:
        return dynamic_plan

    # 如果抽不出概念且本身就是模糊動作，明確標記為模糊，不搜尋。
    if ext_is_vague_motion(motion_clean):
        dynamic_plan.update({
            "reason": "動作描述過於模糊，無法判斷具體動作難度；外部搜尋容易取得不相關資料",
            "topic": "模糊動作",
        })
        return dynamic_plan

    # 沒有明確動作概念時，仍不搜尋，避免把新句子亂導到老人運動總論。
    return dynamic_plan


# ============================================================
# 5. 搜尋與網頁抓取
# ============================================================
def ext_web_search(query: str, max_results: int = 8) -> List[Dict]:
    """
    使用 DuckDuckGo 搜尋外部資料。
    安全版：
    1. 有 timeout，避免卡死。
    2. 有 cache，避免同 query 重複搜尋。
    3. 搜尋失敗就回空 list，不中斷主流程。
    """
    query = ext_normalize_text(query)
    cache = ext_load_search_cache()

    if query in cache:
        cached = cache[query]
        cached = sorted(
            cached,
            key=lambda x: ext_source_priority(x.get("title", ""), x.get("url", ""), x.get("snippet", "")),
            reverse=True,
        )
        return cached[:max_results]

    if not ENABLE_LIVE_SEARCH:
        print(f"[external_search] live search disabled, skip query: {query}")
        cache[query] = []
        ext_save_search_cache(cache)
        return []

    results: List[Dict] = []

    try:
        try:
            ddgs_obj = DDGS(timeout=SEARCH_TIMEOUT_SEC)
        except TypeError:
            ddgs_obj = DDGS()

        with ddgs_obj as ddgs:
            for r in ddgs.text(
                query,
                max_results=max_results,
                region="wt-wt",
                safesearch="moderate",
            ):
                title = r.get("title", "")
                url = r.get("href") or r.get("url", "")
                snippet = r.get("body", "")

                if not url:
                    continue

                results.append({
                    "title": ext_normalize_text(title),
                    "url": url,
                    "snippet": ext_normalize_text(snippet),
                })

                if len(results) >= max_results:
                    break

    except Exception as e:
        print(f"[external_search] search failed or timeout: {e}")
        results = []

    results = sorted(
        results,
        key=lambda x: ext_source_priority(x.get("title", ""), x.get("url", ""), x.get("snippet", "")),
        reverse=True,
    )

    cache[query] = results
    ext_save_search_cache(cache)
    return results[:max_results]


def ext_fetch_page_text(url: str, timeout: int = 5, max_chars: int = 3000) -> str:
    """
    抓網頁文字。Jetson 版限制 max_chars，避免吃太多記憶體。
    """
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (X11; Linux x86_64) "
            "AppleWebKit/537.36 Chrome/120 Safari/537.36"
        )
    }

    try:
        resp = requests.get(url, headers=headers, timeout=timeout)
        resp.raise_for_status()

        # 優先用 requests 判斷到的編碼，避免中文頁被解成亂碼。
        if not resp.encoding or resp.encoding.lower() in ["iso-8859-1", "ascii"]:
            resp.encoding = resp.apparent_encoding or "utf-8"

        soup = BeautifulSoup(resp.text, "html.parser")
        for tag in soup(["script", "style", "nav", "footer", "header", "aside"]):
            tag.decompose()

        paragraphs = []
        for p in soup.find_all(["p", "li", "h1", "h2", "h3"]):
            text = ext_normalize_text(p.get_text(" "))
            if len(text) >= 30:
                paragraphs.append(text)

        full_text = " ".join(paragraphs)
        return full_text[:max_chars]

    except Exception as e:
        print(f"[external_search] fetch failed: {url} | {e}")
        return ""

# ============================================================
# 6. Evidence relevance filter
# ============================================================
def ext_is_relevant_evidence(item: Dict, plan: Dict) -> bool:
    """
    外部 evidence 相關性檢查。
    核心原則：只接受「動作難度 / 復健 / occupational therapy / functional task」相關內容。
    例如喝水搜尋結果若只談 hydration / water intake，必須排除。
    """
    title = item.get("title", "")
    url = item.get("url", "")
    snippet = item.get("snippet", "")
    text = f"{title} {snippet} {url}".lower()

    if ext_is_blocked_source(title, url, snippet):
        return False

    # 使用者報告優先要「醫療/醫學/復健科/物理治療/職能治療」實務建議。
    # PubMed、PMC、期刊論文頁預設排除；若需研究型 evidence，執行時設 ALLOW_RESEARCH_EVIDENCE=1。
    if ext_is_research_source(title, url, snippet) and not ALLOW_RESEARCH_EVIDENCE:
        return False

    # 來源太不專業且不是可信健康/復健網站時，不採用，避免整體建議被泛用文章帶偏。
    if ext_source_priority(title, url, snippet) <= 0:
        return False

    reject_terms = set(plan.get("reject_terms", []) or []) | set(NON_MOTION_TOPIC_HINTS)
    reject_hit = any(term.lower() in text for term in reject_terms)

    rehab_hit = any(k in text for k in MOTION_REHAB_HINTS)
    required_any = plan.get("required_any", []) or plan.get("required_terms", []) or []
    action_any = plan.get("action_any", []) or []

    required_hit = True if not required_any else any(term.lower() in text for term in required_any)
    action_hit = True if not action_any else any(term.lower() in text for term in action_any)

    older_hit = any(
        k in text
        for k in ["older adult", "older adults", "senior", "seniors", "elderly", "aging", "ageing"]
    )

    # 如果命中非動作主題，但沒有同時命中復健/動作語彙，排除。
    if reject_hit and not rehab_hit:
        return False

    # 可信來源仍需與動作/復健或功能性任務相關，不能只因 domain 可信就採用。
    if ext_is_trusted_domain(url) and required_hit and (action_hit or rehab_hit):
        return True

    if (older_hit or ext_is_trusted_domain(url)) and required_hit and action_hit and (rehab_hit or "functional" in text):
        return True

    # 對於非常精準的功能性任務頁面，允許沒有 older adults，但必須同時有 action + rehab。
    if required_hit and action_hit and rehab_hit:
        return True

    return False

# ============================================================
# 7. 主函式：產生 evidence bundle
# ============================================================
def ext_collect_external_evidence(
    motion: str,
    metric: Optional[str] = None,
    speed: Optional[str] = None,
    max_results: int = 1,
    max_pages: int = 0,
) -> Dict:
    """
    外部搜尋主函式 v6：
    1. 先產生 primary movement group plan。
    2. 優先嘗試專業網站 query variants。
    3. 若 STRICT_PROFESSIONAL_SOURCES=1，排除一般健康文與論文；若完全找不到，再回傳無 evidence。
    4. 主流程不因外部搜尋失敗而中斷。
    """
    plan = ext_build_search_plan(motion=motion, metric=metric, speed=speed)

    if not plan["should_search"]:
        return {
            "used_external_search": False,
            "external_search_skipped": True,
            "skip_reason": plan["reason"],
            "query": "",
            "query_attempts": [],
            "motion": motion,
            "metric": metric,
            "speed": speed,
            "topic": plan["topic"],
            "group_key": plan.get("group_key", "functional_movement"),
            "secondary_groups": plan.get("secondary_groups", []),
            "movement_components": plan.get("movement_components", ""),
            "is_daily_non_assessment_motion": ext_is_daily_non_assessment_motion(motion),
            "evidence": [],
        }

    query_variants = plan.get("query_variants") or [plan["query"]]
    query_attempts = []
    evidence_items = []

    for q in query_variants:
        q = ext_normalize_text(q)
        if not q or q in query_attempts:
            continue
        query_attempts.append(q)

        search_results = ext_web_search(query=q, max_results=max(max_results, 8))

        for item in search_results:
            if not ext_is_relevant_evidence(item, plan):
                continue

            # 嚴格模式：使用者報告優先採用專業/醫療/復健實務網站，不採一般健康站做主 evidence。
            if STRICT_PROFESSIONAL_SOURCES and ext_source_priority(item.get("title", ""), item.get("url", ""), item.get("snippet", "")) < 3:
                continue

            page_text = ""
            if max_pages > 0 and len(evidence_items) < max_pages:
                page_text = ext_fetch_page_text(item["url"], timeout=5, max_chars=3000)

            item_out = {
                "title": item["title"],
                "url": item["url"],
                "snippet": item["snippet"],
                "page_text": page_text,
                "source_priority": ext_source_priority(item.get("title", ""), item.get("url", ""), item.get("snippet", "")),
            }
            evidence_items.append(item_out)
            break

        if evidence_items:
            break

    return {
        "used_external_search": True,
        "external_search_skipped": False,
        "skip_reason": "" if evidence_items else "外部搜尋結果未通過專業來源與相關性檢查，未採用外部 evidence。",
        "query": plan["query"],
        "query_attempts": query_attempts,
        "motion": motion,
        "metric": metric,
        "speed": speed,
        "topic": plan["topic"],
        "group_key": plan.get("group_key", "functional_movement"),
        "secondary_groups": plan.get("secondary_groups", []),
        "movement_components": plan.get("movement_components", ""),
        "is_daily_non_assessment_motion": ext_is_daily_non_assessment_motion(motion),
        "evidence": evidence_items[:1],
    }


def ext_build_external_prompt(evidence_bundle: Dict) -> str:
    """
    保留給未來 LLM 生成使用。
    目前 Project1.py 先用 template，不逐筆呼叫 LLM，避免 Jetson 記憶體爆掉。
    """
    motion = evidence_bundle.get("motion", "")
    metric = evidence_bundle.get("metric") or "未知能力項目"
    speed = evidence_bundle.get("speed") or "未知速度"
    is_daily = evidence_bundle.get("is_daily_non_assessment_motion", False)

    evidence_texts = []
    for i, item in enumerate(evidence_bundle.get("evidence", [])[:3], start=1):
        evidence_texts.append(
            f"來源 {i}\n"
            f"標題：{item.get('title', '')[:120]}\n"
            f"日期：{item.get('evidence_date', '') or '未知'}\n"
            f"網址：{item.get('url', '')}\n"
            f"摘要：{item.get('snippet', '')[:300]}\n"
            f"內文節錄：{item.get('page_text', '')[:600]}\n"
        )

    evidence_block = "\n".join(evidence_texts)

    prompt = f"""
你是長者動作難度與復健動作建議系統中的「外部搜尋補強模組」。
請根據外部搜尋資料，生成與「動作難度、功能性任務、復健動作建議」相關的保守建議。

【重要限制】
1. 只能討論動作難度、姿勢控制、上肢/下肢活動、平衡、功能性任務與復健訓練。
2. 不可以把喝水解讀為補水或飲食建議；喝水只能視為握杯、抬杯、送至口部等功能性動作。
3. 不可以做醫療診斷。
4. 不可以取代醫師、物理治療師或職能治療師。
5. 不可以編造來源沒有提到的具體療程。
6. 如果資料不足，請明確說「外部資料不足，僅提供動作觀察與人工複核建議」。
7. 請優先整理兩個以上來源共同支持的內容；只有單一來源提到的內容，請用「可考慮」「可觀察」，不要寫成強制建議。
8. 請依來源編號標示建議依據，例如 [來源1][來源2]。

【輸入資料】
動作描述：{motion}
速度：{speed}
目前能力項目：{metric}
是否可能為日常非評估動作：{is_daily}

【外部搜尋資料】
{evidence_block}

【請輸出固定格式】
能力項目：
判斷說明：
外部補充建議：
注意事項：
資料來源類型：
信心程度：
"""
    return prompt.strip()


def external_search_fallback(
    motion: str,
    metric: Optional[str] = None,
    speed: Optional[str] = None,
    max_results: int = 1,
    max_pages: int = 0,
) -> Dict:
    """
    Project1.py 直接呼叫這個。
    回傳 evidence_bundle 和 prompt。
    """
    evidence_bundle = ext_collect_external_evidence(
        motion=motion,
        metric=metric,
        speed=speed,
        max_results=max_results,
        max_pages=max_pages,
    )
    prompt = ext_build_external_prompt(evidence_bundle)
    return {
        "evidence_bundle": evidence_bundle,
        "prompt": prompt,
    }

# ============================================================
# v7 overrides：連續動作拆解 + 中文專業外部搜尋 + 相關性強化
# ============================================================
# 這一段覆蓋前面 v6 的同名函式；保留原本主流程，但修正：
# 1) 複合動作不只看第一段，而是拆成多個子動作逐項分析。
# 2) 外部搜尋 query 優先使用中文，並偏向中文醫療/復健/物理治療衛教網頁。
# 3) 專業度與內容相關性並重；若專業來源不相關，不採用。
# 4) KG 缺欄位補足時，依 KG 類別與 motion 內容選擇最合理動作群，不再把下肢肌力誤補成上肢。

# 重新設定搜尋數量：多查幾個 query 變體，讓 relevance filter 有機會挑到中文且相關的頁面。
SEARCH_VARIANT_LIMIT = int(os.getenv("SEARCH_VARIANT_LIMIT", "10"))
SEARCH_REGION = os.getenv("SEARCH_REGION", "tw-tzh")

# 中文醫療/復健/衛教來源加權。不是硬綁定，只是排序與查詢變體優先。
CHINESE_PRACTICAL_DOMAINS = [
    "vghtpe.gov.tw", "vghtc.gov.tw", "vghks.gov.tw", "ntuh.gov.tw", "cgmh.org.tw",
    "kmuh.org.tw", "nckuh.hosp.ncku.edu.tw", "tpech.gov.taipei", "shh.org.tw",
    "edah.org.tw", "taichung.gov.tw", "health99.hpa.gov.tw", "hpa.gov.tw", "mohw.gov.tw",
    "rehab.org.tw", "tpta.org.tw", "pt.org.tw", "ot.org.tw",
]

# 中文復健/醫療語彙，用於 relevance filter。
CHINESE_REHAB_HINTS = [
    "復健", "復健科", "物理治療", "職能治療", "治療師", "衛教", "運動", "訓練",
    "關節活動度", "肌力", "平衡", "步態", "行走", "轉身", "側走", "坐站", "單腳站", "肩關節",
    "上肢", "下肢", "軀幹", "抓握", "功能性", "動作控制", "長者", "老人", "銀髮",
]

# 症狀/疾病頁面常常很專業但不相關，例如抬手卻搜到頸椎神經根病。預設排除。
SYMPTOM_DISEASE_TOPIC_HINTS = [
    "cervical radiculopathy", "radiculopathy", "pinched nerve", "neck pain", "sciatica",
    "椎間盤", "神經根", "頸椎", "坐骨神經", "麻木", "疼痛治療", "手麻", "肩頸痛",
]

GROUP_CHINESE_QUERY_TERMS = {
    "single_leg_balance": ["單腳站立", "平衡訓練", "跌倒預防", "長者", "物理治療", "復健", "衛教"],
    "sit_to_stand": ["坐站訓練", "椅子起立", "下肢肌力", "長者", "物理治療", "復健", "衛教"],
    "jump_landing": ["低衝擊", "下肢肌力", "落地控制", "動態平衡", "長者", "物理治療", "復健", "衛教"],
    "bend_squat": ["彎腰", "下蹲", "取物", "下肢控制", "軀幹穩定", "長者", "物理治療", "復健", "衛教"],
    "gait_direction": ["步態訓練", "行走訓練", "轉身", "側向步行", "動態平衡", "長者", "物理治療", "復健", "衛教"],
    "upper_trunk": ["抬手", "肩關節活動度", "上肢運動", "軀幹穩定", "長者", "職能治療", "物理治療", "復健", "衛教"],
    "hand_function": ["抓握訓練", "手部功能", "日常生活活動", "上肢協調", "長者", "職能治療", "復健", "衛教"],
    "endurance": ["低強度耐力", "原地踏步", "步行運動", "活動耐受度", "長者", "物理治療", "復健", "衛教"],
    "functional_movement": ["功能性動作", "動作控制", "姿勢穩定", "長者", "物理治療", "復健", "衛教"],
}

GROUP_SPECIFIC_REJECT = {
    # 上肢抬手任務不應採用頸椎神經根病、夾擠神經、症狀診斷頁。
    "upper_trunk": SYMPTOM_DISEASE_TOPIC_HINTS + ["cervical", "neck", "nerve", "radicular"],
    # 手部功能不應偏向認知或手機成癮。
    "hand_function": ["dementia", "cognitive", "memory", "addiction", "失智", "認知", "記憶", "成癮"],
    # 喝水/杯子任務不應偏向補水營養。
    "functional_movement": [],
}

# 補進 profile，不改主架構。
for _g, _terms in GROUP_CHINESE_QUERY_TERMS.items():
    if _g in MOVEMENT_GROUP_PROFILES:
        MOVEMENT_GROUP_PROFILES[_g]["query_terms_zh"] = _terms
        MOVEMENT_GROUP_PROFILES[_g]["reject_extra"] = GROUP_SPECIFIC_REJECT.get(_g, [])


def ext_source_priority(title: str, url: str, snippet: str = "") -> int:
    """
    v7 來源排序：中文醫療/復健衛教 > 國際專業 PT/OT/醫療 > 相關一般健康站 > 論文/其他。
    專業但不相關的疾病頁會在 relevance filter 被排除。
    """
    u = str(url).lower()
    text_all = f"{title} {url} {snippet}".lower()

    if ext_is_english_or_empty_source(title, url, snippet):
        return 0

    if ext_is_research_source(title, url, snippet):
        return 1

    if any(d in u for d in CHINESE_PRACTICAL_DOMAINS):
        return 6

    if any(h in text_all for h in ["復健", "物理治療", "職能治療", "衛教", "復健科", "醫院"]):
        return 5

    top_domains = [
        "choosept.com", "apta.org", "aota.org", "nhs.uk", "nia.nih.gov", "cdc.gov",
        "medlineplus.gov", "physio-pedia.com", "hss.edu", "mskcc.org",
        "mayoclinic.org", "clevelandclinic.org", "betterhealth.vic.gov.au",
        "myhealth.alberta.ca", "healthlinkbc.ca", "kaiserpermanente.org",
    ]
    if any(d in u for d in top_domains):
        return 5

    if any(d in u for d in PREFERRED_PRACTICAL_DOMAINS):
        return 4

    if any(h in u for h in REHAB_CLINIC_DOMAIN_HINTS) or any(
        h in text_all for h in ["physical therapy", "occupational therapy", "physiotherapy", "rehabilitation", "patient education"]
    ):
        return 3

    if any(d in u for d in GENERAL_HEALTH_FALLBACK_DOMAINS):
        return 2

    if any(d in u for d in [".gov", ".edu", ".gov.tw", ".edu.tw", "hospital", "clinic"]):
        return 2

    return 0


def ext_split_motion_action_segments(motion: str) -> list[str]:
    """
    將連續動作拆成多個子動作。這不是固定句子 hardcode，
    只是根據常見連接符號/連接詞切分：->, then, followed by, and then, 分號等。
    """
    text = ext_normalize_text(motion).lower()
    # 不要用普通逗號盲切，避免自然語句被切太碎；只有 then 類或箭頭才切。
    parts = re.split(r"(?:\s*->\s*|\s*;\s*|\bthen\b|\bfollowed by\b|\band then\b)", text)
    cleaned: list[str] = []
    for p in parts:
        p = p.strip(" .,-")
        p = re.sub(r"^(a|the)\s+(person|subject|individual)\s+", "", p).strip()
        if not p:
            continue
        # 純靜止/停頓是狀態，不當作復健動作項目；但保留 stop 若和 gait 搭配在整句裡由 gait 處理。
        if re.fullmatch(r"(stays?\s+still|stays?\s+stationary|pauses?|stops?|remains?\s+still)", p):
            continue
        cleaned.append(p)
    return cleaned or [ext_simplify_motion_for_query(motion)]


def ext_select_group_for_segment(segment: str, metric: Optional[str] = None, speed: Optional[str] = None) -> dict:
    """針對單一子動作選 group，不讓 metric 壓過明確子動作。"""
    selected = ext_select_primary_group(segment, metric=None, speed=speed)
    # 如果子動作完全看不出來，才用 KG metric 補。
    if selected.get("group_key") == "functional_movement" and metric:
        selected = ext_select_primary_group(segment, metric=metric, speed=speed)
    return selected


def ext_select_groups_for_motion(motion: str, metric: Optional[str] = None, speed: Optional[str] = None) -> dict:
    """
    v7：連續動作全部看。
    回傳 sub_actions（每一段）與 unique group 順序。
    """
    segments = ext_split_motion_action_segments(motion)
    sub_actions = []
    group_keys = []

    for seg in segments:
        selected = ext_select_group_for_segment(seg, metric=metric, speed=speed)
        key = selected.get("group_key", "functional_movement")
        if key == "functional_movement" and ext_is_vague_motion(seg):
            continue
        profile = MOVEMENT_GROUP_PROFILES.get(key, MOVEMENT_GROUP_PROFILES["functional_movement"])
        sub_actions.append({
            "segment": seg,
            "group_key": key,
            "group_title": profile["title"],
            "profile": profile,
        })
        if key not in group_keys:
            group_keys.append(key)

    if not sub_actions:
        selected = ext_select_primary_group(motion, metric=metric, speed=speed)
        key = selected.get("group_key", "functional_movement")
        profile = MOVEMENT_GROUP_PROFILES.get(key, MOVEMENT_GROUP_PROFILES["functional_movement"])
        sub_actions.append({
            "segment": ext_simplify_motion_for_query(motion),
            "group_key": key,
            "group_title": profile["title"],
            "profile": profile,
        })
        group_keys = [key]

    return {
        "segments": segments,
        "sub_actions": sub_actions,
        "group_keys": group_keys,
        "primary_group_key": group_keys[0] if group_keys else "functional_movement",
    }


def ext_build_group_query_plan(group_key: str, motion: str, metric: Optional[str] = None, speed: Optional[str] = None) -> Dict:
    """針對單一 group 產生中文優先 query plan。"""
    profile = MOVEMENT_GROUP_PROFILES.get(group_key, MOVEMENT_GROUP_PROFILES["functional_movement"])
    zh_terms = profile.get("query_terms_zh") or GROUP_CHINESE_QUERY_TERMS.get(group_key, [])
    en_terms = profile.get("query_terms", [])

    # 中文 query 放前面，符合使用者要中文網頁；英文 query 作為找不到中文時的備援。
    zh_base = " ".join(ext_unique_keep_order(zh_terms + ["醫院", "復健科", "物理治療", "職能治療", "衛教"]))
    en_base = " ".join(ext_unique_keep_order(en_terms + [
        "older adults", "physical therapy", "occupational therapy", "rehabilitation", "exercise instructions", "patient education"
    ]))

    chinese_sites = [
        "vghtpe.gov.tw", "vghtc.gov.tw", "cgmh.org.tw", "ntuh.gov.tw", "kmuh.org.tw",
        "health99.hpa.gov.tw", "hpa.gov.tw", "mohw.gov.tw", "tpech.gov.taipei",
    ]
    english_sites = [
        "choosept.com", "nhs.uk", "physio-pedia.com", "apta.org", "aota.org",
        "mayoclinic.org", "clevelandclinic.org", "betterhealth.vic.gov.au", "nia.nih.gov",
    ]

    query_variants = []
    # 先找中文專業站。
    query_variants.extend([f"{zh_base} site:{site}" for site in chinese_sites])
    # 再找中文全網專業衛教。
    query_variants.append(zh_base)
    # 再用英文專業站備援。
    query_variants.extend([f"{en_base} site:{site}" for site in english_sites])
    query_variants.append(en_base)

    query_variants = ext_unique_keep_order(query_variants)
    # 保留較多變體，避免中文站沒有結果；ext_collect 會逐一嘗試。
    query_variants = query_variants[:max(3, SEARCH_VARIANT_LIMIT)]

    reject_terms = ext_unique_keep_order(list(NON_MOTION_TOPIC_HINTS) + profile.get("reject_extra", []))
    if group_key == "upper_trunk":
        # 抬手/上肢控制要避免頸椎病症頁。
        reject_terms = ext_unique_keep_order(reject_terms + SYMPTOM_DISEASE_TOPIC_HINTS)

    return {
        "should_search": True,
        "query": zh_base,
        "query_variants": query_variants,
        "reason": "依連續動作子項目產生中文優先外部搜尋 query",
        "required_terms": ext_unique_keep_order(profile["required_any"] + zh_terms),
        "required_any": ext_unique_keep_order(profile["required_any"] + zh_terms),
        "action_any": ext_unique_keep_order(profile.get("query_terms", []) + profile["required_any"] + zh_terms),
        "reject_terms": reject_terms,
        "movement_components": "、".join(profile["components_zh"]),
        "topic": profile["title"],
        "group_key": group_key,
        "secondary_groups": [],
    }


def ext_build_dynamic_query_plan(motion: str, metric: Optional[str] = None, speed: Optional[str] = None) -> Dict:
    """
    v7：不只選 primary，而是記錄連續動作中的所有 group。
    為了相容舊流程，回傳第一個 group 的 plan，但額外帶 sub_actions/group_keys。
    """
    selected = ext_select_groups_for_motion(motion, metric=metric, speed=speed)
    group_keys = selected["group_keys"]

    if not group_keys or (group_keys == ["functional_movement"] and ext_is_vague_motion(motion)):
        return {
            "should_search": False,
            "query": "",
            "query_variants": [],
            "reason": "無法從連續動作中抽出明確功能性動作，避免採用不相關外部資料",
            "required_terms": [],
            "required_any": [],
            "action_any": [],
            "reject_terms": list(NON_MOTION_TOPIC_HINTS),
            "movement_components": "",
            "topic": "未知動作難度",
            "group_key": "functional_movement",
            "group_keys": [],
            "sub_actions": selected["sub_actions"],
            "secondary_groups": [],
        }

    first_key = group_keys[0]
    plan = ext_build_group_query_plan(first_key, motion, metric=metric, speed=speed)
    plan["group_keys"] = group_keys
    plan["sub_actions"] = selected["sub_actions"]
    plan["secondary_groups"] = [k for k in group_keys if k != first_key]
    # topic 顯示所有子動作群組，但搜尋仍分項進行。
    plan["topic"] = "、".join(MOVEMENT_GROUP_PROFILES[k]["title"] for k in group_keys if k in MOVEMENT_GROUP_PROFILES)
    plan["movement_components"] = "、".join(ext_unique_keep_order(sum([MOVEMENT_GROUP_PROFILES[k]["components_zh"] for k in group_keys if k in MOVEMENT_GROUP_PROFILES], [])))
    return plan


def ext_build_search_plan(motion: str, metric: Optional[str] = None, speed: Optional[str] = None) -> Dict:
    motion_clean = ext_normalize_text(motion)
    dynamic_plan = ext_build_dynamic_query_plan(motion=motion_clean, metric=metric, speed=speed)
    if dynamic_plan["should_search"]:
        return dynamic_plan
    if ext_is_vague_motion(motion_clean):
        dynamic_plan.update({
            "reason": "動作描述過於模糊，無法判斷具體動作難度；外部搜尋容易取得不相關資料",
            "topic": "模糊動作",
        })
    return dynamic_plan


def ext_web_search(query: str, max_results: int = 8) -> List[Dict]:
    """DDG 搜尋，v7 使用中文區域預設值 tw-tzh。"""
    query = ext_normalize_text(query)
    cache = ext_load_search_cache()

    if query in cache:
        cached = cache[query]
        cached = sorted(
            cached,
            key=lambda x: ext_source_priority(x.get("title", ""), x.get("url", ""), x.get("snippet", "")),
            reverse=True,
        )
        return cached[:max_results]

    if not ENABLE_LIVE_SEARCH:
        print(f"[external_search] live search disabled, skip query: {query}")
        cache[query] = []
        ext_save_search_cache(cache)
        return []

    results: List[Dict] = []
    try:
        try:
            ddgs_obj = DDGS(timeout=SEARCH_TIMEOUT_SEC)
        except TypeError:
            ddgs_obj = DDGS()

        with ddgs_obj as ddgs:
            try_regions = [SEARCH_REGION]  # 中文 evidence：不再 fallback 到 wt-wt，避免抓到英文頁
            for region in try_regions:
                try:
                    for r in ddgs.text(query, max_results=max_results, region=region, safesearch="moderate"):
                        title = r.get("title", "")
                        url = r.get("href") or r.get("url", "")
                        snippet = r.get("body", "")
                        if not url:
                            continue
                        item = {
                            "title": ext_normalize_text(title),
                            "url": url,
                            "snippet": ext_normalize_text(snippet),
                        }
                        if ext_is_english_or_empty_source(item["title"], item["url"], item["snippet"]):
                            continue
                        if item not in results:
                            results.append(item)
                        if len(results) >= max_results:
                            break
                except Exception:
                    continue
                if results:
                    break
    except Exception as e:
        print(f"[external_search] search failed or timeout: {e}")
        results = []

    results = sorted(
        results,
        key=lambda x: ext_source_priority(x.get("title", ""), x.get("url", ""), x.get("snippet", "")),
        reverse=True,
    )
    cache[query] = results
    ext_save_search_cache(cache)
    return results[:max_results]


def ext_is_relevant_evidence(item: Dict, plan: Dict) -> bool:
    """
    v7 相關性檢查：內容相關性優先於來源權威。
    例如上肢抬手不能因為 ChoosePT 很專業就接受頸椎神經根病頁。
    """
    title = item.get("title", "")
    url = item.get("url", "")
    snippet = item.get("snippet", "")
    text = f"{title} {snippet} {url}".lower()

    if ext_is_blocked_source(title, url, snippet):
        return False
    if ext_is_research_source(title, url, snippet) and not ALLOW_RESEARCH_EVIDENCE:
        return False
    if ext_source_priority(title, url, snippet) <= 0:
        return False

    group_key = plan.get("group_key", "functional_movement")
    reject_terms = set(plan.get("reject_terms", []) or []) | set(NON_MOTION_TOPIC_HINTS)
    reject_terms |= set(MOVEMENT_GROUP_PROFILES.get(group_key, {}).get("reject_extra", []))

    # 專業但不相關疾病頁直接排除。
    if any(term.lower() in text for term in reject_terms):
        # 若是營養/疾病/症狀頁，不讓 rehab_hit 放行。
        return False

    rehab_hit = any(k in text for k in MOTION_REHAB_HINTS) or any(k in text for k in CHINESE_REHAB_HINTS)
    required_any = plan.get("required_any", []) or plan.get("required_terms", []) or []
    action_any = plan.get("action_any", []) or []
    required_hit = True if not required_any else any(term.lower() in text for term in required_any)
    action_hit = True if not action_any else any(term.lower() in text for term in action_any)

    # group-specific minimum relevance：避免太泛。
    if group_key == "upper_trunk":
        if not any(k in text for k in ["shoulder", "upper limb", "arm", "range of motion", "抬手", "肩關節", "上肢", "手臂", "關節活動度"]):
            return False
    elif group_key == "gait_direction":
        if not any(k in text for k in ["gait", "walking", "walk", "步態", "行走", "走路", "轉身", "側步"]):
            return False
    elif group_key == "single_leg_balance":
        if not any(k in text for k in ["single leg", "one leg", "balance", "單腳", "平衡", "跌倒"]):
            return False
    elif group_key == "sit_to_stand":
        if not any(k in text for k in ["sit to stand", "chair", "lower limb", "坐站", "椅子", "下肢", "起立"]):
            return False

    older_hit = any(k in text for k in ["older adult", "older adults", "senior", "seniors", "elderly", "aging", "ageing", "長者", "老人", "銀髮"])
    trusted_or_practical = ext_is_trusted_domain(url) or ext_source_priority(title, url, snippet) >= 2

    if trusted_or_practical and rehab_hit and (required_hit or action_hit):
        return True
    if rehab_hit and required_hit and action_hit:
        return True
    if older_hit and trusted_or_practical and action_hit:
        return True
    return False


def ext_search_one_group(group_key: str, motion: str, metric: Optional[str], speed: Optional[str], max_results: int, max_pages: int) -> dict:
    """針對單一子動作 group 搜尋 evidence。"""
    plan = ext_build_group_query_plan(group_key, motion, metric=metric, speed=speed)
    query_attempts = []
    evidence_items = []

    for q in plan.get("query_variants") or [plan["query"]]:
        q = ext_normalize_text(q)
        if not q or q in query_attempts:
            continue
        query_attempts.append(q)
        search_results = ext_web_search(query=q, max_results=max(max_results, 8))
        for item in search_results:
            if not ext_is_relevant_evidence(item, plan):
                continue
            if STRICT_PROFESSIONAL_SOURCES and ext_source_priority(item.get("title", ""), item.get("url", ""), item.get("snippet", "")) < 2:
                continue
            page_text = ""
            if max_pages > 0 and len(evidence_items) < max_pages:
                page_text = ext_fetch_page_text(item["url"], timeout=5, max_chars=3000)
            evidence_items.append({
                "title": item["title"],
                "url": item["url"],
                "snippet": item["snippet"],
                "page_text": page_text,
                "source_priority": ext_source_priority(item.get("title", ""), item.get("url", ""), item.get("snippet", "")),
                "group_key": group_key,
            })
            break
        if evidence_items:
            break

    return {
        "group_key": group_key,
        "topic": MOVEMENT_GROUP_PROFILES.get(group_key, MOVEMENT_GROUP_PROFILES["functional_movement"])["title"],
        "movement_components": "、".join(MOVEMENT_GROUP_PROFILES.get(group_key, MOVEMENT_GROUP_PROFILES["functional_movement"])["components_zh"]),
        "query": plan["query"],
        "query_attempts": query_attempts,
        "evidence": evidence_items[:1],
        "skip_reason": "" if evidence_items else "外部搜尋結果未通過中文/專業來源與動作相關性檢查，未採用外部 evidence。",
    }


def ext_collect_external_evidence(motion: str, metric: Optional[str] = None, speed: Optional[str] = None, max_results: int = 1, max_pages: int = 0) -> Dict:
    """
    v7 外部搜尋主函式：連續動作分項搜尋。
    同一筆 motion 可能包含 2~3 個子動作，例如抬手→後退→前進，會分成上肢與步態兩類看。
    """
    plan = ext_build_search_plan(motion=motion, metric=metric, speed=speed)
    if not plan["should_search"]:
        return {
            "used_external_search": False,
            "external_search_skipped": True,
            "skip_reason": plan["reason"],
            "query": "",
            "query_attempts": [],
            "motion": motion,
            "metric": metric,
            "speed": speed,
            "topic": plan["topic"],
            "group_key": plan.get("group_key", "functional_movement"),
            "group_keys": plan.get("group_keys", []),
            "sub_actions": plan.get("sub_actions", []),
            "sub_bundles": [],
            "secondary_groups": plan.get("secondary_groups", []),
            "movement_components": plan.get("movement_components", ""),
            "is_daily_non_assessment_motion": ext_is_daily_non_assessment_motion(motion),
            "evidence": [],
        }

    group_keys = plan.get("group_keys") or [plan.get("group_key", "functional_movement")]
    sub_bundles = []
    all_evidence = []
    all_attempts = []

    for key in group_keys:
        if key == "functional_movement" and len(group_keys) > 1:
            continue
        b = ext_search_one_group(key, motion, metric, speed, max_results=max_results, max_pages=max_pages)
        sub_bundles.append(b)
        all_attempts.extend(b.get("query_attempts", []))
        for ev in b.get("evidence", []):
            all_evidence.append(ev)

    primary_key = group_keys[0] if group_keys else "functional_movement"
    topic = "、".join(MOVEMENT_GROUP_PROFILES[k]["title"] for k in group_keys if k in MOVEMENT_GROUP_PROFILES)
    components = "、".join(ext_unique_keep_order(sum([MOVEMENT_GROUP_PROFILES[k]["components_zh"] for k in group_keys if k in MOVEMENT_GROUP_PROFILES], [])))

    return {
        "used_external_search": True,
        "external_search_skipped": False,
        "skip_reason": "" if all_evidence else "外部搜尋結果未通過中文/專業來源與動作相關性檢查，未採用外部 evidence。",
        "query": "；".join([b.get("query", "") for b in sub_bundles if b.get("query")]),
        "query_attempts": ext_unique_keep_order(all_attempts),
        "motion": motion,
        "metric": metric,
        "speed": speed,
        "topic": topic or plan["topic"],
        "group_key": primary_key,
        "group_keys": group_keys,
        "sub_actions": plan.get("sub_actions", []),
        "sub_bundles": sub_bundles,
        "secondary_groups": [k for k in group_keys if k != primary_key],
        "movement_components": components or plan.get("movement_components", ""),
        "is_daily_non_assessment_motion": ext_is_daily_non_assessment_motion(motion),
        "evidence": ext_sort_evidence_by_time(all_evidence)[:3],
    }


def external_search_fallback(motion: str, metric: Optional[str] = None, speed: Optional[str] = None, max_results: int = 1, max_pages: int = 0) -> Dict:
    evidence_bundle = ext_collect_external_evidence(motion=motion, metric=metric, speed=speed, max_results=max_results, max_pages=max_pages)
    prompt = ext_build_external_prompt(evidence_bundle)
    return {"evidence_bundle": evidence_bundle, "prompt": prompt}


def clean_external_context_text(text: str) -> str:
    text = re.sub(r"\s+", " ", str(text or "")).strip()
    text = re.sub(r"缺少字詞[:：].*", "", text).strip()
    text = re.sub(r"missing words?[:：].*", "", text, flags=re.I).strip()
    text = text.strip(" :：;；,.，。")
    return text


def build_external_template_card(evidence_bundle: dict, class_name: str, score_val: int) -> str:
    """
    v7 外部補充卡：連續動作分項顯示，不再只顯示第一個動作。
    """
    sub_actions = evidence_bundle.get("sub_actions", []) or []
    sub_bundles = evidence_bundle.get("sub_bundles", []) or []
    group_keys = evidence_bundle.get("group_keys") or [evidence_bundle.get("group_key", "functional_movement")]

    lines = []
    if sub_actions and len(sub_actions) > 1:
        lines.append("連續動作拆解：")
        for idx, sa in enumerate(sub_actions, start=1):
            seg = sa.get("segment", "")
            title = sa.get("group_title", "功能性動作控制")
            lines.append(f"{idx}. {seg} → {title}")
        lines.append("")

    # 依 group 逐項輸出，重複 group 只出一次，但上方拆解仍保留每個連續動作片段。
    bundle_by_key = {b.get("group_key"): b for b in sub_bundles}
    for key in group_keys:
        profile = MOVEMENT_GROUP_PROFILES.get(key, MOVEMENT_GROUP_PROFILES["functional_movement"])
        b = bundle_by_key.get(key, {})
        evidence = b.get("evidence", []) or []
        context_text = "外部資料不足，未能擷取明確的動作難度或復健建議重點。"
        source_text = "未取得可用外部資料"
        if evidence:
            first = evidence[0]
            source_titles = []
            seen_source_urls = set()
            for src_item in evidence[:3]:
                src_url = str(src_item.get("url", "")).strip()
                if src_url and src_url in seen_source_urls:
                    continue
                if src_url:
                    seen_source_urls.add(src_url)
                title = str(src_item.get("title", "外部資料")).strip() or "外部資料"
                source_titles.append(title[:60])
            source_text = "；".join(source_titles) if source_titles else first.get("title", "外部資料")[:60]
            context_text = first.get("raptor_context") or first.get("snippet") or first.get("page_text") or ""
            context_text = clean_external_context_text(context_text)[:420] or context_text

        lines.extend([
            f"能力項目：{profile['title']}",
            f"判斷說明：{profile['status']}",
            f"動作組成：{'、'.join(profile['components_zh'])}",
            f"外部資料重點：{context_text}",
            f"動作難度觀察：{profile['how']}",
            f"復健動作建議：{profile['action']}",
            f"注意事項：{profile['caution']}",
            f"資料來源類型：外部搜尋補強；參考來源：{source_text}",
            "信心程度：低至中等，需要人工複核",
            "",
        ])

    return "\n".join(lines).strip()


def ext_group_for_kg_supplement(motion: str, class_name: str, evidence_bundle: dict | None = None) -> str:
    """
    KG 卡片補欄位時，不能直接拿第一個子動作。
    例如：raise arm -> bend down -> stand up 且 KG=下肢肌力，補欄位應偏坐站/下肢，而不是上肢。
    """
    m = ext_normalize_text(motion).lower()
    class_name = (class_name or "").strip()

    if class_name == "單腳站立":
        return "single_leg_balance"
    if class_name in ["空間方位位移", "來回行走", "步行速度"]:
        return "gait_direction"
    if class_name == "上肢與軀幹活動":
        return "upper_trunk"
    if class_name == "心肺耐力":
        return "endurance"
    if class_name == "下肢肌力":
        # motion-specific first
        if re.search(r"\bjump|\bhop|跳", m):
            return "jump_landing"
        if re.search(r"\bbend|\bsquat|\bcrouch|pick(s)?\s+up", m):
            # 若同時有 stand up，坐站轉移更貼合 KG 下肢肌力；否則下蹲。
            if re.search(r"stand(s|ing)?\s+up|sit(s|ting)?", m):
                return "sit_to_stand"
            return "bend_squat"
        if re.search(r"sit|stand", m):
            return "sit_to_stand"
        return "sit_to_stand"

    if evidence_bundle:
        keys = evidence_bundle.get("group_keys") or []
        if keys:
            return keys[0]
    return ext_select_primary_group(motion, metric=class_name, speed=None).get("group_key", "functional_movement")


def build_external_field_supplements(motion: str, class_name: str, score_val: int, evidence_bundle: dict) -> dict:
    """
    v7 KG 缺欄位補足：依 KG 類別 + motion 選擇合理 group。
    外部 evidence 只提供參考網址；若找不到 evidence，但動作明確，仍用動作分析模板補欄位，避免全是資料不足。
    """
    evidence_bundle = evidence_bundle or {}
    group_key = ext_group_for_kg_supplement(motion, class_name, evidence_bundle)
    profile = MOVEMENT_GROUP_PROFILES.get(group_key, MOVEMENT_GROUP_PROFILES["functional_movement"])
    if group_key == "functional_movement" and not evidence_bundle.get("evidence"):
        return {}

    supplements = {
        "action": profile["action"],
        "how_to": profile["how"],
        "caution": profile["caution"],
        "group_key": group_key,
    }

    reference_urls = []
    # 優先找同 group 的 reference。
    for b in evidence_bundle.get("sub_bundles", []) or []:
        if b.get("group_key") != group_key:
            continue
        for item in b.get("evidence", [])[:3]:
            url = str(item.get("url", "")).strip()
            if url and url not in reference_urls:
                reference_urls.append(url)
    if not reference_urls:
        for item in evidence_bundle.get("evidence", [])[:3]:
            url = str(item.get("url", "")).strip()
            if url and url not in reference_urls:
                reference_urls.append(url)

    if reference_urls:
        supplements["reference_urls"] = reference_urls
    return supplements


# ============================================================
# 0. 基本設定
# ============================================================
os.environ["TOKENIZERS_PARALLELISM"] = "false"

BASE_DIR = Path(__file__).resolve().parent

REPO_DIR = BASE_DIR.parent.parent  # Radar_Agent 根目錄（資料已移到 data/）
DATA_DIR = REPO_DIR / "data" / "samples"

CSV_PATH = REPO_DIR / "data" / "labels" / "sorted_data.csv"
JSON_PATH = REPO_DIR / "data" / "samples" / "move.json"

SIM_ACCEPT = 0.70
SIM_REVIEW = 0.55
TOP_K_DEBUG = 5

# ─── 並行設定 ───────────────────────────────────────────────
MAX_NEO4J_WORKERS = 2   # Jetson 省記憶體：Neo4j I/O 並行數降低
LLM_BATCH_SIZE    = 1   # 每批同時送進 LLM 的筆數（依 VRAM 調整）

# 預設 Demo / Jetson 穩定模式：不逐筆呼叫 Qwen。
# 需要展示 LLM 改寫時，再用 USE_LLM=1 開啟。
USE_LLM = os.getenv("USE_LLM", "0") == "1"
EXTERNAL_MAX_RESULTS = int(os.getenv("EXTERNAL_MAX_RESULTS", "8"))
EXTERNAL_MAX_PAGES = int(os.getenv("EXTERNAL_MAX_PAGES", "3"))

if not torch.cuda.is_available():
    print("⚠️  警告：找不到 CUDA GPU，將嘗試使用 CPU（速度會很慢）")
    DEVICE = "cpu"
    GPU_NAME = "CPU"
    VRAM_GB = 0
else:
    GPU_NAME = torch.cuda.get_device_name(0)
    VRAM_GB  = torch.cuda.get_device_properties(0).total_memory / 1e9
    DEVICE   = "cuda"

print(f"✅ 運算裝置：{GPU_NAME}  VRAM：{VRAM_GB:.1f} GB")
print(f"   CUDA：{torch.version.cuda if torch.cuda.is_available() else 'N/A'}  PyTorch：{torch.__version__}")

# ============================================================
# 1. 載入 RAG 需要的模型
# ============================================================
print("\n🌟 正在載入 RAG 模型...")

# 1) Qwen：只負責根據檢索到的知識圖譜建議進行文字整理
#    不讓模型自行新增運動、頻率、醫療判斷或未檢索到的內容。
HF_TOKEN = os.getenv("HF_TOKEN")
MODEL_ID = "Qwen/Qwen2.5-0.5B-Instruct"

tokenizer = None
model = None

if USE_LLM:
    tokenizer = AutoTokenizer.from_pretrained(MODEL_ID, token=HF_TOKEN)
    tokenizer.padding_side = "left"
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token

    MODEL_DTYPE = torch.float16 if DEVICE == "cuda" else torch.float32
    model = AutoModelForCausalLM.from_pretrained(
        MODEL_ID,
        token=HF_TOKEN,
        attn_implementation="sdpa" if DEVICE == "cuda" else "eager",
        torch_dtype=MODEL_DTYPE,
        low_cpu_mem_usage=True,
    )
    model = model.to(DEVICE)
    model.eval()
    print(f"✅ Qwen 載入完成：{next(model.parameters()).device}")
else:
    print("⏭️  USE_LLM=0：跳過 Qwen 載入，改用 KG + 外部 evidence 合併模板")

# 2) all-MiniLM-L6-v2：負責將動作句轉成向量，找最相似的標竿句
#    embedder 先放 CPU，降低 Jetson 共用記憶體壓力。
EMBEDDER_DEVICE = "cpu"
embedder = SentenceTransformer("all-MiniLM-L6-v2", device=EMBEDDER_DEVICE)
print(f"✅ Embedder 載入完成：{EMBEDDER_DEVICE}")


def llm_generate(messages: list[dict], max_new_tokens: int = 220) -> str:
    """
    RAG 文字整理用 LLM。
    使用 deterministic decoding，降低同一輸入多次輸出不一致的機率。
    """
    if not USE_LLM or tokenizer is None or model is None:
        return ""

    text = tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
    enc = tokenizer(
        text,
        return_tensors="pt",
        truncation=True,
        max_length=2048,
    ).to(DEVICE)

    input_len = enc.input_ids.shape[1]

    if DEVICE == "cuda":
        torch.cuda.empty_cache()

    with torch.no_grad():
        output_ids = model.generate(
            input_ids=enc.input_ids,
            attention_mask=enc.attention_mask,
            max_new_tokens=max_new_tokens,
            do_sample=False,
            num_beams=1,
            repetition_penalty=1.05,
            pad_token_id=tokenizer.pad_token_id,
        )

    new_tokens = output_ids[0][input_len:]
    out = tokenizer.decode(new_tokens, skip_special_tokens=True).strip()

    # 保留 Markdown 換行，不要把格式壓成一整段
    out = out.replace("\r\n", "\n").replace("\r", "\n")
    out = "\n".join(
        re.sub(r"[ \t]+", " ", line).strip()
        for line in out.split("\n")
    )
    out = re.sub(r"\n{3,}", "\n\n", out).strip()

    return out


def rag_generate_action_card(prompt_payload: str, max_new_tokens: int = 420) -> str:
    """
    讓 LLM 根據知識圖譜內容，生成固定格式的可執行建議卡。
    內容來自 KG，格式由 prompt 規範，LLM 負責轉成清楚文字。
    """
    messages = [
        {
            "role": "system",
            "content": (
                "你是 RAG 健康建議生成助手。\n"
                "你只能根據使用者提供的【知識圖譜資料】生成建議。\n"
                "你可以重新組織語句，讓一般長者與家屬看得懂。\n"
                "但禁止新增知識圖譜中沒有提供的運動項目、次數、時間、頻率、器材、疾病、診斷、營養、飲水或醫療判斷。\n"
                "所有數字必須完全來自【建議數值】或【評估分數】。\n"
                "標題必須使用知識圖譜中的能力名稱，不得自行改名，例如不得把「步行速度」改成「跑步速度」。\n"
                "不得輸出英文動作句。\n"
                "不得輸出簡體中文。\n"
                "不得加入祝福語、結尾語或額外說明。\n"
                "請輸出固定格式，不要省略欄位。\n\n"
                "固定格式如下：\n"
                "### {能力名稱}\n"
                "- **目前狀態**：\n"
                "- **建議動作**：\n"
                "- **怎麼做**：\n"
                "- **每次做多少**：\n"
                "- **每週目標**：\n"
                "- **注意事項**：\n"
                "- **依據來源**：知識圖譜建議（{能力名稱} / 分數 {分數}）\n\n"
                "如果某個欄位在知識圖譜中沒有足夠資料，請寫「目前資料不足」，不得自行補充。"
            ),
        },
        {
            "role": "user",
            "content": prompt_payload,
        },
    ]
    return llm_generate(messages, max_new_tokens=max_new_tokens)

def rag_generate_from_kg(prompt_payload: str, max_new_tokens: int = 420) -> str:
    """
    相容舊版函式名稱。
    目前所有 RAG 生成都統一交給 action-card 版本。
    """
    return rag_generate_action_card(prompt_payload, max_new_tokens=max_new_tokens)


def external_generate_from_prompt(external_prompt: str, max_new_tokens: int = 360) -> str:
    """
    外部搜尋補強用 LLM。
    這個函式和 KG 生成分開，避免被 KG 的嚴格 validator 擋掉。
    """
    messages = [
        {
            "role": "system",
            "content": (
                "你是長者肢體能力建議系統中的外部搜尋補強助手。\n"
                "你只能根據使用者提供的【外部搜尋資料】生成建議。\n"
                "請輸出保守、安全、低風險的一般性建議。\n"
                "禁止做醫療診斷、禁止開處方、禁止宣稱治療效果。\n"
                "如果外部搜尋資料不足，請明確說明資料不足，只提供一般安全提醒。\n"
                "請使用繁體中文，不要使用簡體字。\n"
                "請不要加入祝福語、結尾語或額外聊天內容。"
            ),
        },
        {
            "role": "user",
            "content": external_prompt,
        },
    ]

    generated = llm_generate(messages, max_new_tokens=max_new_tokens).strip()
    generated = normalize_traditional_text(generated)

    if not generated:
        return (
            "能力項目：未知能力項目\n"
            "判斷說明：外部搜尋資料不足，無法形成明確判斷。\n"
            "外部補充建議：僅提供一般性安全提醒，建議人工複核或補充知識圖譜。\n"
            "注意事項：如有不適，請停止活動並諮詢專業人員。\n"
            "資料來源類型：外部搜尋補強\n"
            "信心程度：低"
        )

    return generated


def render_external_sources(evidence_bundle: dict, max_sources: int = 3) -> list[str]:
    """
    將外部搜尋 evidence 的來源列進報告，方便追蹤。
    """
    evidence = evidence_bundle.get("evidence", [])

    if not evidence:
        return ["- **外部來源紀錄**：未取得可用外部來源。"]

    lines = ["- **外部來源紀錄**："]

    for i, item in enumerate(evidence[:max_sources], start=1):
        title = item.get("title", "").strip() or "無標題"
        url = item.get("url", "").strip() or "無網址"
        lines.append(f"  {i}. {title} — {url}")

    return lines



def split_text_to_sentences(text: str) -> list[str]:
    """
    增強式向量檢索：先做 sentence-aware segmentation。
    重點：保留原文句子，不產生 parent summary，避免摘要節點帶來幻覺或語意漂移。
    """
    text = re.sub(r"\s+", " ", str(text or "")).strip()
    if not text:
        return []

    # 同時支援中文與英文標點。
    parts = re.split(r"(?<=[。！？!?；;\.])\s*", text)
    sentences = []
    for s in parts:
        s = re.sub(r"\s+", " ", s).strip()
        if len(s) >= 12:
            sentences.append(s)
    return sentences


def chunk_text_by_chars(text: str, chunk_size: int = 520, overlap_sentences: int = 1) -> list[str]:
    """
    將網頁原文切成可檢索 chunks。
    舊版用 word split，中文網頁常常沒有空白，容易切壞；這版改用句子 + 字數上限。
    """
    sentences = split_text_to_sentences(text)
    if not sentences:
        clean = re.sub(r"\s+", " ", str(text or "")).strip()
        return [clean[:chunk_size]] if len(clean) >= 50 else []

    chunks = []
    current = []
    current_len = 0

    for sent in sentences:
        sent_len = len(sent)
        if current and current_len + sent_len > chunk_size:
            chunk = " ".join(current).strip()
            if len(chunk) >= 50:
                chunks.append(chunk)

            # 保留少量重疊句，避免上下文被切斷。
            current = current[-overlap_sentences:] if overlap_sentences > 0 else []
            current_len = sum(len(x) for x in current)

        current.append(sent)
        current_len += sent_len

    if current:
        chunk = " ".join(current).strip()
        if len(chunk) >= 50:
            chunks.append(chunk)

    return chunks


def extract_query_terms(query: str) -> list[str]:
    """
    從 query 抽出中英文關鍵詞，作為 lexical relevance filter。
    目的不是取代 embedding，而是避免只靠語意向量撈到表面相似但主題偏掉的段落。
    """
    query = str(query or "").lower()
    raw_terms = re.findall(r"[\u4e00-\u9fff]{2,}|[a-zA-Z][a-zA-Z0-9_-]{2,}", query)
    stop = {
        "site", "http", "https", "www", "com", "org", "gov", "edu",
        "older", "adults", "adult", "elderly", "patient", "education",
        "recommendation", "instructions", "health", "醫院", "衛教"
    }
    terms = []
    for t in raw_terms:
        t = t.strip().lower()
        if not t or t in stop:
            continue
        if t not in terms:
            terms.append(t)
    return terms[:24]


def lexical_overlap_score(text: str, terms: list[str]) -> float:
    """
    簡單 keyword overlap 分數。
    回傳 0~1，讓段落至少要碰到查詢主題的一部分。
    """
    if not terms:
        return 0.0
    low = str(text or "").lower()
    hits = sum(1 for t in terms if t and t in low)
    return hits / max(1, len(terms))


def mmr_select(node_embs: np.ndarray, relevance: np.ndarray, top_k: int = 3, lambda_mult: float = 0.72) -> list[int]:
    """
    Maximal Marginal Relevance：在相關性與多樣性之間取平衡。
    避免 top_k 全部都是同一段附近的重複內容。
    """
    if len(relevance) == 0:
        return []

    selected = []
    candidates = set(range(len(relevance)))

    first = int(np.argmax(relevance))
    selected.append(first)
    candidates.remove(first)

    while candidates and len(selected) < top_k:
        best_idx = None
        best_score = -1e9
        for idx in candidates:
            diversity_penalty = max(float(node_embs[idx] @ node_embs[j]) for j in selected)
            score = lambda_mult * float(relevance[idx]) - (1.0 - lambda_mult) * diversity_penalty
            if score > best_score:
                best_score = score
                best_idx = idx
        selected.append(int(best_idx))
        candidates.remove(best_idx)

    return selected


def enhanced_vector_retrieve_from_page(
    query: str,
    page_text: str,
    top_k: int = 3,
    chunk_size: int = 520,
    min_dense_score: float = 0.18,
) -> list[str]:
    """
    增強式向量檢索 Enhanced Vector Retrieval。

    取代原本 RAPTOR-lite 摘要樹：
    1. 只切原文 chunks，不建立 parent summary。
    2. dense embedding 召回語意相關段落。
    3. lexical overlap 過濾主題漂移。
    4. MMR 去除重複段落。
    5. 分數過低就不回傳，避免硬塞不可靠 evidence 給後面模板/LLM。
    """
    chunks = chunk_text_by_chars(page_text, chunk_size=chunk_size, overlap_sentences=1)
    if not chunks:
        return []

    query = re.sub(r"\s+", " ", str(query or "")).strip()
    if not query:
        query = "復健 動作 訓練 安全 注意事項"

    terms = extract_query_terms(query)

    query_emb = embedder.encode(
        [query],
        convert_to_numpy=True,
        normalize_embeddings=True,
        show_progress_bar=False,
    )[0]

    node_embs = embedder.encode(
        chunks,
        batch_size=16,
        convert_to_numpy=True,
        normalize_embeddings=True,
        show_progress_bar=False,
    )

    dense_scores = node_embs @ query_emb
    lexical_scores = np.array([lexical_overlap_score(c, terms) for c in chunks], dtype=np.float32)

    # dense 為主、lexical 為輔；這樣比單純向量更不容易抓到主題偏掉的段落。
    relevance = 0.78 * dense_scores + 0.22 * lexical_scores

    # 先拿較寬鬆候選，再做 MMR。
    candidate_order = np.argsort(-relevance)[: max(top_k * 6, top_k)]
    filtered = []
    for idx in candidate_order:
        idx = int(idx)
        # dense 很低且完全沒有 keyword overlap，就不要放進 evidence。
        if dense_scores[idx] < min_dense_score and lexical_scores[idx] <= 0:
            continue
        filtered.append(idx)

    if not filtered:
        return []

    sub_embs = node_embs[filtered]
    sub_rel = relevance[filtered]
    selected_local = mmr_select(sub_embs, sub_rel, top_k=top_k, lambda_mult=0.72)
    selected = [filtered[i] for i in selected_local]

    outputs = []
    for idx in selected:
        chunk = re.sub(r"\s+", " ", chunks[int(idx)]).strip()
        if chunk:
            outputs.append(chunk[:900])

    return outputs


# Backward compatible alias：讓舊呼叫不會壞，但實際上已經不是摘要樹。
def raptor_lite_retrieve_from_page(query: str, page_text: str, top_k: int = 3) -> list[str]:
    return enhanced_vector_retrieve_from_page(query=query, page_text=page_text, top_k=top_k)


def clean_external_context_text(text: str) -> str:
    """
    清理外部搜尋摘要。
    避免把搜尋/比對過程中的雜訊，例如「缺少字詞：...」放進使用者報告。
    """
    text = re.sub(r"\s+", " ", str(text or "")).strip()
    text = re.sub(r"缺少字詞[:：].*", "", text).strip()
    text = re.sub(r"missing words?[:：].*", "", text, flags=re.I).strip()
    text = text.strip(" :：;；,.，。")
    return text

def build_external_template_card(evidence_bundle: dict, class_name: str, score_val: int) -> str:
    """
    外部搜尋補強模板 v6。
    依 primary movement group 產生較具體的使用者內容，不再輸出重複的泛用模板。
    """
    evidence = evidence_bundle.get("evidence", []) or []
    group_key = evidence_bundle.get("group_key") or "functional_movement"
    profile = MOVEMENT_GROUP_PROFILES.get(group_key, MOVEMENT_GROUP_PROFILES["functional_movement"])
    topic = evidence_bundle.get("topic") or profile["title"]
    movement_components = evidence_bundle.get("movement_components") or "、".join(profile["components_zh"])

    source_text = "未取得可用外部資料"
    context_text = "外部資料不足，未能擷取明確的動作難度或復健建議重點。"

    if evidence:
        first = evidence[0]
        source_text = first.get("title", "外部資料")[:60]
        context_text = (
            first.get("raptor_context")
            or first.get("snippet")
            or first.get("page_text")
            or ""
        )
        context_text = clean_external_context_text(context_text)[:420] or context_text

    return (
        f"能力項目：{topic}\n"
        f"判斷說明：{profile['status']}\n"
        f"動作組成：{movement_components}\n"
        f"外部資料重點：{context_text}\n"
        f"動作難度觀察：{profile['how']}\n"
        f"復健動作建議：{profile['action']}\n"
        f"注意事項：{profile['caution']}\n"
        f"資料來源類型：外部搜尋補強；參考來源：{source_text}\n"
        f"信心程度：低至中等，需要人工複核"
    )



def is_missing_field(value: str) -> bool:
    """
    判斷 KG 欄位是否缺資料。
    只要是空值、None、目前資料不足等，都視為缺欄位。
    """
    if value is None:
        return True

    text = str(value).strip()
    if not text:
        return True

    missing_markers = [
        "目前資料不足",
        "資料不足",
        "未提供",
        "無",
        "None",
        "none",
        "null",
        "NULL",
        "N/A",
    ]
    return any(m in text for m in missing_markers)


def get_external_context_text(evidence_bundle: dict) -> str:
    """
    從外部 evidence 中取出可用文字。
    優先使用 RAPTOR-lite 擷取結果，其次使用 snippet / page_text。
    """
    if not evidence_bundle:
        return ""

    evidence = evidence_bundle.get("evidence", [])
    if not evidence:
        return ""

    chunks = []
    for item in evidence[:3]:
        text = (
            item.get("raptor_context")
            or item.get("snippet")
            or item.get("page_text")
            or ""
        )
        text = re.sub(r"\s+", " ", str(text)).strip()
        if text:
            chunks.append(text[:700])

    return "\n".join(chunks)


def build_external_field_supplements(
    motion: str,
    class_name: str,
    score_val: int,
    evidence_bundle: dict,
) -> dict:
    """
    補足 KG 缺欄位 v6。
    - 優先依 motion 的 primary movement group 補欄位，而不是只看 class_name。
    - 有外部 evidence 時附參考網址；沒有 evidence 但動作明確時，使用「動作分析模板」補足，避免 KG 卡片全是資料不足。
    - 不補評估分數、不覆蓋 KG 數值建議。
    """
    evidence_bundle = evidence_bundle or {}
    evidence = evidence_bundle.get("evidence", []) or []

    group_key = evidence_bundle.get("group_key")
    if not group_key:
        group_key = ext_select_primary_group(motion, metric=class_name, speed=None).get("group_key", "functional_movement")

    profile = MOVEMENT_GROUP_PROFILES.get(group_key, MOVEMENT_GROUP_PROFILES["functional_movement"])

    # 動作完全不明確時，不補，避免把模糊句硬轉復健建議。
    if group_key == "functional_movement" and not evidence:
        return {}

    supplements = {
        "action": profile["action"],
        "how_to": profile["how"],
        "caution": profile["caution"],
        "group_key": group_key,
    }

    reference_urls = []
    for item in evidence[:3]:
        url = str(item.get("url", "")).strip()
        if url and url not in reference_urls:
            reference_urls.append(url)
    if reference_urls:
        supplements["reference_urls"] = reference_urls

    return supplements

def normalize_traditional_text(text: str) -> str:
    """
    將模型偶爾輸出的常見簡體字修成繁體字。
    不改變建議內容，只修正顯示文字。
    """
    if not text:
        return text

    replacements = {
        "训练": "訓練",
        "稳定": "穩定",
        "稳定性": "穩定性",
        "每周": "每週",
        "建议": "建議",
        "运动": "運動",
        "风险": "風險",
        "评估": "評估",
        "分数": "分數",
        "医学": "醫學",
        "频率": "頻率",
        "次数": "次數",
        "时间": "時間",
        "长者": "長者",
        "身体": "身體",
        "头晕": "頭暈",
        "疼痛": "疼痛",
        "过度": "過度",
        "疲劳": "疲勞",
        "适当": "適當",
        "营养": "營養",
        "单": "單",
        "体": "體",
        "锻炼": "鍛鍊",
        "检查": "檢查",
        "回复": "回覆",
    }
    for src, tgt in replacements.items():
        text = text.replace(src, tgt)
    return text


def remove_markdown_numbering(text: str) -> str:
    """
    移除 Markdown 標題或條列開頭的編號，避免 validator 把「1. 下肢肌力」誤判為 LLM 新增數字。
    """
    if not text:
        return text
    text = re.sub(r"(?m)^\s*#{1,6}\s*\d+(?:\.\d+)*\s*[\.、)]?\s*", "### ", text)
    text = re.sub(r"(?m)^\s*[-*]\s*\d+(?:\.\d+)*\s*[\.、)]?\s*", "- ", text)
    return text


def validate_llm_recommendation(output: str, source_text: str) -> tuple[bool, str]:
    """
    輕量輸出檢查：
    1. 避免 LLM 偷加新的數字。
    2. 避免輸出醫療診斷、英文動作句、簡體字或未授權生活建議。
    若不通過，呼叫端會 fallback 回知識圖譜原文。
    """
    if not output.strip():
        return False, "空輸出"

    source_for_num = remove_markdown_numbering(source_text)
    output_for_num = remove_markdown_numbering(output)

    source_nums = set(re.findall(r"\d+(?:\.\d+)?", source_for_num))
    output_nums = set(re.findall(r"\d+(?:\.\d+)?", output_for_num))
    extra_nums = output_nums - source_nums
    if extra_nums:
        return False, f"LLM 輸出了知識圖譜沒有的數字：{sorted(extra_nums)}"

    forbidden_terms = [
        "診斷", "治療", "痊癒", "處方", "藥物",
        "糖尿病", "高血壓", "中風", "心臟病", "骨質疏鬆",
    ]
    for term in forbidden_terms:
        if term in output and term not in source_text:
            return False, f"LLM 輸出了未授權醫療詞：{term}"

    # 不允許 LLM 自行補充營養、飲水、疲勞等 KG 未提供內容。
    forbidden_generation_terms = [
        "跑步速度", "營養", "水分", "飲水", "過度疲勞", "適當休息",
        "希望這張", "幫助到您", "祝", "謝謝",
    ]
    for term in forbidden_generation_terms:
        if term in output and term not in source_text:
            return False, f"LLM 輸出包含不允許詞：{term}"

    simplified_terms = [
        "训练", "稳定", "每周", "适当", "营养", "疲劳", "过度", "风险",
        "建议", "运动", "评估", "分数", "时间", "次数", "频率",
    ]
    for term in simplified_terms:
        if term in output:
            return False, f"LLM 輸出包含簡體字：{term}"

    english_motion_patterns = [
        "A person", "a person", "person hop", "person moves", "person run",
        "person walk", "person is", "The person", "the person",
    ]
    for term in english_motion_patterns:
        if term in output:
            return False, f"LLM 輸出包含英文動作描述：{term}"

    return True, "pass"


def build_kg_fallback_card(
    class_name: str,
    score_val: int,
    main_text: str,
    numeric_text: str,
    external_fields: dict | None = None,
) -> str:
    """
    KG 建議卡。

    設計原則：
    - KG 提供主要狀態、分數與數值建議。
    - 若 KG 缺少「建議動作 / 怎麼做 / 注意事項」，可由外部 evidence 補足。
    - 外部 evidence 不覆蓋 KG 分數，也不自動生成正式訓練劑量。
    """
    external_fields = external_fields or {}

    status = main_text if not is_missing_field(main_text) else "目前資料不足"

    action = external_fields.get("action", "目前資料不足")
    how_to = external_fields.get("how_to", "目前資料不足")
    caution = external_fields.get("caution", "目前資料不足")

    # 數值仍以 KG 為主，避免外部資料亂補訓練劑量。
    amount = numeric_text if not is_missing_field(numeric_text) else "目前資料不足"
    weekly_goal = numeric_text if not is_missing_field(numeric_text) else "目前資料不足"

    # 給使用者看的報告只呈現外部參考資料網址，不顯示內部 KG/分數來源字樣。
    reference_urls = external_fields.get("reference_urls", []) if external_fields else []
    reference_urls = [str(u).strip() for u in reference_urls if str(u).strip()]
    reference_line = ""
    if reference_urls:
        reference_lines = ["\n- **參考資料**："]
        for idx, url in enumerate(reference_urls[:3], start=1):
            reference_lines.append(f"  {idx}. {url}")
        reference_line = "\n".join(reference_lines)

    return (
        f"### {class_name}\n"
        f"- **目前狀態**：{status}\n"
        f"- **建議動作**：{action}\n"
        f"- **怎麼做**：{how_to}\n"
        f"- **每次做多少**：{amount}\n"
        f"- **每週目標**：{weekly_goal}\n"
        f"- **注意事項**：{caution}"
        f"{reference_line}"
    )

def safe_generate_action_card(prompt_payload: str, fallback_text: str, max_new_tokens: int = 420) -> str:
    """
    生成 KG 建議卡。
    預設 USE_LLM=0：直接使用知識圖譜安全格式，避免 Jetson 逐筆生成卡住。
    若執行時加 USE_LLM=1，才會呼叫 Qwen 做文字改寫。

    使用者報告版：
    - 不輸出「系統提醒」等內部工程訊息。
    - 若 LLM 未啟用、生成失敗或安全檢查失敗，直接回退到 fallback_text。
    """
    if not USE_LLM:
        return fallback_text

    generated = rag_generate_action_card(prompt_payload, max_new_tokens=max_new_tokens).strip()
    generated = normalize_traditional_text(generated)

    if not generated:
        return fallback_text

    ok, reason = validate_llm_recommendation(generated, prompt_payload)
    if ok:
        return generated

    return fallback_text

# ============================================================
# 2. Neo4j 連線
# ============================================================
URI      = os.getenv("NEO4J_URI",      "bolt://localhost:7687")
USER     = os.getenv("NEO4J_USER",     "neo4j")
PASSWORD = os.environ["NEO4J_PASSWORD"]  # 不再用公開的預設密碼；請設定環境變數（docs/04、週一清單 3-9）

driver = GraphDatabase.driver(URI, auth=(USER, PASSWORD))

def check_connection():
    try:
        with driver.session() as session:
            session.run("RETURN 1").single()
        print("✅ Neo4j 連線成功！")
    except Exception as e:
        print(f"❌ Neo4j 連線失敗: {e}")
        raise SystemExit(1)

check_connection()

# ============================================================
# 3. 載入分類資料 + 建向量索引
# ============================================================
TYPO_FIXES = {
    "stationery": "stationary",
    "attemptes": "attempts",
    "hopps": "hops",
    "standstill": "stands still",
    "her/his": "their",
}

def normalize_text(text: str) -> str:
    text = str(text).lower().strip()
    for a, b in TYPO_FIXES.items():
        text = text.replace(a, b)
    return re.sub(r"\s+", " ", text)


def load_candidate_table(csv_path: Path) -> pd.DataFrame:
    if not csv_path.exists():
        raise FileNotFoundError(f"❌ 找不到分類資料：{csv_path}")
    df = pd.read_csv(csv_path)
    required = {"coarsed_sentence", "class", "score"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"❌ sorted_data.csv 缺少欄位：{missing}")
    df = df.dropna(subset=list(required)).copy()
    df["text_norm"] = df["coarsed_sentence"].map(normalize_text)
    df["class"]     = df["class"].astype(str).str.strip()
    df["score"]     = df["score"].astype(int)
    print(f"✅ 讀取分類資料：{len(df)} 筆\n{df['class'].value_counts().to_string()}")
    return df

candidate_df = load_candidate_table(CSV_PATH)

print("\n🧠 建立候選向量索引...")
candidate_embeddings = embedder.encode(
    candidate_df["text_norm"].tolist(),
    batch_size=32,          # ── 優化④：加大 batch_size
    convert_to_numpy=True,
    normalize_embeddings=True,
    show_progress_bar=True,
)
print("✅ 向量索引完成")

# ============================================================
# 4. 規則層
# ============================================================
def split_motion_segments(motion: str) -> list[str]:
    parts = re.split(r"\s*(?:->|;|, then| followed by | and then )\s*", str(motion), flags=re.I)
    return [p.strip() for p in parts if p.strip()]


def detect_base_classes(text: str, speed: str = "") -> tuple[list[str], list[str]]:
    t = normalize_text(text)
    s = str(speed).lower().strip()
    classes: list[str] = []
    reasons: list[str] = []

    def add(cls: str, why: str):
        if cls not in classes:
            classes.append(cls)
        reasons.append(why)

    if re.search(r"\b(hop|hops|hopping)\b.*\b(one foot|single foot|one leg)\b", t):
        add("單腳站立", "偵測到 hop on one foot / 單腳跳")
        add("下肢肌力", "單腳跳也與下肢肌力相關")
        return classes, reasons

    if any(k in t for k in ["sit down", "sits down", "sitting down", "stand up", "stands up", "stood up", "squat", "crouch"]):
        add("下肢肌力", "偵測到 sit/stand/squat 類姿勢轉換")

    if any(k in t for k in ["run in place", "runs in place", "running in place"]):
        add("心肺耐力", "偵測到 run in place / 原地跑")
    elif re.search(r"\b(run|runs|running)\b", t):
        add("心肺耐力", "偵測到 run/running")
        if any(k in t for k in ["forward", "backward"]):
            add("來回行走", "偵測到 forward/backward 的來回移動")

    if re.search(r"\b(jump|jumps|jumping|hop|hops|hopping)\b", t):
        add("下肢肌力", "偵測到 jump/hop")
        if any(k in t for k in ["multiple times", "repeatedly", "several times"]):
            add("心肺耐力", "偵測到多次跳躍")

    if any(k in t for k in ["bend", "bends", "bending", "pick up", "picks up", "raise hand", "raises hand", "raise their hand", "arm", "hands"]):
        add("上肢與軀幹活動", "偵測到 bend/hand/arm/pick up")

    if any(k in t for k in ["quickly", "slowly", "fast", "speed"]):
        add("步行速度", "偵測到速度描述")
    if s == "fast" and re.search(r"\b(move|moves|walk|walks|run|runs)\b", t):
        add("步行速度", "Speed=Fast 且動作為移動類")

    spatial_words = ["forward", "backward", "left", "right", "side", "diagonal", "circle", "clockwise", "counter-clockwise"]
    if any(k in t for k in spatial_words):
        add("空間方位位移", "偵測到方向詞")

    if any(k in t for k in ["upward", "downward"]):
        if not any(k in t for k in ["sit", "stand", "bend", "jump", "hop"]):
            add("空間方位位移", "偵測到 upward/downward 且非姿勢轉換")

    if not classes and any(k in t for k in ["stationary", "stays still", "stand still", "stands still"]):
        add("其他", "只有 stationary，資訊不足")

    if not classes:
        add("其他", "未命中明確動作規則")

    return classes, reasons


def class_matches_allowed(candidate_class: str, allowed_classes: list[str]) -> bool:
    parts = [p.strip() for p in str(candidate_class).split("+")]
    return any(a in parts or a == candidate_class for a in allowed_classes)


def class_rule_bonus(candidate_class: str, allowed_classes: list[str]) -> float:
    parts = [p.strip() for p in str(candidate_class).split("+")]
    if candidate_class in allowed_classes:
        return 0.10
    if any(a in parts for a in allowed_classes):
        return 0.05
    return 0.0

# ── 優化⑤：預先把 class → numpy mask 快取起來，避免每次都重新 map ──
_class_array = candidate_df["class"].to_numpy()

def _build_mask(allowed_tuple: tuple[str, ...]) -> np.ndarray:
    """把 allowed_classes 轉成 numpy bool mask，可向量化。"""
    mask = np.zeros(len(_class_array), dtype=bool)
    for cls in allowed_tuple:
        mask |= (_class_array == cls)
        for i, c in enumerate(_class_array):
            if not mask[i] and cls in [p.strip() for p in str(c).split("+")]:
                mask[i] = True
    return mask

# ============================================================
# 5. 批次向量匹配（核心優化）
# ============================================================

# ── 優化⑥：把「單筆 encode」改成「先收集全部 segment → 一次 batch encode」──
# 這個函式現在只做純 numpy 計算，不做任何 GPU encode
def match_precomputed_embedding(
    query_emb: np.ndarray,
    sentence: str,
    speed: str = "",
) -> dict:
    norm_sentence = normalize_text(sentence)
    allowed_classes, reasons = detect_base_classes(norm_sentence, speed)

    mask = _build_mask(tuple(sorted(allowed_classes)))
    filtered_idx = np.where(mask)[0]
    fallback_all = False
    if len(filtered_idx) == 0:
        filtered_idx = np.arange(len(candidate_df))
        fallback_all = True

    sims = candidate_embeddings[filtered_idx] @ query_emb

    rows = []
    for local_i, sim in enumerate(sims):
        idx = int(filtered_idx[local_i])
        row = candidate_df.iloc[idx]
        bonus = class_rule_bonus(row["class"], allowed_classes)
        rows.append({
            "idx": idx,
            "matched_text": row["coarsed_sentence"],
            "class_name": row["class"],
            "score_val": int(row["score"]),
            "similarity": float(sim),
            "rule_bonus": bonus,
            "final_score": float(sim + bonus),
            "allowed_classes": allowed_classes,
            "rule_reasons": reasons,
            "fallback_all": fallback_all,
        })

    rows.sort(key=lambda r: r["final_score"], reverse=True)
    best = rows[0]
    best["top_candidates"] = rows[:TOP_K_DEBUG]
    best["confidence"] = (
        "high"          if best["similarity"] >= SIM_ACCEPT else
        "medium_review" if best["similarity"] >= SIM_REVIEW else
        "low"
    )
    return best

# ============================================================
# 6. Neo4j 查詢 + LRU 快取
# ============================================================

# ── 優化⑦：LRU cache 避免相同 class+score 重複打 DB ──
@lru_cache(maxsize=256)
def query_advice_by_class_score(class_name: str, score_val: int) -> str | None:
    cypher = """
    MATCH (class_node:Class {name: $class_name})-[:has_level]->(criteria:Criteria)
    WHERE toInteger(criteria.score) = toInteger($score_val)
    OPTIONAL MATCH (criteria)-[:give_advice]->(suggestion:Suggestion)
    RETURN suggestion.content AS advice
    LIMIT 1
    """
    with driver.session() as session:
        rec = session.run(cypher, class_name=class_name, score_val=score_val).single()
    if rec and rec.get("advice"):
        return rec.get("advice")
    for part in str(class_name).split("+"):
        part = part.strip()
        with driver.session() as session:
            rec = session.run(cypher, class_name=part, score_val=score_val).single()
        if rec and rec.get("advice"):
            return rec.get("advice")
    return None

# ============================================================
# 7. RAG 正式報告模式
# ============================================================
# 本版本保留 RAG 流程：
# all-MiniLM-L6-v2 負責檢索最相似標竿句，Neo4j 提供知識圖譜建議，
# Qwen 只根據已檢索到的知識圖譜內容進行文字整理。
# 報告章節與欄位仍由 Python 固定控制，以降低幻覺與格式漂移。

# ============================================================
# 8. 整合管線（重新設計為批次架構）
# ============================================================

PRIORITY = {
    "單腳站立":     0.25,
    "心肺耐力":     0.15,
    "下肢肌力":     0.12,
    "步行速度":     0.10,
    "來回行走":     0.08,
    "上肢與軀幹活動": 0.06,
    "空間方位位移":  0.00,
    "其他":        -0.10,
}

def _selection_score(r: dict) -> float:
    parts = [p.strip() for p in str(r["class_name"]).split("+")]
    pri = max([PRIORITY.get(p, 0) for p in parts] + [PRIORITY.get(r["class_name"], 0)])
    return r["final_score"] + pri


def parse_timestamp(ts: str) -> datetime:
    raw = str(ts).strip()
    cleaned = re.sub(r"\s*(AM|PM)\s*$", "", raw, flags=re.I)
    for fmt in ["%Y/%m/%d %H:%M", "%Y-%m-%d %H:%M", "%Y/%m/%d %I:%M %p"]:
        try:
            return datetime.strptime(cleaned if "%p" not in fmt else raw, fmt)
        except ValueError:
            continue
    raise ValueError(f"無法解析時間：{ts}")


def compute_time_delta(records: list[dict]) -> list[float | None]:
    deltas = [None]
    for i in range(1, len(records)):
        try:
            t1 = parse_timestamp(records[i - 1]["Timestamp"])
            t2 = parse_timestamp(records[i]["Timestamp"])
            deltas.append(abs((t2 - t1).total_seconds() / 60))
        except Exception:
            deltas.append(None)
    return deltas


def run_pipeline(input_json: list[dict], skip_neo4j: bool = False) -> str:
    print(f"\n🚀 開始分析 {len(input_json)} 筆動作記錄...\n" + "=" * 60)

    # 時間排序
    try:
        input_json = sorted(input_json, key=lambda r: parse_timestamp(r["Timestamp"]))
    except Exception as e:
        print(f"⚠️ 時間排序失敗：{e}")

    time_deltas = compute_time_delta(input_json)

    # ─────────────────────────────────────────────────────────
    # Phase 1：解析所有 segments，準備批次 encode
    # ─────────────────────────────────────────────────────────
    print("\n📦 Phase 1：解析所有 segments...")
    record_meta = []   # [(timestamp, motion, speed, segments_list)]
    all_norm_segs: list[str] = []   # 所有 segment 的 normalized text（flat list）
    seg_ptr: list[tuple[int, int]] = []  # 每筆 record 對應的 slice in all_norm_segs

    for rec in input_json:
        motion = rec.get("Motion", "")
        speed  = str(rec.get("Speed", "")).strip().lower()
        segs   = split_motion_segments(motion) or [motion]
        record_meta.append((rec.get("Timestamp", ""), motion, speed, segs))

        start = len(all_norm_segs)
        for seg in segs:
            all_norm_segs.append(normalize_text(seg))
        seg_ptr.append((start, len(all_norm_segs)))

    # ─────────────────────────────────────────────────────────
    # Phase 2：一次性批次 encode 所有 segments（最大優化點）
    # ─────────────────────────────────────────────────────────
    print(f"\n🔢 Phase 2：批次 encode {len(all_norm_segs)} 個 segments（一次 GPU 推論）...")
    all_query_embs = embedder.encode(
        all_norm_segs,
        batch_size=32,          # 充分填滿 GPU
        convert_to_numpy=True,
        normalize_embeddings=True,
        show_progress_bar=True,
    )
    print("✅ 批次 encode 完成")

    # ─────────────────────────────────────────────────────────
    # Phase 3：向量匹配（純 numpy，CPU 多線程）
    # ─────────────────────────────────────────────────────────
    print("\n🔍 Phase 3：向量匹配...")

    def match_record(i: int) -> dict:
        ts, motion, speed, segs = record_meta[i]
        s, e = seg_ptr[i]
        seg_results = []
        for j, seg in enumerate(segs):
            qemb = all_query_embs[s + j]
            seg_results.append(match_precomputed_embedding(qemb, seg, speed))
        best = sorted(seg_results, key=_selection_score, reverse=True)[0]
        return {
            "idx": i, "timestamp": ts, "motion": motion, "speed": speed,
            "segments": segs, "segment_results": seg_results, "best": best,
        }

    # ── 優化⑨：CPU 多線程加速純 numpy 匹配（GIL 在 numpy 時釋放）──
    match_results = [None] * len(input_json)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = {pool.submit(match_record, i): i for i in range(len(input_json))}
        for fut in as_completed(futures):
            r = fut.result()
            match_results[r["idx"]] = r

    # ─────────────────────────────────────────────────────────
    # Phase 4：Neo4j 並行查詢（I/O 密集，大量並行）
    # ─────────────────────────────────────────────────────────
    if skip_neo4j:
        print(f"\n⏭️  Phase 4：跳過 Neo4j 查詢（--no-db 模式）...")
        for r in match_results:
            r["advice"] = None
    else:
        print(f"\n🌐 Phase 4：並行查詢 Neo4j（{MAX_NEO4J_WORKERS} 線程）...")

        def fetch_advice(r: dict) -> dict:
            best = r["best"]
            sim = best["similarity"]

            # similarity < 0.7：不進知識圖譜，避免低信心匹配造成錯誤 grounding
            if sim < SIM_ACCEPT:
                r["advice"] = None
                r["skip_kg"] = True
                r["kg_skip_reason"] = (
                    f"向量相似度 {sim:.4f} 低於門檻 {SIM_ACCEPT}，"
                    "不採用知識圖譜匹配結果，改由原始句子進行外部搜尋。"
                )
                return r

            r["skip_kg"] = False
            r["kg_skip_reason"] = ""

            advice = query_advice_by_class_score(best["class_name"], best["score_val"])
            r["advice"] = advice
            return r

        with ThreadPoolExecutor(max_workers=MAX_NEO4J_WORKERS) as pool:
            match_results = list(pool.map(fetch_advice, match_results))

    # ─────────────────────────────────────────────────────────
    # Phase 5：依知識圖譜產生固定格式個別建議
    # ─────────────────────────────────────────────────────────
    print("\n🧱 Phase 5：產生固定格式個別建議...")

    def build_fixed_advice(r: dict) -> str:
        """
        KG + 外部搜尋 evidence 合併版。

        新版重點：
        - 每筆都保留 KG matching 結果。
        - similarity < SIM_ACCEPT 時，不採用 KG 作為正式依據。
        - 外部搜尋只查「動作難度 / 功能性動作 / 復健建議」。
        - 若 KG 有建議但缺「建議動作 / 怎麼做 / 注意事項」，用外部 evidence 補足。
        - 不用外部 evidence 補 KG 分數或訓練劑量。
        - 預設不逐筆呼叫 Qwen，用模板穩定輸出；USE_LLM=1 才對 KG 內容做改寫。
        """
        best = r["best"]
        confidence = best["confidence"]
        similarity = best["similarity"]
        advice = r.get("advice")
        kg_skipped = bool(r.get("skip_kg"))
        kg_skip_reason = r.get("kg_skip_reason", "")

        class_name = best["class_name"]
        score_val = best["score_val"]

        kg_has_advice = bool(advice) and not kg_skipped

        # --------------------------------------------------------
        # 先跑外部搜尋，因為 KG 缺欄位時要用外部 evidence 補足
        # --------------------------------------------------------
        if kg_skipped:
            search_metric = None
        elif class_name in ["其他", "未知", "無法分類"]:
            search_metric = None
        else:
            search_metric = class_name

        evidence_bundle = None
        evidence_count = 0
        external_card = ""
        external_search_error = ""

        try:
            ext_result = external_search_fallback(
                motion=r["motion"],
                metric=search_metric,
                speed=r["speed"],
                max_results=EXTERNAL_MAX_RESULTS,
                max_pages=EXTERNAL_MAX_PAGES,
            )
            evidence_bundle = ext_result["evidence_bundle"]
            evidence_count = len(evidence_bundle.get("evidence", []))

            # Enhanced Vector Retrieval：只檢索原文片段，不建立摘要樹，降低摘要節點造成的幻覺。
            query_text = evidence_bundle.get("query", "")
            for item in evidence_bundle.get("evidence", []):
                page_text = item.get("page_text", "")
                if page_text:
                    retrieved_nodes = enhanced_vector_retrieve_from_page(
                        query=query_text,
                        page_text=page_text,
                        top_k=3,
                    )
                    item["retrieval_context"] = "\n".join(retrieved_nodes)
                    # 保留舊欄位名稱，避免後面 build_external_template_card / get_external_context_text 壞掉。
                    item["raptor_context"] = item["retrieval_context"]
                else:
                    item["retrieval_context"] = item.get("snippet", "")
                    item["raptor_context"] = item["retrieval_context"]

        except Exception as e:
            evidence_bundle = {
                "query": "",
                "evidence": [],
                "skip_reason": f"外部搜尋失敗：{e}",
                "topic": "",
                "external_search_skipped": True,
            }
            evidence_count = 0
            external_search_error = str(e)

        # 保留結構化外部 evidence，給 Phase 6 整體建議使用。
        # 不要只從 fixed_advice 文字反解析，避免格式變動時整體建議漏掉外部卡片。
        r["evidence_bundle"] = evidence_bundle
        r["external_search_error"] = external_search_error

        external_fields = build_external_field_supplements(
            motion=r["motion"],
            class_name=class_name,
            score_val=score_val,
            evidence_bundle=evidence_bundle,
        )

        lines = ["#### 健康建議"]

        # --------------------------------------------------------
        # 一、知識圖譜 evidence
        # --------------------------------------------------------
        lines += [
            "",
            "##### 一、知識圖譜檢索結果",
            f"- **匹配類別**：{class_name}",
            f"- **評估分數**：{score_val}",
            f"- **匹配語句**：{best['matched_text']}",
            f"- **向量相似度**：{similarity:.4f}",
        ]

        if kg_skipped:
            lines.append(f"- **KG 採用狀態**：未採用。{kg_skip_reason}")
        elif kg_has_advice:
            if external_fields:
                lines.append("- **KG 採用狀態**：採用。相似度達門檻且知識圖譜有對應建議；缺失欄位已由動作分析/外部 evidence 補足。")
            else:
                lines.append("- **KG 採用狀態**：採用。相似度達門檻且知識圖譜有對應建議。")
        else:
            lines.append(
                f"- **KG 採用狀態**：未採用。知識圖譜缺少「{class_name} / 分數 {score_val}」對應建議。"
            )

        kg_card_text = ""
        main_text = ""
        numeric_text = "目前資料不足"

        if kg_has_advice:
            if "【建議數值】" in advice:
                main_text, numeric_text = advice.split("【建議數值】", 1)
                main_text = main_text.strip()
                numeric_text = numeric_text.strip()
            else:
                main_text = advice.strip()
                numeric_text = "目前資料不足"

            prompt_payload = (
                "【知識圖譜資料】\n"
                f"能力名稱：{class_name}\n"
                f"評估分數：{score_val}\n"
                f"能力狀態：{main_text}\n"
                f"建議數值：{numeric_text}\n\n"
                "【外部 evidence 補足欄位】\n"
                f"建議動作：{external_fields.get('action', '目前資料不足')}\n"
                f"怎麼做：{external_fields.get('how_to', '目前資料不足')}\n"
                f"注意事項：{external_fields.get('caution', '目前資料不足')}\n\n"
                "【生成要求】\n"
                "請根據以上知識圖譜資料生成固定格式健康建議卡。\n"
                "若知識圖譜缺少建議動作、怎麼做或注意事項，可使用【外部 evidence 補足欄位】補齊。\n"
                "但評估分數、每次做多少、每週目標只能來自知識圖譜。\n"
                "不得新增疾病診斷、醫療處方、訓練劑量或未提供的數字。\n"
                "請使用繁體中文，不要使用簡體字。\n"
            )

            fallback_text = build_kg_fallback_card(
                class_name=class_name,
                score_val=score_val,
                main_text=main_text,
                numeric_text=numeric_text,
                external_fields=external_fields,
            )
            kg_card_text = safe_generate_action_card(
                prompt_payload,
                fallback_text=fallback_text,
                max_new_tokens=260,
            )
            lines += ["", kg_card_text]
        else:
            lines.append("- **KG 建議內容**：目前沒有可直接採用的知識圖譜建議。")

        # --------------------------------------------------------
        # 二、外部搜尋 evidence
        # --------------------------------------------------------
        lines += ["", "##### 二、外部搜尋補充"]

        if external_search_error:
            lines.append(f"- **外部搜尋狀態**：執行失敗，原因：{external_search_error}")
            lines.append("- **外部補充內容**：外部搜尋失敗，僅保留知識圖譜與安全提醒。")
        else:
            lines.append(f"- **外部搜尋 Query**：`{evidence_bundle.get('query', '')}`")
            lines.append(f"- **外部 evidence 筆數**：{evidence_count}")

            if evidence_count == 0:
                skip_reason = evidence_bundle.get("skip_reason", "") if evidence_bundle else ""
                topic = evidence_bundle.get("topic", "") if evidence_bundle else ""
                if skip_reason:
                    lines.append("- **外部搜尋狀態**：未採用外部資料。")
                    lines.append(f"- **未採用原因**：{skip_reason}")
                    lines.append(f"- **判斷主題**：{topic}")
                lines.append(
                    "- **外部補充內容**：此筆資料沒有取得足夠相關的外部 evidence，僅保留低風險安全提醒。"
                )
            else:
                external_card = build_external_template_card(
                    evidence_bundle=evidence_bundle,
                    class_name=class_name,
                    score_val=score_val,
                )
                lines += ["", external_card, ""]
                lines += render_external_sources(evidence_bundle)

        return "\n".join(lines)

    for r in match_results:
        r["fixed_advice"] = build_fixed_advice(r)

    # ─────────────────────────────────────────────────────────
    # Phase 6：RAG + LLM 結構化整體建議生成
    # ─────────────────────────────────────────────────────────
    print("\n📝 Phase 6：RAG + LLM 結構化整體建議生成...")

    def split_advice_text(advice: str) -> tuple[str, str]:
        """
        將圖譜建議拆成：
        1. 文字建議
        2. 【建議數值】後面的數值內容
        """
        if "【建議數值】" in advice:
            main_text, numeric_text = advice.split("【建議數值】", 1)
            return main_text.strip(), numeric_text.strip()
        return advice.strip(), ""

    # 同一能力若出現多筆，整體建議採最低分那一筆，避免過度樂觀
    overall_by_class: dict[str, dict] = {}

    for r in match_results:
        best = r["best"]
        class_name = best["class_name"]
        score_val = best["score_val"]
        advice = r.get("advice")

        if not advice:
            continue

        if class_name not in overall_by_class:
            overall_by_class[class_name] = {
                "score": score_val,
                "advice": advice,
            }
        else:
            old_score = overall_by_class[class_name]["score"]
            if score_val < old_score:
                overall_by_class[class_name] = {
                    "score": score_val,
                    "advice": advice,
                }

    # 僅根據圖譜建議原文中已明示的內容判斷是否需要加強
    strengthen_keywords = ["不足", "偏低", "需加強", "需要加強"]
    strengthen_items: list[tuple[str, str]] = []

    for class_name, item in overall_by_class.items():
        advice = item["advice"]
        if any(k in advice for k in strengthen_keywords):
            strengthen_items.append((class_name, advice))

    # 固定排序，讓報告每次順序一致
    overall_order = [
        "下肢肌力",
        "心肺耐力",
        "步行速度",
        "單腳站立",
        "上肢與軀幹活動",
        "來回行走",
        "空間方位位移",
        "其他",
    ]

    sorted_overall_items = sorted(
        overall_by_class.items(),
        key=lambda x: overall_order.index(x[0]) if x[0] in overall_order else 999
    )

    class_display_names = {
        "下肢肌力": "下肢肌力",
        "心肺耐力": "心肺耐力",
        "步行速度": "步行能力",
        "單腳站立": "平衡穩定性",
        "上肢與軀幹活動": "上肢與軀幹活動",
        "來回行走": "來回行走",
        "空間方位位移": "空間方位位移",
        "其他": "其他",
    }

    def join_readable(items: list[str]) -> str:
        if not items:
            return ""
        if len(items) == 1:
            return items[0]
        if len(items) == 2:
            return "與".join(items)
        return "、".join(items[:-1]) + "與" + items[-1]

    def render_overall_sentence(class_name: str, advice: str) -> str:
        """
        僅將知識圖譜 advice 原文改成較連貫的正式報告句型。
        不新增 advice 原文之外的運動、頻率、器材或醫療判斷。
        """
        main_text, numeric_text = split_advice_text(advice)

        intro_map = {
            "下肢肌力": "在下肢肌力方面",
            "心肺耐力": "在心肺耐力方面",
            "步行速度": "在步行能力方面",
            "單腳站立": "在平衡訓練方面",
            "上肢與軀幹活動": "在上肢與軀幹活動方面",
            "來回行走": "在來回行走方面",
            "空間方位位移": "在空間方位位移方面",
            "其他": "在其他能力方面",
        }
        intro = intro_map.get(class_name, f"在{class_name}方面")

        if numeric_text:
            return f"{intro}，{main_text} 建議數值為：{numeric_text}。"
        return f"{intro}，{main_text}"

    present_classes = [
        class_display_names.get(class_name, class_name)
        for class_name, _ in sorted_overall_items
    ]

    overall_paragraphs: list[str] = []

    if present_classes:
        overall_paragraphs.append(
            f"本週建議以**{join_readable(present_classes)}**為主要訓練方向。"
        )
        overall_paragraphs.extend(
            render_overall_sentence(class_name, item["advice"])
            for class_name, item in sorted_overall_items
        )
    else:
        overall_paragraphs.append("目前知識圖譜中沒有可直接採用的整體建議內容。")

    # ─────────────────────────────────────────────────────────
    # 整體建議：直接使用 Phase 5 個別分析中已完成的 KG + 外部 evidence 補欄位結果
    # 目的：避免整體建議重新用舊 KG card，導致「建議動作 / 怎麼做 / 注意事項」又變成目前資料不足。
    # ─────────────────────────────────────────────────────────
    def extract_kg_card_from_fixed_advice(fixed_advice: str) -> str:
        """
        從個別 fixed_advice 中擷取「使用者需要看的 KG 建議卡」。

        個別建議區塊前面會有「### 健康建議」與「一、知識圖譜檢索結果」，
        這些是系統診斷資訊，不應出現在最後的整體建議。
        因此這裡只擷取真正的能力卡，例如「### 下肢肌力」。
        """
        if not fixed_advice:
            return ""

        # 只看外部搜尋補充之前的 KG 區塊
        kg_part = re.split(r"\n##### 二、外部搜尋補充|\n##### 三、合併建議", fixed_advice, maxsplit=1)[0]

        # 找出所有三級標題，跳過「健康建議」，取最後一個真正能力標題。
        headings = list(re.finditer(r"(?m)^###\s+(.+?)\s*$", kg_part))
        start = None
        for h in headings:
            title = h.group(1).strip()
            if title and title != "健康建議":
                start = h.start()

        if start is None:
            return ""

        card = kg_part[start:].strip()

        # 最後再清掉系統內部提醒，讓整體建議更像給使用者看的報告。
        card_lines = []
        for line in card.splitlines():
            if "**系統提醒**" in line:
                continue
            card_lines.append(line)

        return "\n".join(card_lines).strip()

    def extract_external_card_from_fixed_advice(fixed_advice: str) -> str:
        """
        從個別 fixed_advice 中擷取外部 evidence 補充卡。
        只在外部 evidence 筆數 > 0 時擷取，避免把 skipped / 0 evidence 放進整體建議。
        """
        if not fixed_advice:
            return ""

        sec_match = re.search(
            r"##### 二、外部搜尋補充\n(.+?)(?=\n##### 三、合併建議|\n---|\Z)",
            fixed_advice,
            flags=re.S,
        )
        if not sec_match:
            return ""

        sec = sec_match.group(1).strip()

        # 沒有 evidence 的外部搜尋不放進整體建議
        if "外部 evidence 筆數**：0" in sec or "外部 evidence 筆數：0" in sec:
            return ""

        # 擷取真正的外部卡內容：能力項目：... 到 外部來源紀錄之前
        card_match = re.search(
            r"(能力項目：.+?)(?=\n- \*\*外部來源紀錄\*\*|\Z)",
            sec,
            flags=re.S,
        )
        card = card_match.group(1).strip() if card_match else sec

        lines = []
        for line in card.splitlines():
            line = line.strip()
            if not line:
                continue
            # 這些是系統內部資訊，不放進整體建議
            if line.startswith("資料來源類型：") or line.startswith("信心程度："):
                continue
            lines.append(line)

        return "\n".join(lines).strip()

    def parse_external_field(card: str, field_name: str) -> str:
        """
        從外部卡中抓指定欄位，例如「能力項目：...」。
        """
        if not card:
            return ""

        field_names = [
            "能力項目", "判斷說明", "動作組成", "外部資料主題", "外部資料重點",
            "動作難度觀察", "復健動作建議", "注意事項", "資料來源類型", "信心程度",
        ]
        other_fields = "|".join(re.escape(x) for x in field_names if x != field_name)
        pattern = rf"{re.escape(field_name)}：(.+?)(?=\n(?:{other_fields})：|\Z)"
        m = re.search(pattern, card, flags=re.S)
        if not m:
            return ""
        return re.sub(r"\s+", " ", m.group(1)).strip()

    def extract_reference_urls(text: str) -> list[str]:
        """
        從整段 fixed_advice 擷取外部來源網址，給使用者看的整體建議使用。
        """
        urls = re.findall(r"https?://[^\s)）]+", text or "")
        clean_urls: list[str] = []
        for url in urls:
            url = url.rstrip("。.,，；;]")
            if url and url not in clean_urls:
                clean_urls.append(url)
        return clean_urls

    def external_group_key_title(ability_name: str, components: str, fixed_advice: str) -> tuple[str, str]:
        """
        依 external card 的能力名稱/動作組成對應到 canonical group。
        v6 修正：不要先判斷步態，避免「上肢+稍微移動」全部被歸進步態。
        """
        text_all = f"{ability_name} {components} {fixed_advice}".lower()

        title_to_key = {
            "單腳站立與平衡控制": "single_leg_balance",
            "坐站轉移與下肢肌力": "sit_to_stand",
            "跳躍與落地控制": "jump_landing",
            "彎身下蹲與下肢控制": "bend_squat",
            "步態與方向控制": "gait_direction",
            "上肢與軀幹控制": "upper_trunk",
            "手部功能與日常操作": "hand_function",
            "耐力與活動耐受度": "endurance",
        }
        for title, key in title_to_key.items():
            if title in ability_name:
                return key, MOVEMENT_GROUP_PROFILES[key]["title"]

        # priority：更特殊的動作先判斷，步態最後，避免混入 walking 字眼就全部變步態。
        ordered = [
            ("single_leg_balance", ["單腳", "one foot", "single leg"]),
            ("sit_to_stand", ["坐站", "站起", "坐下", "sit to stand", "chair rise"]),
            ("jump_landing", ["跳躍", "落地", "jump", "landing"]),
            ("bend_squat", ["彎身", "下蹲", "squat", "bending", "bend"]),
            ("upper_trunk", ["上肢", "軀幹", "抬手", "肩", "揮手", "upper", "trunk", "shoulder", "arm raise"]),
            ("hand_function", ["喝水", "手部", "抓握", "閱讀", "手機", "杯", "hand", "grasp", "cup", "phone", "book"]),
            ("endurance", ["心肺", "耐力", "endurance", "aerobic", "running", "run"]),
            ("gait_direction", ["步態", "行走", "前後", "方向", "轉身", "側向", "空間方位", "gait", "walking", "turn", "lateral", "direction", "forward", "backward"]),
        ]
        for key, kws in ordered:
            if any(k in text_all for k in kws):
                return key, MOVEMENT_GROUP_PROFILES[key]["title"]

        return "functional_movement", MOVEMENT_GROUP_PROFILES["functional_movement"]["title"]

    def extract_external_overall_item(fixed_advice: str) -> dict:
        """
        從個別 fixed_advice 中抽出外部 evidence，先不立刻產生卡片。
        先抽成資料，後面再依能力群組合併，避免同類項目重複出現。
        """
        ext_card = extract_external_card_from_fixed_advice(fixed_advice)
        if not ext_card:
            return {}

        ability_name = parse_external_field(ext_card, "能力項目") or "功能性動作觀察"
        components = parse_external_field(ext_card, "動作組成")

        # v10：不要再從整段 fixed_advice 盲抓所有 URL。
        # 多個外部卡片混在同一筆紀錄時，盲抓會把「步態」綁到「凱格爾」之類錯誤來源。
        # 這裡改成：先讀本卡片的「參考來源：標題」，再到外部來源紀錄中找對應 URL。
        source_titles = []
        for m in re.finditer(r"參考來源：([^\n]+)", ext_card):
            raw_titles = re.sub(r"\s+", " ", m.group(1)).strip()
            for title in re.split(r"[；;]", raw_titles):
                title = title.strip()
                if title and title != "未取得可用外部資料" and title not in source_titles:
                    source_titles.append(title)

        source_lines = re.findall(r"\d+\.\s*(.+?)\s*—\s*(https?://[^\s)）]+)", fixed_advice or "")
        urls = []
        for src_title, src_url in source_lines:
            src_title_clean = re.sub(r"\s+", " ", src_title).strip()
            for wanted in source_titles:
                # 允許截斷標題的模糊匹配，但不要完全無關就綁上去。
                if wanted in src_title_clean or src_title_clean in wanted:
                    src_url = src_url.rstrip("。.,，；;]")
                    if src_url and src_url not in urls:
                        urls.append(src_url)

        # 若只有一個來源，才允許 fallback；多來源時不盲抓，避免錯綁。
        if not urls:
            all_urls = extract_reference_urls(fixed_advice)
            if len(all_urls) == 1:
                urls = all_urls

        if not urls:
            # 沒有可追溯且可對應到本卡片的來源，不放進整體建議
            return {}

        group_key, group_title = external_group_key_title(ability_name, components, ext_card)
        return {
            "group_key": group_key,
            "group_title": group_title,
            "ability_name": ability_name,
            "components": components,
            "urls": urls,
        }

    def render_external_group_overall_card(group: dict) -> str:
        """
        將同一 canonical movement group 的外部 evidence 合併成一張使用者卡片。
        v6：直接使用 MOVEMENT_GROUP_PROFILES，避免每張外部卡都出現重複泛用文字。
        """
        key = group["group_key"]
        profile = MOVEMENT_GROUP_PROFILES.get(key, MOVEMENT_GROUP_PROFILES["functional_movement"])
        title = profile["title"]
        urls = group.get("urls", [])

        # 僅保留本 group 自己的核心動作組成，不把其他 group 的 components 混進來。
        components_text = "、".join(profile["components_zh"])

        lines = [
            f"### {title}",
            f"- **目前狀態**：{profile['status']}",
            f"- **建議動作**：{profile['action']}",
            f"- **動作重點**：{components_text}",
            f"- **怎麼做**：{profile['how']}",
            f"- **注意事項**：{profile['caution']}",
        ]

        if urls:
            lines.append("- **參考資料**：")
            for idx, url in enumerate(urls[:3], start=1):
                lines.append(f"  {idx}. {url}")

        return "\n".join(lines).strip()

    overall_cards: list[str] = []
    used_overall_classes: set[str] = set()

    # 1) 優先放 KG 可採用的能力建議卡。
    #    這些卡片已在 Phase 5 被外部 evidence 補足 KG 缺欄位。
    #    同一能力只放一張；若多筆同能力，優先選分數較低者。
    kg_candidates: list[tuple[int, str, str]] = []

    for r in match_results:
        best = r["best"]
        class_name = best["class_name"]
        score_val = int(best["score_val"])

        if not r.get("advice"):
            continue
        if class_name in ["其他", "未知", "無法分類"]:
            continue

        kg_card = extract_kg_card_from_fixed_advice(r.get("fixed_advice", ""))
        if kg_card:
            kg_candidates.append((score_val, class_name, kg_card))

    # 同一 class 取最低分那筆，避免整體建議過度樂觀
    best_kg_by_class: dict[str, tuple[int, str]] = {}
    for score_val, class_name, kg_card in kg_candidates:
        if class_name not in best_kg_by_class or score_val < best_kg_by_class[class_name][0]:
            best_kg_by_class[class_name] = (score_val, kg_card)

    for class_name in overall_order:
        if class_name in best_kg_by_class:
            overall_cards.append(best_kg_by_class[class_name][1])
            used_overall_classes.add(class_name)

    # 放入 overall_order 沒列到的其他 class
    for class_name, (_, kg_card) in best_kg_by_class.items():
        if class_name not in used_overall_classes:
            overall_cards.append(kg_card)
            used_overall_classes.add(class_name)

    # 2) 如果某些資料沒有 KG 可採用，但有外部動作 evidence，
    #    先依「能力群組」合併，再輸出使用者卡片。
    #    這樣可以避免整體建議出現一堆「步態移動、方向控制...」但內容幾乎一樣的卡。
    def extract_external_overall_item_from_result(r: dict) -> dict:
        """
        從結構化 evidence_bundle 建立整體建議用的外部項目。
        這比從 fixed_advice 文字反解析穩定，避免個別建議明明有 Top3 evidence，
        但整體建議因文字格式或標題比對失敗而漏掉。
        """
        evidence_bundle = r.get("evidence_bundle") or {}
        evidence = evidence_bundle.get("evidence") or []
        if not evidence:
            return {}

        group_key = evidence_bundle.get("group_key") or "functional_movement"
        profile = MOVEMENT_GROUP_PROFILES.get(group_key, MOVEMENT_GROUP_PROFILES["functional_movement"])

        urls: list[str] = []
        for item in evidence[:3]:
            url = str(item.get("url", "")).strip().rstrip("。.,，；;]")
            if url and url not in urls:
                urls.append(url)

        if not urls:
            return {}

        return {
            "group_key": group_key,
            "group_title": profile["title"],
            "ability_name": evidence_bundle.get("topic") or profile["title"],
            "components": evidence_bundle.get("movement_components") or "、".join(profile["components_zh"]),
            "urls": urls,
        }

    external_groups: dict[str, dict] = {}

    for r in match_results:
        best = r["best"]
        class_name = best["class_name"]

        # 如果這筆已經有 KG advice 且該 class 已經放進整體 KG card，就不重複放外部卡。
        if r.get("advice") and class_name in used_overall_classes:
            continue

        item = extract_external_overall_item_from_result(r)
        if not item:
            # 保留舊文字解析作為備援，但主要以 evidence_bundle 為準。
            item = extract_external_overall_item(r.get("fixed_advice", ""))
        if not item:
            continue

        key = item["group_key"]
        if key not in external_groups:
            external_groups[key] = {
                "group_key": key,
                "group_title": item["group_title"],
                "components_list": [],
                "urls": [],
                "count": 0,
            }

        external_groups[key]["count"] += 1

        if item.get("components"):
            external_groups[key]["components_list"].append(item["components"])

        for url in item.get("urls", []):
            if url not in external_groups[key]["urls"]:
                external_groups[key]["urls"].append(url)

    external_group_order = [
        "sit_to_stand",
        "single_leg_balance",
        "jump_landing",
        "bend_squat",
        "gait_direction",
        "upper_trunk",
        "hand_function",
        "endurance",
        "functional_movement",
    ]

    external_cards: list[str] = []
    for key in external_group_order:
        if key in external_groups:
            external_cards.append(render_external_group_overall_card(external_groups[key]))

    for key, group in external_groups.items():
        if key not in external_group_order:
            external_cards.append(render_external_group_overall_card(group))

    if external_cards:
        overall_cards.extend(external_cards)

    if overall_cards:
        overall_text = "\n\n".join(overall_cards)
    else:
        overall_text = "目前知識圖譜與外部動作 evidence 中沒有可直接採用的整體建議內容。"

    strengthen_text: list[str] = []

    if strengthen_items:
        for class_name, advice in strengthen_items:
            main_text, _ = split_advice_text(advice)
            strengthen_text.append(
                f"本次紀錄中，**{class_display_names.get(class_name, class_name)}**出現較低分結果，建議優先注意。{main_text}"
            )
    else:
        strengthen_text.append("目前知識圖譜建議中，未標示明確需要優先加強的能力。")

    # ─────────────────────────────────────────────────────────
    # 組裝固定格式 Markdown 報告
    # ─────────────────────────────────────────────────────────
    lines = [
        "# 🏥 長者行動能力分析報告",
        "",
        f"> 分析時間：{datetime.now().strftime('%Y/%m/%d %H:%M')}  ",
        f"> 總記錄筆數：{len(match_results)} 筆  ",
        f"> 運算裝置：{GPU_NAME} (CUDA {torch.version.cuda})  ",
        "",
        "---",
        "",
        "## 一、個別動作分析",
        "",
    ]

    for i, (res, dt) in enumerate(zip(match_results, time_deltas), 1):
        best = res["best"]

        lines += [
            f"### 記錄 {i}：{res['timestamp']}",
            "",
            f"- **動作描述**：{res['motion']}",
            f"- **動作速度**：{res['speed']}",
            f"- **分類結果**：{best['class_name']}",
            f"- **評估分數**：{best['score_val']}",
            f"- **匹配語句**：{best['matched_text']}",
            f"- **向量相似度**：{best['similarity']:.4f}",
            f"- **信心等級**：{best['confidence']}",
        ]

        if dt is not None:
            lines.append(f"- **距上次間隔**：{dt:.1f} 分鐘")

        lines += [
            "",
            res["fixed_advice"],
            "",
            "---",
            "",
        ]

    lines += [
        "## 二、整體建議",
        "",
        "### 本週建議運動",
        "",
        overall_text,
        "",
        "---",
        "",
        "*本報告根據動作紀錄整理，僅供健康參考；如有身體不適或疑問，請諮詢醫師或專業人員。*",
    ]

    return "\n".join(lines)

# ============================================================
# 9. 命令行參數和時間範圍過濾
# ============================================================

def load_records_from_data_dir(data_dir: Path) -> list[dict]:
    """
    掃描固定 data 資料夾內的所有 JSON 檔，合併成一份記錄列表。
    正式時間查詢流程會先讀取 data 資料夾，再由 filter_records_by_time() 依時間範圍篩選。
    """
    if not data_dir.exists():
        print(f"❌ 找不到資料夾：{data_dir}")
        raise SystemExit(1)

    json_files = sorted(p for p in data_dir.glob("*.json") if p.is_file())

    if not json_files:
        print(f"❌ 資料夾中找不到 JSON 檔案：{data_dir}")
        raise SystemExit(1)

    all_records: list[dict] = []
    loaded_files = 0

    for json_file in json_files:
        try:
            with open(json_file, "r", encoding="utf-8") as f:
                data = json.load(f)

            if isinstance(data, list):
                valid_records = [rec for rec in data if isinstance(rec, dict)]
                all_records.extend(valid_records)
                loaded_files += 1

                skipped = len(data) - len(valid_records)
                if skipped:
                    print(f"⚠️  {json_file.name} 有 {skipped} 筆非 dict 資料，已略過。")

            elif isinstance(data, dict):
                all_records.append(data)
                loaded_files += 1

            else:
                print(f"⚠️  略過 {json_file.name}：最外層必須是 list 或 dict。")

        except json.JSONDecodeError as e:
            print(f"⚠️  略過 JSON 格式錯誤檔案：{json_file.name}，原因：{e}")
        except Exception as e:
            print(f"⚠️  讀取失敗，略過檔案：{json_file.name}，原因：{e}")

    print(f"\n✅ 掃描資料夾：{data_dir}")
    print(f"   讀取 {loaded_files} / {len(json_files)} 個 JSON 檔，共 {len(all_records)} 筆記錄")

    if not all_records:
        print("❌ data 資料夾中沒有可用記錄！")
        raise SystemExit(1)

    return all_records

def filter_records_by_time(records: list[dict], start_time: str = None, end_time: str = None) -> list[dict]:
    """根據時間範圍過濾記錄。
    
    Args:
        records: 所有記錄列表
        start_time: 開始時間字串（格式：YYYY/MM/DD HH:MM 或 YYYY-MM-DD HH:MM）
        end_time: 結束時間字串（同上格式）
    
    Returns:
        過濾後的記錄列表
    """
    if not start_time and not end_time:
        print(f"ℹ️  未指定時間範圍，將分析全部 {len(records)} 筆記錄")
        return records
    
    try:
        start_dt = parse_timestamp(start_time) if start_time else datetime.min
        end_dt   = parse_timestamp(end_time) if end_time else datetime.max
    except ValueError as e:
        print(f"❌ 時間解析失敗：{e}")
        raise SystemExit(1)
    
    filtered = []
    for rec in records:
        try:
            rec_dt = parse_timestamp(rec.get("Timestamp", ""))
            if start_dt <= rec_dt <= end_dt:
                filtered.append(rec)
        except ValueError:
            continue
    
    print(f"✅ 時間範圍過濾：{start_time or '開始'} → {end_time or '結束'}")
    print(f"   找到 {len(filtered)} / {len(records)} 筆符合記錄")
    
    if not filtered:
        print("❌ 未找到符合時間範圍的記錄！")
        raise SystemExit(1)
    
    return filtered


def get_command_args() -> argparse.Namespace:
    """解析命令行參數。"""
    parser = argparse.ArgumentParser(
        description="🏥 長者行動能力分析系統 - 根據時間範圍生成健康報告",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
        使用範例：
        # 分析預設 move.json 的全部記錄
        python Project1.py

        # 依時間範圍掃描 data/samples/ 內所有 JSON
        python Project1.py --start "2025/03/10 08:20" --end "2025/03/10 09:10"

        # 依時間範圍掃描 data/samples/，從指定時間開始到最後
        python Project1.py --start "2025/03/10 08:20"

        # 測試指定單一 JSON 檔案（相對路徑找不到時會到 data/samples/ 找）
        python Project1.py --input 01.json

        # 註：報告只印在終端機，沒有寫檔選項

        支援的時間格式：
        - YYYY/MM/DD HH:MM
        - YYYY-MM-DD HH:MM
        - YYYY/MM/DD HH:MM AM/PM
        - YYYY-MM-DD HH:MM AM/PM
                """,
    )

    parser.add_argument(
        "--input",
        type=str,
        default=None,
        help="輸入 JSON 檔案路徑（預設：move.json）",
    )
    
    parser.add_argument(
        "--start",
        type=str,
        default=None,
        help="開始時間（格式：YYYY/MM/DD HH:MM）",
    )
    parser.add_argument(
        "--end",
        type=str,
        default=None,
        help="結束時間（格式：YYYY/MM/DD HH:MM）",
    )
    parser.add_argument(
        "--no-db",
        action="store_true",
        help="跳過 Neo4j 資料庫查詢（用於測試）",
    )
    
    return parser.parse_args()



# ============================================================
# v8 overrides：彈性健康網頁搜尋 + 外部卡片精簡
# ============================================================
# 修正重點：
# 1) 外部搜尋不再只限醫院/復健科網頁；只要是醫療、健康、復健、物理治療、職能治療相關且內容貼合動作即可。
# 2) 仍會避開「看起來專業但和動作無關」的頁面，例如抬手卻搜到頸椎神經根病。
# 3) 外部-only 建議卡拿掉「每次做多少 / 每週目標」，避免一堆目前資料不足。
# 4) KG 補欄位仍尊重 KG 類別；外部搜尋只補動作、做法、注意事項與參考資料。

STRICT_PROFESSIONAL_SOURCES = os.getenv("STRICT_PROFESSIONAL_SOURCES", "0") == "1"
MIN_SOURCE_PRIORITY = float(os.getenv("MIN_SOURCE_PRIORITY", "4.5"))
SEARCH_VARIANT_LIMIT = int(os.getenv("SEARCH_VARIANT_LIMIT", "12"))

GENERAL_HEALTH_FALLBACK_DOMAINS = list(dict.fromkeys(GENERAL_HEALTH_FALLBACK_DOMAINS + [
    # 台灣/中文健康衛教類，內容相關時可作為備援
    "commonhealth.com.tw",   # 康健
    "heho.com.tw",           # Heho 健康
    "careonline.com.tw",     # 照護線上
    "edh.tw",                # 早安健康
    "helloyishi.com.tw",     # Hello 醫師
    "h2u.ai",                # 健康資訊
]))

GROUP_ACTION_KEYWORDS_ZH = {
    "upper_trunk": ["抬手", "上肢", "手臂", "肩關節", "肩膀", "關節活動度", "肩部運動", "軀幹"],
    "gait_direction": ["步態", "行走", "走路", "步行", "轉身", "側步", "側向", "前後", "動態平衡", "平衡"],
    "single_leg_balance": ["單腳", "平衡", "跌倒", "站立", "單腳站"],
    "sit_to_stand": ["坐站", "坐下", "站起", "起立", "椅子", "下肢", "肌力"],
    "jump_landing": ["跳躍", "落地", "下肢", "平衡", "低衝擊", "動態平衡"],
    "bend_squat": ["彎腰", "彎身", "下蹲", "深蹲", "取物", "軀幹", "下肢"],
    "hand_function": ["抓握", "手部", "手指", "精細動作", "日常生活", "職能治療", "上肢"],
    "endurance": ["耐力", "心肺", "原地踏步", "步行運動", "活動耐受", "有氧"],
    "functional_movement": ["功能性動作", "動作控制", "姿勢", "平衡", "復健", "運動"],
}

GROUP_ACTION_KEYWORDS_EN = {
    "upper_trunk": ["shoulder", "upper limb", "arm", "range of motion", "trunk", "raising arm"],
    "gait_direction": ["gait", "walking", "walk", "turning", "lateral", "balance", "step"],
    "single_leg_balance": ["single leg", "one leg", "balance", "fall", "standing"],
    "sit_to_stand": ["sit to stand", "chair", "lower limb", "stand up", "leg strength"],
    "jump_landing": ["jump", "landing", "lower limb", "balance", "low impact"],
    "bend_squat": ["squat", "bending", "bend", "trunk", "lower limb"],
    "hand_function": ["hand", "grasp", "fine motor", "activities of daily living", "occupational therapy"],
    "endurance": ["endurance", "aerobic", "walking exercise", "activity tolerance"],
    "functional_movement": ["functional movement", "motor control", "rehabilitation", "exercise"],
}

USELESS_PAGE_HINTS = [
    "科部介紹", "科室介紹", "服務項目", "團隊介紹", "醫師介紹", "門診時間", "掛號", "交通資訊",
    "全球資訊網", "本科介紹", "歷史沿革", "組織架構"
]


def ext_source_priority(title: str, url: str, snippet: str = "") -> int:
    """
    v8 來源排序：內容相關 > 來源形式。
    醫療/健康/復健/物理治療/職能治療頁都可以；論文預設排除；純科部介紹降權。
    """
    u = str(url).lower()
    text_all = f"{title} {url} {snippet}".lower()

    if ext_is_english_or_empty_source(title, url, snippet):
        return 0
    title_snippet = f"{title} {snippet}"

    if ext_is_research_source(title, url, snippet):
        return 1
    if ext_is_blocked_source(title, url, snippet):
        return 0

    # 明顯只是科部介紹或掛號頁，除非同時包含具體運動/訓練內容，否則降權。
    generic_intro = any(k in title_snippet for k in USELESS_PAGE_HINTS)
    concrete_action = any(k in title_snippet for k in [
        "訓練", "運動", "衛教", "平衡", "步態", "行走", "坐站", "肌力", "關節活動度", "上肢", "下肢", "肩關節", "跌倒", "伸展"
    ])
    if generic_intro and not concrete_action:
        return 1

    # 中文醫療/政府/健康教育站
    if any(d in u for d in CHINESE_PRACTICAL_DOMAINS):
        return 5
    if any(d in u for d in GENERAL_HEALTH_FALLBACK_DOMAINS):
        return 4

    # 文字本身顯示是醫療/復健/健康衛教內容
    if any(h in text_all for h in ["復健", "物理治療", "職能治療", "衛教", "醫師", "治療師", "健康", "運動", "訓練"]):
        return 4

    top_domains = [
        "choosept.com", "apta.org", "aota.org", "nhs.uk", "nia.nih.gov", "cdc.gov",
        "medlineplus.gov", "physio-pedia.com", "hss.edu", "mskcc.org", "mayoclinic.org",
        "clevelandclinic.org", "betterhealth.vic.gov.au", "myhealth.alberta.ca", "healthlinkbc.ca",
        "kaiserpermanente.org",
    ]
    if any(d in u for d in top_domains):
        return 4
    if any(h in u for h in REHAB_CLINIC_DOMAIN_HINTS) or any(h in text_all for h in [
        "physical therapy", "occupational therapy", "physiotherapy", "rehabilitation", "patient education", "exercise"
    ]):
        return 3
    if any(d in u for d in [".gov", ".edu", ".gov.tw", ".edu.tw"]):
        return 3
    return 0


def ext_build_group_query_plan(group_key: str, motion: str, metric: Optional[str] = None, speed: Optional[str] = None) -> Dict:
    """
    v8：中文健康/醫療/復健內容優先，但不硬綁醫院站。
    先用更貼近動作的中文 query，找不到再退到英文 PT/OT/健康頁。
    """
    profile = MOVEMENT_GROUP_PROFILES.get(group_key, MOVEMENT_GROUP_PROFILES["functional_movement"])
    zh_terms = profile.get("query_terms_zh") or GROUP_CHINESE_QUERY_TERMS.get(group_key, [])
    en_terms = profile.get("query_terms", [])

    # 避免 query 被「醫院 復健科」帶去科部介紹頁；改用健康/衛教/運動/復健建議等內容詞。
    zh_action = " ".join(ext_unique_keep_order(zh_terms))
    zh_health = " ".join(ext_unique_keep_order(zh_terms + ["健康", "衛教", "復健運動", "動作訓練", "物理治療", "職能治療"]))
    zh_exercise = " ".join(ext_unique_keep_order(zh_terms + ["運動教學", "居家運動", "復健建議", "注意事項"]))
    en_base = " ".join(ext_unique_keep_order(en_terms + [
        "older adults", "physical therapy", "occupational therapy", "rehabilitation", "exercise", "patient education"
    ]))

    chinese_sites = [
        # 內容型健康/衛教站優先，不只醫院
        "health99.hpa.gov.tw", "hpa.gov.tw", "mohw.gov.tw",
        "commonhealth.com.tw", "heho.com.tw", "careonline.com.tw", "helloyishi.com.tw",
        # 醫療院所仍作為備援
        "vghtpe.gov.tw", "vghtc.gov.tw", "cgmh.org.tw", "ntuh.gov.tw", "kmuh.org.tw",
    ]
    english_sites = [
        "choosept.com", "nhs.uk", "physio-pedia.com", "apta.org", "aota.org",
        "mayoclinic.org", "clevelandclinic.org", "betterhealth.vic.gov.au", "nia.nih.gov",
        "verywellhealth.com", "healthline.com",
    ]

    query_variants = []
    # 1. 不限制網站的中文 query，讓搜尋引擎先找真正相關的內容。
    query_variants.extend([zh_action, zh_health, zh_exercise])
    # 2. 中文健康/醫療/復健站備援。
    query_variants.extend([f"{zh_action} site:{site}" for site in chinese_sites])
    # 3. 英文專業與健康站備援。
    query_variants.append(en_base)
    query_variants.extend([f"{en_base} site:{site}" for site in english_sites])

    query_variants = [q for q in ext_unique_keep_order(query_variants) if q]
    query_variants = query_variants[:max(5, SEARCH_VARIANT_LIMIT)]

    reject_terms = ext_unique_keep_order(list(NON_MOTION_TOPIC_HINTS) + profile.get("reject_extra", []))
    if group_key == "upper_trunk":
        reject_terms = ext_unique_keep_order(reject_terms + SYMPTOM_DISEASE_TOPIC_HINTS)

    return {
        "should_search": True,
        "query": zh_health,
        "query_variants": query_variants,
        "reason": "依連續動作子項目產生中文健康/復健外部搜尋 query",
        "required_terms": ext_unique_keep_order(profile.get("required_any", []) + GROUP_ACTION_KEYWORDS_ZH.get(group_key, []) + GROUP_ACTION_KEYWORDS_EN.get(group_key, [])),
        "required_any": ext_unique_keep_order(profile.get("required_any", []) + GROUP_ACTION_KEYWORDS_ZH.get(group_key, []) + GROUP_ACTION_KEYWORDS_EN.get(group_key, [])),
        "action_any": ext_unique_keep_order(profile.get("query_terms", []) + profile.get("required_any", []) + GROUP_ACTION_KEYWORDS_ZH.get(group_key, []) + GROUP_ACTION_KEYWORDS_EN.get(group_key, [])),
        "reject_terms": reject_terms,
        "movement_components": "、".join(profile["components_zh"]),
        "topic": profile["title"],
        "group_key": group_key,
        "secondary_groups": [],
    }


def ext_is_relevant_evidence(item: Dict, plan: Dict) -> bool:
    """
    v8：放寬來源類型，但加強「內容是否真的跟該動作有關」。
    健康網頁可以採用；科部介紹/病症頁/論文頁不會因為來源看似專業就通過。
    """
    title = item.get("title", "")
    url = item.get("url", "")
    snippet = item.get("snippet", "")
    text = f"{title} {snippet} {url}".lower()
    text_zh = f"{title} {snippet}"

    if ext_is_blocked_source(title, url, snippet):
        return False
    if ext_is_research_source(title, url, snippet) and not ALLOW_RESEARCH_EVIDENCE:
        return False

    group_key = plan.get("group_key", "functional_movement")
    reject_terms = set(plan.get("reject_terms", []) or []) | set(NON_MOTION_TOPIC_HINTS)
    reject_terms |= set(MOVEMENT_GROUP_PROFILES.get(group_key, {}).get("reject_extra", []))
    if any(term.lower() in text for term in reject_terms):
        return False

    zh_group_terms = GROUP_ACTION_KEYWORDS_ZH.get(group_key, [])
    en_group_terms = GROUP_ACTION_KEYWORDS_EN.get(group_key, [])
    group_hit = any(k.lower() in text for k in en_group_terms) or any(k in text_zh for k in zh_group_terms)

    rehab_hit = any(k in text for k in MOTION_REHAB_HINTS) or any(k in text_zh for k in CHINESE_REHAB_HINTS)
    health_hit = any(k in text_zh for k in ["健康", "衛教", "運動", "訓練", "復健", "物理治療", "職能治療", "治療師"])

    # 太泛的科部介紹：沒有命中該動作核心詞就不要。
    generic_intro = any(k in text_zh for k in USELESS_PAGE_HINTS)
    if generic_intro and not group_hit:
        return False

    priority = ext_source_priority(title, url, snippet)
    if priority <= 0:
        return False

    # 放寬來源，但不放寬內容：必須命中該 group 的動作詞，且至少有健康/復健/運動語境。
    if group_hit and (rehab_hit or health_hit):
        return True

    # 如果是高可信健康/醫療來源，且有清楚 group hit，可以接受。
    if priority >= 4 and group_hit:
        return True

    return False

def ext_page_text_relevance_score(text: str, plan: dict) -> int:
    text = str(text).lower()
    score = 0

    for term in plan.get("action_any", []):
        if str(term).lower() in text:
            score += 2

    positive_terms = [
        "復健", "復健科", "物理治療", "職能治療", "衛教",
        "訓練", "運動", "肌力", "平衡", "跌倒",
        "長者", "老人", "上肢", "下肢", "步態", "關節活動度",
        "rehabilitation", "physical therapy", "occupational therapy",
        "exercise", "balance", "fall", "strength", "older adults"
    ]

    for term in positive_terms:
        if term.lower() in text:
            score += 1

    for term in plan.get("reject_terms", []):
        if str(term).lower() in text:
            score -= 3

    return score


def ext_search_one_group(group_key: str, motion: str, metric: Optional[str], speed: Optional[str], max_results: int, max_pages: int) -> dict:
    """v8：每個子動作群組各自搜尋，健康網頁可用；先找內容相關，再看來源層級。"""
    plan = ext_build_group_query_plan(group_key, motion, metric=metric, speed=speed)
    query_attempts = []
    evidence_items = []

    for q in plan.get("query_variants") or [plan["query"]]:
        q = ext_normalize_text(q)
        if not q or q in query_attempts:
            continue
        query_attempts.append(q)
        search_results = ext_web_search(query=q, max_results=max(max_results, 8))
        for item in search_results:
            if not ext_is_relevant_evidence(item, plan):
                continue
            # STRICT 模式才硬要求高來源分；預設不硬卡醫院/專業站。
            if STRICT_PROFESSIONAL_SOURCES and ext_source_priority(item.get("title", ""), item.get("url", ""), item.get("snippet", "")) < 3:
                continue
            page_text = ""
            if max_pages > 0 and len(evidence_items) < max_pages:
                page_text = ext_fetch_page_text(item["url"], timeout=5, max_chars=3000)
            if page_text:
                page_score = ext_page_text_relevance_score(page_text, plan)
                if page_score < 3:
                    continue
            evidence_items.append({
                "title": item["title"],
                "url": item["url"],
                "snippet": item["snippet"],
                "page_text": page_text,
                "source_priority": ext_source_priority(item.get("title", ""), item.get("url", ""), item.get("snippet", "")),
                "group_key": group_key,
            })
            break
        if evidence_items:
            break

    return {
        "group_key": group_key,
        "topic": MOVEMENT_GROUP_PROFILES.get(group_key, MOVEMENT_GROUP_PROFILES["functional_movement"])["title"],
        "movement_components": "、".join(MOVEMENT_GROUP_PROFILES.get(group_key, MOVEMENT_GROUP_PROFILES["functional_movement"])["components_zh"]),
        "query": plan["query"],
        "query_attempts": query_attempts,
        "evidence": evidence_items[:1],
        "skip_reason": "" if evidence_items else "外部搜尋結果未通過健康/復健內容相關性檢查，未採用外部 evidence。",
    }


def build_external_template_card(evidence_bundle: dict, class_name: str, score_val: int) -> str:
    """
    v8 外部補充卡：連續動作分項顯示；外部-only 不輸出每次做多少/每週目標。
    """
    sub_actions = evidence_bundle.get("sub_actions", []) or []
    sub_bundles = evidence_bundle.get("sub_bundles", []) or []
    group_keys = evidence_bundle.get("group_keys") or [evidence_bundle.get("group_key", "functional_movement")]

    lines = []
    if sub_actions and len(sub_actions) > 1:
        lines.append("連續動作拆解：")
        for idx, sa in enumerate(sub_actions, start=1):
            seg = sa.get("segment", "")
            title = sa.get("group_title", "功能性動作控制")
            lines.append(f"{idx}. {seg} → {title}")
        lines.append("")

    bundle_by_key = {b.get("group_key"): b for b in sub_bundles}
    used = set()
    for key in group_keys:
        if key in used:
            continue
        used.add(key)
        profile = MOVEMENT_GROUP_PROFILES.get(key, MOVEMENT_GROUP_PROFILES["functional_movement"])
        b = bundle_by_key.get(key, {})
        evidence = b.get("evidence", []) or []
        context_text = "外部資料不足，未能擷取明確的動作難度或復健建議重點。"
        source_text = "未取得可用外部資料"
        if evidence:
            first = evidence[0]
            source_titles = []
            seen_source_urls = set()
            for src_item in evidence[:3]:
                src_url = str(src_item.get("url", "")).strip()
                if src_url and src_url in seen_source_urls:
                    continue
                if src_url:
                    seen_source_urls.add(src_url)
                title = str(src_item.get("title", "外部資料")).strip() or "外部資料"
                source_titles.append(title[:60])
            source_text = "；".join(source_titles) if source_titles else first.get("title", "外部資料")[:60]
            context_text = first.get("raptor_context") or first.get("snippet") or first.get("page_text") or ""
            context_text = clean_external_context_text(context_text)[:420] or context_text

        lines.extend([
            f"能力項目：{profile['title']}",
            f"判斷說明：{profile['status']}",
            f"動作組成：{'、'.join(profile['components_zh'])}",
            f"外部資料重點：{context_text}",
            f"動作難度觀察：{profile['how']}",
            f"復健動作建議：{profile['action']}",
            f"注意事項：{profile['caution']}",
            f"資料來源類型：外部搜尋補強；參考來源：{source_text}",
            "信心程度：低至中等，需要人工複核",
            "",
        ])

    return "\n".join(lines).strip()


def build_external_field_supplements(motion: str, class_name: str, score_val: int, evidence_bundle: dict) -> dict:
    """
    v8 KG 缺欄位補足：尊重 KG 類別與 motion；外部 evidence 只補參考資料。
    """
    evidence_bundle = evidence_bundle or {}
    group_key = ext_group_for_kg_supplement(motion, class_name, evidence_bundle)
    profile = MOVEMENT_GROUP_PROFILES.get(group_key, MOVEMENT_GROUP_PROFILES["functional_movement"])
    if group_key == "functional_movement" and not evidence_bundle.get("evidence"):
        return {}

    supplements = {
        "action": profile["action"],
        "how_to": profile["how"],
        "caution": profile["caution"],
        "group_key": group_key,
    }

    reference_urls = []
    for b in evidence_bundle.get("sub_bundles", []) or []:
        if b.get("group_key") != group_key:
            continue
        for item in b.get("evidence", [])[:3]:
            url = str(item.get("url", "")).strip()
            if url and url not in reference_urls:
                reference_urls.append(url)
    if not reference_urls:
        for item in evidence_bundle.get("evidence", [])[:3]:
            url = str(item.get("url", "")).strip()
            if url and url not in reference_urls:
                reference_urls.append(url)
    if reference_urls:
        supplements["reference_urls"] = reference_urls
    return supplements



# ============================================================
# v9 overrides：嚴格過濾參考網頁（非商品、非輔具、非 PDF、非學術）
# ============================================================
# 修正重點：
# 1) 參考來源必須是一般健康/醫療/復健/物理治療/職能治療「內容頁」，不再只卡醫院，但也不能是商品頁。
# 2) 排除輔具、器材販售、購物、價格、租借、商品介紹、步態訓練器等頁面。
# 3) 排除 PDF、研討會、論文、期刊、學會投稿、會議摘要、研究型頁面。
# 4) 網頁大標題必須跟該能力項目有明確關聯；只在 snippet 有關但標題無關時，不採用。
# 5) 外部-only 卡片維持不輸出「每次做多少 / 每週目標」。KG 卡若本身有數值仍保留。

STRICT_TITLE_MATCH = os.getenv("STRICT_TITLE_MATCH", "1") == "1"
REJECT_PDF_SOURCES = os.getenv("REJECT_PDF_SOURCES", "1") == "1"
REJECT_COMMERCE_SOURCES = os.getenv("REJECT_COMMERCE_SOURCES", "1") == "1"
REJECT_ACADEMIC_SOURCES = os.getenv("REJECT_ACADEMIC_SOURCES", "1") == "1"

# 商品、輔具、器材販售、租借、價格頁：使用者報告不可採用。
COMMERCE_AND_ASSISTIVE_HINTS_ZH = [
    "輔具", "器材", "設備", "訓練器", "步態訓練器", "助行器", "助步器", "拐杖", "輪椅",
    "購買", "販售", "銷售", "商品", "產品", "價格", "報價", "特價", "優惠", "購物", "商城",
    "購物車", "加入購物車", "下單", "訂購", "租借", "租賃", "經銷", "代理", "型號", "規格",
    "品牌", "目錄", "展示", "採購", "輔具補助", "補助申請", "器械", "醫材", "照護床",
]
COMMERCE_AND_ASSISTIVE_HINTS_EN = [
    "assistive device", "mobility aid", "walking aid", "walker", "rollator", "cane", "wheelchair",
    "gait trainer", "training device", "equipment", "device", "product", "shop", "shopping", "cart",
    "buy", "sale", "price", "pricing", "rent", "rental", "supplier", "catalog", "model", "specification",
    "brand", "store", "checkout", "order now", "add to cart", "medical device",
]

# PDF / 檔案 / 學術會議 / 論文頁：使用者報告不可採用。
PDF_HINTS = [".pdf", "[pdf]", "pdf", "downloadfile", "upload/hot", "uploadfile", "uploads/", "attachment"]
ACADEMIC_EVENT_HINTS_ZH = [
    "研討會", "學術", "論文", "期刊", "研究論文", "學位論文", "碩士論文", "博士論文", "投稿", "徵稿",
    "摘要", "海報", "壁報", "會議", "年會", "論壇", "工作坊", "講座", "課程報名", "研習", "教育訓練",
    "台灣復健醫學會", "醫學會", "學會", "研究計畫", "臨床研究", "文獻", "系統性回顧", "統合分析",
]
ACADEMIC_EVENT_HINTS_EN = [
    "conference", "symposium", "congress", "annual meeting", "abstract", "poster", "proceedings",
    "journal", "publication", "paper", "thesis", "dissertation", "research article", "systematic review",
    "meta-analysis", "clinical trial", "study protocol", "call for papers", "workshop", "webinar", "seminar",
]

# 更細的「標題必須貼題」關鍵字。這裡不是固定句子規則，而是每個能力大類的標題相關性檢查。
TITLE_KEYWORDS_ZH = {
    "upper_trunk": ["抬手", "舉手", "上肢", "手臂", "肩", "肩關節", "肩膀", "關節活動", "活動度", "伸展", "軀幹", "手部", "職能治療"],
    "gait_direction": ["步態", "行走", "走路", "步行", "轉身", "側步", "側向", "平衡", "跌倒", "動態平衡", "步伐", "長者運動"],
    "single_leg_balance": ["單腳", "單腳站", "平衡", "跌倒", "站立", "不倒", "下肢穩定", "長者平衡"],
    "sit_to_stand": ["坐站", "坐姿", "坐著", "椅子", "起立", "站起", "下肢肌力", "肌力", "長者肌力", "坐到站"],
    "jump_landing": ["跳躍", "落地", "低衝擊", "下肢", "膝", "踝", "動態平衡", "肌力", "穩定"],
    "bend_squat": ["彎腰", "彎身", "下蹲", "深蹲", "取物", "髖", "膝", "下肢", "軀幹", "肌力", "平衡"],
    "hand_function": ["抓握", "手部", "手指", "精細動作", "日常生活", "職能治療", "上肢", "手功能"],
    "endurance": ["耐力", "心肺", "有氧", "原地踏步", "步行運動", "活動耐受", "運動耐受", "體能"],
    "functional_movement": ["功能性動作", "動作控制", "姿勢", "平衡", "復健運動", "動作訓練", "長者運動"],
}
TITLE_KEYWORDS_EN = {
    "upper_trunk": ["shoulder", "upper limb", "arm", "range of motion", "trunk", "raising arm", "occupational therapy"],
    "gait_direction": ["gait", "walking", "turning", "lateral", "balance", "fall prevention", "step"],
    "single_leg_balance": ["single leg", "one leg", "balance", "fall", "standing"],
    "sit_to_stand": ["sit to stand", "chair rise", "chair exercise", "lower limb", "leg strength"],
    "jump_landing": ["jump", "landing", "lower limb", "low impact", "balance"],
    "bend_squat": ["squat", "bending", "bend", "trunk", "lower limb"],
    "hand_function": ["hand", "grasp", "fine motor", "activities of daily living", "occupational therapy"],
    "endurance": ["endurance", "aerobic", "walking exercise", "activity tolerance"],
    "functional_movement": ["functional movement", "motor control", "rehabilitation exercise"],
}

# 將商品/學術/PDF 類詞加入整體 blocked hints，讓 ext_is_blocked_source 也能擋掉。
BLOCKED_SOURCE_HINTS = list(dict.fromkeys(
    list(BLOCKED_SOURCE_HINTS)
    + COMMERCE_AND_ASSISTIVE_HINTS_ZH
    + COMMERCE_AND_ASSISTIVE_HINTS_EN
    + PDF_HINTS
    + ACADEMIC_EVENT_HINTS_ZH
    + ACADEMIC_EVENT_HINTS_EN
))


def _v9_text_blob(title: str, url: str, snippet: str = "") -> tuple[str, str]:
    zh = f"{title} {snippet} {url}"
    low = zh.lower()
    return zh, low


def ext_count_chinese_chars(text: str) -> int:
    """計算中文字數，用來排除英文頁或無內容頁。"""
    return len(re.findall(r"[\u4e00-\u9fff]", str(text or "")))


def ext_is_english_or_empty_source(title: str, url: str, snippet: str = "") -> bool:
    """排除英文頁、英文首頁、空內容搜尋結果。"""
    u = str(url or "").lower()
    raw = f"{title} {snippet}"

    bad_url_hints = [
        "/english", "/en/", "?lang=en", "&lang=en", "language=en",
        "/eng/", "/en-us/", "/en_us/", "/english/"
    ]
    if any(h in u for h in bad_url_hints):
        return True

    if CHINESE_ONLY_EVIDENCE and ext_count_chinese_chars(raw) < MIN_CHINESE_CHARS:
        return True

    return False


def ext_is_usable_chinese_page_text(page_text: str, plan: Optional[dict] = None) -> bool:
    """確認抓下來的網頁內文真的有中文、有內容，且不是首頁/掛號/科部介紹。"""
    text = ext_normalize_text(page_text or "")
    if not text:
        return False

    if CHINESE_ONLY_EVIDENCE and ext_count_chinese_chars(text) < MIN_PAGE_TEXT_CHINESE_CHARS:
        return False

    visible = re.sub(r"\s+", "", text)
    if visible:
        zh_ratio = ext_count_chinese_chars(visible) / max(len(visible), 1)
        if CHINESE_ONLY_EVIDENCE and zh_ratio < 0.08:
            return False

    useless_hits = [
        "latest news", "outpatient schedule", "network registration", "voice registration",
        "visiting progress", "facebook fan page", "dean's mailbox",
        "科部介紹", "科室介紹", "服務項目", "團隊介紹", "醫師介紹",
        "門診時間", "掛號", "交通資訊", "全球資訊網", "本科介紹", "歷史沿革", "組織架構"
    ]
    if any(h.lower() in text.lower() for h in useless_hits):
        concrete_terms = ["訓練", "運動", "復健", "物理治療", "職能治療", "肌力", "平衡", "步態", "坐站", "抬手", "下蹲", "關節活動度"]
        if not any(t in text for t in concrete_terms):
            return False

    if plan:
        group_key = plan.get("group_key", "")
        zh_group_terms = TITLE_KEYWORDS_ZH.get(group_key, []) + GROUP_ACTION_KEYWORDS_ZH.get(group_key, [])
        if zh_group_terms and not any(t in text for t in zh_group_terms):
            return False

    return True


def ext_is_pdf_source(title: str, url: str, snippet: str = "") -> bool:
    zh, low = _v9_text_blob(title, url, snippet)
    if not REJECT_PDF_SOURCES:
        return False
    u = str(url).lower().split("?")[0]
    if u.endswith(".pdf"):
        return True
    return any(h.lower() in low for h in PDF_HINTS)


def ext_is_commerce_or_assistive_source(title: str, url: str, snippet: str = "") -> bool:
    if not REJECT_COMMERCE_SOURCES:
        return False
    zh, low = _v9_text_blob(title, url, snippet)
    return any(h in zh for h in COMMERCE_AND_ASSISTIVE_HINTS_ZH) or any(h in low for h in COMMERCE_AND_ASSISTIVE_HINTS_EN)


def ext_is_academic_or_event_source(title: str, url: str, snippet: str = "") -> bool:
    if not REJECT_ACADEMIC_SOURCES:
        return False
    zh, low = _v9_text_blob(title, url, snippet)
    # 原本 research 判斷保留，再補中文學術/研討會/會議詞
    if ext_is_research_source(title, url, snippet):
        return True
    return any(h in zh for h in ACADEMIC_EVENT_HINTS_ZH) or any(h in low for h in ACADEMIC_EVENT_HINTS_EN)


def ext_title_matches_group(title: str, group_key: str) -> bool:
    """網頁大標題必須跟能力項目相關。"""
    if not STRICT_TITLE_MATCH:
        return True
    title = str(title or "").strip()
    if not title:
        return False
    low = title.lower()
    zh_terms = TITLE_KEYWORDS_ZH.get(group_key, []) + GROUP_ACTION_KEYWORDS_ZH.get(group_key, [])
    en_terms = TITLE_KEYWORDS_EN.get(group_key, []) + GROUP_ACTION_KEYWORDS_EN.get(group_key, [])
    return any(t in title for t in zh_terms) or any(t.lower() in low for t in en_terms)


def ext_source_priority(title: str, url: str, snippet: str = "") -> int:
    """
    v9 來源排序：先過濾不能用的來源，再排序。
    允許健康網站，但商品/輔具/PDF/學術頁直接淘汰。
    """
    if ext_is_pdf_source(title, url, snippet):
        return 0
    if ext_is_commerce_or_assistive_source(title, url, snippet):
        return 0
    if ext_is_academic_or_event_source(title, url, snippet):
        return 0

    u = str(url).lower()
    zh, text_all = _v9_text_blob(title, url, snippet)
    title_snippet = f"{title} {snippet}"

    generic_intro = any(k in title_snippet for k in USELESS_PAGE_HINTS)
    concrete_action = any(k in title_snippet for k in [
        "訓練", "運動", "衛教", "平衡", "步態", "行走", "坐站", "肌力", "關節活動度",
        "上肢", "下肢", "肩關節", "跌倒", "伸展", "抬手", "單腳", "深蹲", "彎腰",
    ])
    if generic_intro and not concrete_action:
        return 0

    # 政府/公共健康教育/醫院衛教內容
    if any(d in u for d in ["hpa.gov.tw", "health99", "mohw.gov.tw", "gov.tw", "nhs.uk", "cdc.gov", "nia.nih.gov", "medlineplus.gov"]):
        return 5
    # 中文健康網，只要標題貼題且內容不是商業/學術，即可採用
    if any(d in u for d in GENERAL_HEALTH_FALLBACK_DOMAINS + ["commonhealth.com.tw", "heho.com.tw", "careonline.com.tw", "edh.tw", "health.ltn.com.tw", "helloyishi.com.tw"]):
        return 4
    # 醫院、復健、PT/OT 專業站
    if any(d in u for d in CHINESE_PRACTICAL_DOMAINS):
        return 4
    if any(h in u for h in REHAB_CLINIC_DOMAIN_HINTS):
        return 3
    if any(h in text_all for h in ["復健", "物理治療", "職能治療", "衛教", "治療師", "健康", "運動", "訓練"]):
        return 3

    top_domains = [
        "choosept.com", "apta.org", "aota.org", "nhs.uk", "nia.nih.gov", "cdc.gov",
        "medlineplus.gov", "physio-pedia.com", "mayoclinic.org", "clevelandclinic.org",
        "betterhealth.vic.gov.au", "myhealth.alberta.ca", "healthlinkbc.ca",
        "verywellhealth.com", "healthline.com",
    ]
    if any(d in u for d in top_domains):
        return 3
    return 0



def ext_evidence_total_score(item: dict, plan: dict, page_text: str = "") -> float:
    title = item.get("title", "")
    url = item.get("url", "")
    snippet = item.get("snippet", "")
    text = f"{title} {snippet} {page_text} {url}".lower()

    if ext_is_english_or_empty_source(title, url, snippet):
        return -999.0

    if page_text and not ext_is_usable_chinese_page_text(page_text, plan):
        return -999.0

    # 權重設計：來源可信度仍保留，但不讓 5/6 分來源自動壓過內容貼合度。
    # 目前目標是「找最能支撐復健/動作建議的 evidence」，所以提高內容與教學性比重。
    source_score = float(ext_source_priority(title, url, snippet))
    action_score = 0.0
    teaching_score = 0.0
    risk_score = 0.0

    teaching_terms = [
        "怎麼做", "做法", "步驟", "訓練", "運動", "動作",
        "復健", "物理治療", "職能治療", "衛教",
        "exercise", "training", "how to", "instructions"
    ]

    for term in teaching_terms:
        if term.lower() in text:
            teaching_score += 0.5

    for term in plan.get("action_any", []):
        if str(term).lower() in text:
            action_score += 0.8

    for term in plan.get("reject_terms", []):
        if str(term).lower() in text:
            risk_score += 2.0

    if any(x in text for x in ["商品", "購買", "價格", "器材", "課程", "廣告"]):
        risk_score += 2.0

    score = (
        source_score * SOURCE_WEIGHT
        + action_score * ACTION_WEIGHT
        + teaching_score * TEACHING_WEIGHT
        - risk_score * RISK_WEIGHT
    )

    return score


def ext_extract_evidence_date(item: dict) -> datetime:
    """
    從 title / snippet / url 嘗試抓文章時間。
    抓不到時間時回傳 datetime.min，排序時會排在較後面。
    只作為 Top3 evidence 的時間排序，不影響原本搜尋與分類流程。
    """
    text = " ".join([
        str(item.get("title", "")),
        str(item.get("snippet", "")),
        str(item.get("url", "")),
        str(item.get("page_text", ""))[:300],
    ])

    patterns = [
        r"(20\d{2})[/-](\d{1,2})[/-](\d{1,2})",
        r"(20\d{2})年\s*(\d{1,2})月\s*(\d{1,2})日",
        r"(20\d{2})(\d{2})(\d{2})",
    ]

    for pat in patterns:
        m = re.search(pat, text)
        if not m:
            continue
        try:
            y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
            return datetime(y, mo, d)
        except Exception:
            continue

    return datetime.min


def ext_sort_evidence_by_time(evidence_items: list[dict]) -> list[dict]:
    """
    Top3 evidence 的最後呈現順序：
    1. 有日期者：新到舊
    2. 無日期或同日期：維持 evidence_total_score / source_priority 輔助排序
    """
    return sorted(
        evidence_items,
        key=lambda x: (
            ext_extract_evidence_date(x),
            float(x.get("evidence_total_score", 0)),
            float(x.get("source_priority", 0)),
        ),
        reverse=True,
    )

def ext_build_group_query_plan(group_key: str, motion: str, metric: Optional[str] = None, speed: Optional[str] = None) -> Dict:
    """
    v9 query：中文健康內容優先，加入排除商品/輔具/PDF/學術的負向詞。
    不硬限制醫院，但要求搜尋結果標題貼合能力項目。
    """
    profile = MOVEMENT_GROUP_PROFILES.get(group_key, MOVEMENT_GROUP_PROFILES["functional_movement"])
    zh_terms = profile.get("query_terms_zh") or GROUP_CHINESE_QUERY_TERMS.get(group_key, [])
    en_terms = profile.get("query_terms", [])

    zh_action = " ".join(ext_unique_keep_order(zh_terms))
    zh_health = " ".join(ext_unique_keep_order(zh_terms + ["健康", "衛教", "復健運動", "動作訓練", "物理治療", "職能治療"]))
    zh_exercise = " ".join(ext_unique_keep_order(zh_terms + ["運動教學", "居家運動", "復健建議", "注意事項", "治療師"]))
    en_base = " ".join(ext_unique_keep_order(en_terms + [
        "older adults", "physical therapy", "occupational therapy", "rehabilitation", "exercise", "patient education"
    ]))

    # 負向詞：避免商品/輔具/PDF/論文/研討會
    neg_zh = " -輔具 -訓練器 -器材 -設備 -產品 -商品 -購買 -價格 -租借 -購物 -PDF -論文 -研討會 -學術 -期刊 -年會"
    neg_en = " -pdf -product -shop -buy -price -equipment -device -walker -wheelchair -conference -journal -paper -pubmed -pmc"

    chinese_health_sites = [
        "hpa.gov.tw", "health99.hpa.gov.tw", "mohw.gov.tw",
        "health.ltn.com.tw", "commonhealth.com.tw", "heho.com.tw", "careonline.com.tw", "edh.tw", "helloyishi.com.tw",
        "vghtpe.gov.tw", "vghtc.gov.tw", "cgmh.org.tw", "ntuh.gov.tw", "kmuh.org.tw",
    ]
    english_sites = [
        "choosept.com", "nhs.uk", "physio-pedia.com", "apta.org", "aota.org",
        "mayoclinic.org", "clevelandclinic.org", "betterhealth.vic.gov.au", "nia.nih.gov",
        "verywellhealth.com", "healthline.com",
    ]

    query_variants = []
    query_variants.extend([zh_health + neg_zh, zh_exercise + neg_zh, zh_action + " 長者 運動 衛教" + neg_zh])
    query_variants.extend([f"{zh_action} site:{site}{neg_zh}" for site in chinese_health_sites])
    query_variants.append(en_base + neg_en)
    query_variants.extend([f"{en_base} site:{site}{neg_en}" for site in english_sites])

    query_variants = [q for q in ext_unique_keep_order(query_variants) if q and len(q.strip()) > 3]
    query_variants = query_variants[:max(8, SEARCH_VARIANT_LIMIT)]

    reject_terms = ext_unique_keep_order(
        list(NON_MOTION_TOPIC_HINTS)
        + profile.get("reject_extra", [])
        + COMMERCE_AND_ASSISTIVE_HINTS_ZH
        + COMMERCE_AND_ASSISTIVE_HINTS_EN
        + PDF_HINTS
        + ACADEMIC_EVENT_HINTS_ZH
        + ACADEMIC_EVENT_HINTS_EN
    )
    if group_key == "upper_trunk":
        reject_terms = ext_unique_keep_order(reject_terms + SYMPTOM_DISEASE_TOPIC_HINTS)

    return {
        "should_search": True,
        "query": zh_health + neg_zh,
        "query_variants": query_variants,
        "reason": "依連續動作子項目產生中文健康/復健外部搜尋 query，並排除商品、輔具、PDF 與學術頁",
        "required_terms": ext_unique_keep_order(profile.get("required_any", []) + GROUP_ACTION_KEYWORDS_ZH.get(group_key, []) + GROUP_ACTION_KEYWORDS_EN.get(group_key, [])),
        "required_any": ext_unique_keep_order(profile.get("required_any", []) + GROUP_ACTION_KEYWORDS_ZH.get(group_key, []) + GROUP_ACTION_KEYWORDS_EN.get(group_key, [])),
        "action_any": ext_unique_keep_order(profile.get("query_terms", []) + profile.get("required_any", []) + GROUP_ACTION_KEYWORDS_ZH.get(group_key, []) + GROUP_ACTION_KEYWORDS_EN.get(group_key, [])),
        "reject_terms": reject_terms,
        "movement_components": "、".join(profile["components_zh"]),
        "topic": profile["title"],
        "group_key": group_key,
        "secondary_groups": [],
    }


def ext_is_relevant_evidence(item: Dict, plan: Dict) -> bool:
    """
    v9：嚴格挑參考網頁。
    必須同時滿足：
    - 非商品/非輔具/非 PDF/非學術
    - title 與 group 相關
    - 內容有健康/復健/運動/治療語境
    """
    title = item.get("title", "")
    url = item.get("url", "")
    snippet = item.get("snippet", "")
    zh, text = _v9_text_blob(title, url, snippet)

    if ext_is_pdf_source(title, url, snippet):
        return False
    if ext_is_commerce_or_assistive_source(title, url, snippet):
        return False
    if ext_is_academic_or_event_source(title, url, snippet):
        return False
    if ext_is_blocked_source(title, url, snippet):
        return False
    if ext_is_research_source(title, url, snippet) and not ALLOW_RESEARCH_EVIDENCE:
        return False

    group_key = plan.get("group_key", "functional_movement")

    if not ext_title_matches_group(title, group_key):
        return False

    reject_terms = set(plan.get("reject_terms", []) or []) | set(NON_MOTION_TOPIC_HINTS)
    reject_terms |= set(MOVEMENT_GROUP_PROFILES.get(group_key, {}).get("reject_extra", []))
    for term in reject_terms:
        if str(term).strip() and str(term).lower() in text:
            return False

    zh_group_terms = TITLE_KEYWORDS_ZH.get(group_key, []) + GROUP_ACTION_KEYWORDS_ZH.get(group_key, [])
    en_group_terms = TITLE_KEYWORDS_EN.get(group_key, []) + GROUP_ACTION_KEYWORDS_EN.get(group_key, [])
    title_group_hit = any(k in str(title) for k in zh_group_terms) or any(k.lower() in str(title).lower() for k in en_group_terms)
    snippet_group_hit = any(k in zh for k in zh_group_terms) or any(k.lower() in text for k in en_group_terms)
    if not (title_group_hit and snippet_group_hit):
        return False

    rehab_or_health_hit = (
        any(k in text for k in MOTION_REHAB_HINTS)
        or any(k in zh for k in CHINESE_REHAB_HINTS)
        or any(k in zh for k in ["健康", "衛教", "運動", "訓練", "復健", "物理治療", "職能治療", "治療師", "長者"])
    )
    if not rehab_or_health_hit:
        return False

    generic_intro = any(k in zh for k in USELESS_PAGE_HINTS)
    if generic_intro and not title_group_hit:
        return False

    return ext_source_priority(title, url, snippet) > 0


def ext_search_one_group(group_key: str, motion: str, metric: Optional[str], speed: Optional[str], max_results: int, max_pages: int) -> dict:
    """v9：每個子動作群組各自搜尋，嚴格過濾商品/輔具/PDF/學術，且標題必須貼題。"""
    plan = ext_build_group_query_plan(group_key, motion, metric=metric, speed=speed)
    query_attempts = []
    candidates = []

    for q in plan.get("query_variants") or [plan["query"]]:
        q = ext_normalize_text(q)
        if not q or q in query_attempts:
            continue
        query_attempts.append(q)
        search_results = ext_web_search(query=q, max_results=max(max_results, 10))
        for item in search_results:
            if not ext_is_relevant_evidence(item, plan):
                continue
            pr = ext_source_priority(item.get("title", ""), item.get("url", ""), item.get("snippet", ""))
            candidates.append({**item, "source_priority": pr, "group_key": group_key})
        # 找到夠好的候選就停，避免越搜越偏
        if any(c.get("source_priority", 0) >= 4 for c in candidates):
            break
        if len(query_attempts) >= max(8, SEARCH_VARIANT_LIMIT):
            break

    candidates = sorted(candidates, key=lambda x: x.get("source_priority", 0), reverse=True)
    evidence_items = []
    for item in candidates[:1]:
        page_text = ""
        if max_pages > 0:
            page_text = ext_fetch_page_text(item["url"], timeout=5, max_chars=3000)
        evidence_items.append({
            "title": item["title"],
            "url": item["url"],
            "snippet": item["snippet"],
            "page_text": page_text,
            "source_priority": item.get("source_priority", 0),
            "group_key": group_key,
        })

    return {
        "group_key": group_key,
        "topic": MOVEMENT_GROUP_PROFILES.get(group_key, MOVEMENT_GROUP_PROFILES["functional_movement"])["title"],
        "movement_components": "、".join(MOVEMENT_GROUP_PROFILES.get(group_key, MOVEMENT_GROUP_PROFILES["functional_movement"])["components_zh"]),
        "query": plan["query"],
        "query_attempts": query_attempts,
        "evidence": evidence_items,
        "skip_reason": "" if evidence_items else "外部搜尋結果未通過標題貼合、非商品、非 PDF、非學術與健康/復健內容相關性檢查，未採用外部 evidence。",
    }


def render_external_sources(evidence_bundle: dict, max_sources: int = 3) -> list[str]:
    """v9：外部來源只列出通過嚴格篩選的網頁。"""
    evidence = evidence_bundle.get("evidence", [])
    if not evidence:
        return ["- **外部來源紀錄**：未取得可用外部來源。"]
    lines = ["- **外部來源紀錄**："]
    seen = set()
    n = 0
    for item in evidence:
        title = item.get("title", "").strip() or "無標題"
        url = item.get("url", "").strip() or "無網址"
        if not url or url in seen:
            continue
        # 雙保險：來源紀錄也不列商品/PDF/學術
        if ext_is_pdf_source(title, url, item.get("snippet", "")):
            continue
        if ext_is_commerce_or_assistive_source(title, url, item.get("snippet", "")):
            continue
        if ext_is_academic_or_event_source(title, url, item.get("snippet", "")):
            continue
        seen.add(url)
        n += 1
        lines.append(f"  {n}. {title} — {url}")
        if n >= max_sources:
            break
    if n == 0:
        return ["- **外部來源紀錄**：未取得可用外部來源。"]
    return lines


# ============================================================
# v10 overrides：保守可信版 RAG / 外部 evidence 防幻覺止血
# ============================================================
# 修正重點：
# 1) functional_movement 不再做外部搜尋，避免泛用 query 搜到凱格爾、商品、疾病頁。
# 2) 喝水、看書、揮手等日常非評估動作，若沒有明確困難/失衡/疼痛，不自動產生正式復健 evidence。
# 3) 模糊動作（subtle/slight/brief 等）若沒有明確動作詞，不外搜。
# 4) shift weight / weight shifting / side-to-side 歸入步態與方向/重心轉移類。
# 5) evidence 進一步排除疾病、性功能、骨盆底肌、中風等不應自動套用到一般長者的文章。

CONSERVATIVE_EXTERNAL_SEARCH = os.getenv("CONSERVATIVE_EXTERNAL_SEARCH", "1") == "1"
DAILY_NON_ASSESSMENT_STRICT = os.getenv("DAILY_NON_ASSESSMENT_STRICT", "1") == "1"
SKIP_FUNCTIONAL_EXTERNAL_SEARCH = os.getenv("SKIP_FUNCTIONAL_EXTERNAL_SEARCH", "1") == "1"

# 這類來源即使看起來像健康/復健，也不能在沒有病史的情況下當作一般動作 evidence。
BAD_EVIDENCE_HINTS_ZH = [
    "凱格爾", "骨盆底肌", "男性更持久", "性能力", "勃起", "攝護腺",
    "中風", "腦中風", "偏癱", "半身不遂", "腦傷", "巴金森", "失智", "認知障礙",
    "癌症", "腫瘤", "術後", "開刀", "手術", "糖尿病足", "神經病變",
    "頸椎神經", "神經根", "椎間盤", "坐骨神經", "手麻", "腳麻",
]
BAD_EVIDENCE_HINTS_EN = [
    "kegel", "pelvic floor", "sexual", "erectile", "prostate",
    "stroke", "hemiplegia", "brain injury", "parkinson", "dementia", "cognitive impairment",
    "cancer", "tumor", "postoperative", "surgery", "diabetic foot", "neuropathy",
    "cervical radiculopathy", "pinched nerve", "sciatica",
]

IMPAIRMENT_HINTS = [
    "difficulty", "difficult", "unable", "cannot", "can't", "unstable", "unsteady",
    "fall", "falls", "pain", "weak", "weakness", "limited", "impaired", "assist", "support",
    "困難", "無法", "不能", "不穩", "失衡", "跌倒", "疼痛", "無力", "受限", "扶持", "協助",
]

CLEAR_ASSESSMENT_ACTION_PATTERNS = [
    r"\bsit\s+down\b", r"\bstand\s+up\b", r"\bsit[-\s]?to[-\s]?stand\b", r"\bchair\s+rise\b",
    r"\bwalk(s|ing|ed)?\b", r"\bstep(s|ping)?\b", r"\bturn(s|ing)?\b",
    r"\bshift(s|ing)?\s+weight\b", r"\bweight\s+shift(s|ing)?\b", r"\bside[-\s]?to[-\s]?side\b",
    r"\bone\s+(foot|leg)\b", r"\bsingle[-\s]?leg\b", r"\bhop(s|ping)?\b",
    r"\bjump(s|ing|ed)?\b", r"\bsquat(s|ting)?\b", r"\bbend(s|ing)?\b", r"\bcrouch\b",
    r"\braise(s|d)?\s+(an?\s+)?arm\b", r"\braise(s|d)?\s+(their\s+)?hand\b",
    r"\breach(es|ing)?\b", r"\bwave(s|ing)?\b", r"\bpoint(s|ing)?\b",
    r"\bgrasp(s|ing)?\b", r"\bhold(s|ing)?\b", r"\bdrink(s|ing)?\b", r"\bread(s|ing)?\b",
]

# 修正容易誤判的 movement keyword。
GROUP_KEYWORDS["gait_direction"] = ext_unique_keep_order(GROUP_KEYWORDS.get("gait_direction", []) + [
    r"\bshift(s|ing)?\s+weight\b",
    r"\bweight\s+shift(s|ing)?\b",
    r"\bside[-\s]?to[-\s]?side\b",
    r"\bchanges?\s+stance\b",
    r"\bpostural\s+control\b",
])

# torso/trunk 單獨出現太容易把「模糊身體晃動」誤判成上肢與軀幹控制；改成較明確的上肢/肩/抬手動作。
GROUP_KEYWORDS["upper_trunk"] = [
    r"\braise(s|d)?\b", r"\barm(s)?\b", r"\bhand(s)?\b",
    r"\breach(es|ing)?\b", r"\bwave(s|ing)?\b", r"\bpoint(s|ing)?\b",
    r"\bshoulder\b", r"\bupper\s+limb\b", r"\bupper\s+extremity\b",
    r"\btrunk\s+control\b", r"\btorso\s+control\b",
]


def ext_has_clear_assessment_action(motion: str) -> bool:
    m = ext_normalize_text(motion).lower()
    return any(re.search(pat, m, flags=re.I) for pat in CLEAR_ASSESSMENT_ACTION_PATTERNS)


def ext_has_impairment_hint(motion: str) -> bool:
    m = ext_normalize_text(motion).lower()
    return any(k.lower() in m for k in IMPAIRMENT_HINTS)


def ext_is_daily_motion_without_difficulty(motion: str) -> bool:
    """喝水/看書/揮手等日常動作，沒有困難或危險線索時，不自動查復健 evidence。"""
    if not DAILY_NON_ASSESSMENT_STRICT:
        return False
    return ext_is_daily_non_assessment_motion(motion) and not ext_has_impairment_hint(motion)


def ext_should_skip_external_by_motion(motion: str, metric: Optional[str] = None) -> tuple[bool, str, str]:
    """集中管理是否跳過外部搜尋，讓 debug 比較好追。"""
    motion_clean = ext_normalize_text(motion)
    metric = (metric or "").strip()

    # 若 KG 已經有明確能力項目，允許 KG 補欄位；若沒有 metric，保守處理。
    has_metric = bool(metric and metric not in ["其他", "未知", "無法分類"])

    if CONSERVATIVE_EXTERNAL_SEARCH and ext_is_vague_motion(motion_clean) and not ext_has_clear_assessment_action(motion_clean) and not has_metric:
        return True, "動作描述過於模糊，且沒有明確可評估動作詞；為避免外部 evidence 搜歪，未進行外部搜尋。", "模糊動作"

    if CONSERVATIVE_EXTERNAL_SEARCH and ext_is_daily_motion_without_difficulty(motion_clean) and not has_metric:
        return True, "此筆較像日常非評估動作，且沒有明顯困難、失衡、疼痛或無力線索；僅保留觀察，不自動套用復健網頁。", "日常非評估動作"

    return False, "", ""


def ext_build_dynamic_query_plan(motion: str, metric: Optional[str] = None, speed: Optional[str] = None) -> Dict:
    """
    v10：保守版 query plan。
    - functional_movement 不搜尋。
    - 日常非評估動作不搜尋。
    - 模糊且無明確動作不搜尋。
    """
    motion_clean = ext_normalize_text(motion)

    skip, reason, topic = ext_should_skip_external_by_motion(motion_clean, metric=metric)
    if skip:
        return {
            "should_search": False,
            "query": "",
            "query_variants": [],
            "reason": reason,
            "required_terms": [],
            "required_any": [],
            "action_any": [],
            "reject_terms": list(NON_MOTION_TOPIC_HINTS),
            "movement_components": "",
            "topic": topic,
            "group_key": "functional_movement",
            "group_keys": [],
            "sub_actions": [],
            "secondary_groups": [],
        }

    selected = ext_select_groups_for_motion(motion_clean, metric=metric, speed=speed)
    group_keys = [k for k in (selected.get("group_keys") or []) if k and k != "functional_movement"]

    if not group_keys:
        return {
            "should_search": False,
            "query": "",
            "query_variants": [],
            "reason": "無法穩定歸入明確能力項目；functional_movement 不做外部搜尋，避免採用不相關 evidence。",
            "required_terms": [],
            "required_any": [],
            "action_any": [],
            "reject_terms": list(NON_MOTION_TOPIC_HINTS),
            "movement_components": "",
            "topic": "功能性動作控制",
            "group_key": "functional_movement",
            "group_keys": [],
            "sub_actions": selected.get("sub_actions", []),
            "secondary_groups": [],
        }

    first_key = group_keys[0]
    plan = ext_build_group_query_plan(first_key, motion_clean, metric=metric, speed=speed)
    plan["group_keys"] = group_keys
    plan["sub_actions"] = [sa for sa in selected.get("sub_actions", []) if sa.get("group_key") != "functional_movement"]
    plan["secondary_groups"] = [k for k in group_keys if k != first_key]
    plan["topic"] = "、".join(MOVEMENT_GROUP_PROFILES[k]["title"] for k in group_keys if k in MOVEMENT_GROUP_PROFILES)
    plan["movement_components"] = "、".join(ext_unique_keep_order(sum([
        MOVEMENT_GROUP_PROFILES[k]["components_zh"] for k in group_keys if k in MOVEMENT_GROUP_PROFILES
    ], [])))
    return plan


def ext_contains_bad_evidence_topic(title: str, url: str, snippet: str = "") -> bool:
    zh, low = _v9_text_blob(title, url, snippet)
    return any(k in zh for k in BAD_EVIDENCE_HINTS_ZH) or any(k in low for k in BAD_EVIDENCE_HINTS_EN)


def ext_title_matches_group(title: str, group_key: str) -> bool:
    """v10：functional_movement 不接受；其他 group 仍要求標題貼題。"""
    if group_key == "functional_movement" and SKIP_FUNCTIONAL_EXTERNAL_SEARCH:
        return False
    if not STRICT_TITLE_MATCH:
        return True
    title = str(title or "").strip()
    if not title:
        return False
    low = title.lower()
    zh_terms = TITLE_KEYWORDS_ZH.get(group_key, []) + GROUP_ACTION_KEYWORDS_ZH.get(group_key, [])
    en_terms = TITLE_KEYWORDS_EN.get(group_key, []) + GROUP_ACTION_KEYWORDS_EN.get(group_key, [])
    return any(t in title for t in zh_terms) or any(t.lower() in low for t in en_terms)


def ext_is_relevant_evidence(item: Dict, plan: Dict) -> bool:
    """
    v10：更嚴格的 evidence gate。
    只允許「標題貼題 + 內容貼題 + 健康/復健語境」的網頁進入報告。
    """
    title = item.get("title", "")
    url = item.get("url", "")
    snippet = item.get("snippet", "")

    if ext_is_english_or_empty_source(title, url, snippet):
        return False

    zh, text = _v9_text_blob(title, url, snippet)

    group_key = plan.get("group_key", "functional_movement")
    if group_key == "functional_movement" and SKIP_FUNCTIONAL_EXTERNAL_SEARCH:
        return False

    if ext_contains_bad_evidence_topic(title, url, snippet):
        return False
    if ext_is_pdf_source(title, url, snippet):
        return False
    if ext_is_commerce_or_assistive_source(title, url, snippet):
        return False
    if ext_is_academic_or_event_source(title, url, snippet):
        return False
    if ext_is_blocked_source(title, url, snippet):
        return False
    if ext_is_research_source(title, url, snippet) and not ALLOW_RESEARCH_EVIDENCE:
        return False
    if not ext_title_matches_group(title, group_key):
        return False

    reject_terms = set(plan.get("reject_terms", []) or []) | set(NON_MOTION_TOPIC_HINTS)
    reject_terms |= set(MOVEMENT_GROUP_PROFILES.get(group_key, {}).get("reject_extra", []))
    for term in reject_terms:
        term = str(term).strip()
        if term and term.lower() in text:
            return False

    zh_group_terms = TITLE_KEYWORDS_ZH.get(group_key, []) + GROUP_ACTION_KEYWORDS_ZH.get(group_key, [])
    en_group_terms = TITLE_KEYWORDS_EN.get(group_key, []) + GROUP_ACTION_KEYWORDS_EN.get(group_key, [])
    title_group_hit = any(k in str(title) for k in zh_group_terms) or any(k.lower() in str(title).lower() for k in en_group_terms)
    snippet_group_hit = any(k in zh for k in zh_group_terms) or any(k.lower() in text for k in en_group_terms)
    if not (title_group_hit and snippet_group_hit):
        return False

    rehab_or_health_hit = (
        any(k in text for k in MOTION_REHAB_HINTS)
        or any(k in zh for k in CHINESE_REHAB_HINTS)
        or any(k in zh for k in ["健康", "衛教", "運動", "訓練", "復健", "物理治療", "職能治療", "治療師", "長者", "老人"])
    )
    if not rehab_or_health_hit:
        return False

    return ext_source_priority(title, url, snippet) > 0


def ext_search_one_group(group_key: str, motion: str, metric: Optional[str], speed: Optional[str], max_results: int, max_pages: int) -> dict:
    """
    v10.3：候選池全部評分後取 Top3 evidence。
    - 不再遇到第一筆可用 evidence 就 break。
    - 先把多個 query 的候選結果放進同一個候選池。
    - 先用 title/snippet/url 做初篩與初分。
    - 若需要抓全文，先抓前幾名候選的 page_text，再重新計算總分。
    - 最後取分數最高的 Top3，並以文章時間由新到舊排序呈現。
    """
    if group_key == "functional_movement" and SKIP_FUNCTIONAL_EXTERNAL_SEARCH:
        return {
            "group_key": group_key,
            "topic": MOVEMENT_GROUP_PROFILES["functional_movement"]["title"],
            "movement_components": "、".join(MOVEMENT_GROUP_PROFILES["functional_movement"]["components_zh"]),
            "query": "",
            "query_attempts": [],
            "evidence": [],
            "skip_reason": "功能性動作控制屬於 fallback 類別，未進行外部搜尋，以避免引用不相關網頁。",
        }

    plan = ext_build_group_query_plan(group_key, motion, metric=metric, speed=speed)
    query_attempts = []
    candidates = []
    seen_urls = set()

    # 候選池概念：
    # 讓一般 query 與高可信來源補查 query 都先進入同一個候選池，
    # 不因為前面某個 query 找到第一筆可用資料就提前停止。
    max_query_attempts = max(8, SEARCH_VARIANT_LIMIT)
    search_limit_per_query = max(max_results, 10)

    for q in plan.get("query_variants") or [plan["query"]]:
        q = ext_normalize_text(q)
        if not q or q in query_attempts:
            continue

        query_attempts.append(q)
        search_results = ext_web_search(query=q, max_results=search_limit_per_query)

        for item in search_results:
            url = item.get("url", "")
            if not url or url in seen_urls:
                continue

            if not ext_is_relevant_evidence(item, plan):
                continue

            source_priority = ext_source_priority(
                item.get("title", ""),
                item.get("url", ""),
                item.get("snippet", "")
            )
            initial_score = ext_evidence_total_score(item, plan)

            if STRICT_PROFESSIONAL_SOURCES and initial_score < MIN_SOURCE_PRIORITY:
                continue

            seen_urls.add(url)
            candidates.append({
                **item,
                "source_priority": source_priority,
                "evidence_total_score": initial_score,
                "initial_total_score": initial_score,
                "page_text": "",
                "group_key": group_key,
                "matched_query": q,
            })

        if len(query_attempts) >= max_query_attempts:
            break

    # 沒有任何候選就直接回傳空 evidence。
    if not candidates:
        return {
            "group_key": group_key,
            "topic": MOVEMENT_GROUP_PROFILES.get(group_key, MOVEMENT_GROUP_PROFILES["functional_movement"])["title"],
            "movement_components": "、".join(MOVEMENT_GROUP_PROFILES.get(group_key, MOVEMENT_GROUP_PROFILES["functional_movement"])["components_zh"]),
            "query": plan["query"],
            "query_attempts": query_attempts,
            "evidence": [],
            "skip_reason": "外部搜尋結果未通過 v10.2 候選池 evidence gate，未採用外部 evidence。",
        }

    # 先用初分排序，再視需要抓前幾名全文。
    candidates = sorted(
        candidates,
        key=lambda x: (
            x.get("evidence_total_score", 0),
            x.get("source_priority", 0)
        ),
        reverse=True,
    )

    if max_pages > 0:
        # 不只抓第一名，避免第一名 snippet 看起來好，但全文是英文首頁或沒有實際內容。
        fetch_top_n = min(len(candidates), max(6, max_pages))
        checked_candidates = []
        for item in candidates[:fetch_top_n]:
            page_text = ext_fetch_page_text(item["url"], timeout=5, max_chars=3000)
            if not ext_is_usable_chinese_page_text(page_text, plan):
                continue
            item["page_text"] = page_text
            item["evidence_total_score"] = ext_evidence_total_score(item, plan, page_text=page_text)
            checked_candidates.append(item)

        # 有抓全文時，只保留「真的抓得到中文內容」的候選，避免報告列出點進去沒內容的頁面。
        candidates = checked_candidates

    if not candidates:
        return {
            "group_key": group_key,
            "topic": MOVEMENT_GROUP_PROFILES.get(group_key, MOVEMENT_GROUP_PROFILES["functional_movement"])["title"],
            "movement_components": "、".join(MOVEMENT_GROUP_PROFILES.get(group_key, MOVEMENT_GROUP_PROFILES["functional_movement"])["components_zh"]),
            "query": plan["query"],
            "query_attempts": query_attempts,
            "evidence": [],
            "skip_reason": "外部搜尋結果不是中文內容頁或沒有可用內文，未採用外部 evidence。",
        }

    # 抓完全文後再重新排序，最後才決定採用哪一筆。
    candidates = sorted(
        candidates,
        key=lambda x: (
            x.get("evidence_total_score", 0),
            x.get("source_priority", 0)
        ),
        reverse=True,
    )

    top_candidates = candidates[:3]
    evidence_items = []
    for item in top_candidates:
        evidence_items.append({
            "title": item["title"],
            "url": item["url"],
            "snippet": item["snippet"],
            "page_text": item.get("page_text", ""),
            "source_priority": item.get("source_priority", 0),
            "evidence_total_score": item.get("evidence_total_score", item.get("source_priority", 0)),
            "matched_query": item.get("matched_query", ""),
            "evidence_date": ext_extract_evidence_date(item).strftime("%Y-%m-%d") if ext_extract_evidence_date(item) != datetime.min else "",
            "group_key": group_key,
        })

    evidence_items = ext_sort_evidence_by_time(evidence_items)

    return {
        "group_key": group_key,
        "topic": MOVEMENT_GROUP_PROFILES.get(group_key, MOVEMENT_GROUP_PROFILES["functional_movement"])["title"],
        "movement_components": "、".join(MOVEMENT_GROUP_PROFILES.get(group_key, MOVEMENT_GROUP_PROFILES["functional_movement"])["components_zh"]),
        "query": plan["query"],
        "query_attempts": query_attempts,
        "evidence": evidence_items,
        "skip_reason": "",
    }


def ext_collect_external_evidence(motion: str, metric: Optional[str] = None, speed: Optional[str] = None, max_results: int = 1, max_pages: int = 0) -> Dict:
    """v10：只對明確 group 搜尋；模糊/日常/fallback 類直接回傳 skipped。"""
    plan = ext_build_search_plan(motion=motion, metric=metric, speed=speed)
    if not plan["should_search"]:
        return {
            "used_external_search": False,
            "external_search_skipped": True,
            "skip_reason": plan.get("reason", "未進行外部搜尋。"),
            "query": "",
            "query_attempts": [],
            "motion": motion,
            "metric": metric,
            "speed": speed,
            "topic": plan.get("topic", ""),
            "group_key": plan.get("group_key", "functional_movement"),
            "group_keys": plan.get("group_keys", []),
            "sub_actions": plan.get("sub_actions", []),
            "sub_bundles": [],
            "secondary_groups": plan.get("secondary_groups", []),
            "movement_components": plan.get("movement_components", ""),
            "is_daily_non_assessment_motion": ext_is_daily_non_assessment_motion(motion),
            "evidence": [],
        }

    group_keys = [k for k in (plan.get("group_keys") or [plan.get("group_key", "functional_movement")]) if k != "functional_movement"]
    if not group_keys:
        return {
            "used_external_search": False,
            "external_search_skipped": True,
            "skip_reason": "沒有明確能力群組，未進行外部搜尋。",
            "query": "",
            "query_attempts": [],
            "motion": motion,
            "metric": metric,
            "speed": speed,
            "topic": "功能性動作控制",
            "group_key": "functional_movement",
            "group_keys": [],
            "sub_actions": plan.get("sub_actions", []),
            "sub_bundles": [],
            "secondary_groups": [],
            "movement_components": "",
            "is_daily_non_assessment_motion": ext_is_daily_non_assessment_motion(motion),
            "evidence": [],
        }

    sub_bundles = []
    all_evidence = []
    all_attempts = []
    for key in group_keys:
        b = ext_search_one_group(key, motion, metric, speed, max_results=max_results, max_pages=max_pages)
        sub_bundles.append(b)
        all_attempts.extend(b.get("query_attempts", []))
        all_evidence.extend(b.get("evidence", []))

    primary_key = group_keys[0]
    topic = "、".join(MOVEMENT_GROUP_PROFILES[k]["title"] for k in group_keys if k in MOVEMENT_GROUP_PROFILES)
    components = "、".join(ext_unique_keep_order(sum([
        MOVEMENT_GROUP_PROFILES[k]["components_zh"] for k in group_keys if k in MOVEMENT_GROUP_PROFILES
    ], [])))

    return {
        "used_external_search": True,
        "external_search_skipped": False,
        "skip_reason": "" if all_evidence else "外部搜尋結果未通過 v10 嚴格 evidence gate，未採用外部 evidence。",
        "query": "；".join([b.get("query", "") for b in sub_bundles if b.get("query")]),
        "query_attempts": ext_unique_keep_order(all_attempts),
        "motion": motion,
        "metric": metric,
        "speed": speed,
        "topic": topic or plan.get("topic", ""),
        "group_key": primary_key,
        "group_keys": group_keys,
        "sub_actions": plan.get("sub_actions", []),
        "sub_bundles": sub_bundles,
        "secondary_groups": [k for k in group_keys if k != primary_key],
        "movement_components": components or plan.get("movement_components", ""),
        "is_daily_non_assessment_motion": ext_is_daily_non_assessment_motion(motion),
        # 保留最多 3 筆外部 evidence，讓「外部來源紀錄」可以真正列出 1、2、3。
        # 其他搜尋、排序與生成邏輯維持原狀。
        "evidence": all_evidence[:3],
    }

def debug_external_case(motion, metric=None, speed=None):
    print("\n" + "=" * 80)
    print("Motion:", motion)
    print("Metric:", metric)
    print("Speed:", speed)

    plan = ext_build_search_plan(motion, metric=metric, speed=speed)
    print("\n[PLAN]")
    print("should_search:", plan.get("should_search"))
    print("topic:", plan.get("topic"))
    print("group_key:", plan.get("group_key"))
    print("group_keys:", plan.get("group_keys"))
    print("reason:", plan.get("reason"))
    print("query:", plan.get("query"))
    print("query_variants:")
    for q in plan.get("query_variants", [])[:5]:
        print(" -", q)

    result = external_search_fallback(
        motion=motion,
        metric=metric,
        speed=speed,
        max_results=1,
        max_pages=1
    )

    bundle = result["evidence_bundle"]

    print("\n[EVIDENCE]")
    print("used_external_search:", bundle.get("used_external_search"))
    print("skip_reason:", bundle.get("skip_reason"))
    print("topic:", bundle.get("topic"))

    for i, e in enumerate(bundle.get("evidence", []), start=1):
        print(f"\nEvidence {i}")
        print("priority:", e.get("source_priority"))
        print("title:", e.get("title"))
        print("url:", e.get("url"))
        print("snippet:", e.get("snippet")[:300])

# ============================================================
# 主程式
# ============================================================
if __name__ == "__main__":

    # debug_external_case("A person stands up from a chair.", metric="下肢肌力", speed="normal")
    # debug_external_case("A person walks forward and turns left.", metric="步行速度", speed="normal")
    # debug_external_case("A person raises one arm.", metric="上肢與軀幹活動", speed="normal")
    # debug_external_case("A person drinks water from a cup.", metric=None, speed="normal")
    # debug_external_case("A person stands on one leg.", metric="單腳站立", speed="normal")
    
    # 解析命令行參數
    args = get_command_args()

    # ─── 決定輸入來源 ────────────────────────────────────────────
    # 1. 有指定 --input：維持原本測試流程，只讀單一 JSON 檔
    # 2. 沒有 --input，但有 --start / --end：正式時間查詢流程，掃描固定 data 資料夾
    # 3. 兩者都沒有：維持原本預設，讀取 move.json
    input_file = None
    use_data_dir_by_time = False

    if args.input:
        input_file = Path(args.input)

        # 相對路徑：先以目前工作目錄為準（例如 data/samples/01.json），
        # 找不到再接在 data/samples/ 下面（例如 01.json）
        if not input_file.is_absolute() and not input_file.is_file():
            input_file = DATA_DIR / input_file

        if not input_file.is_file():
            print(f"\n❌ 找不到 JSON 檔案：{input_file}")
            raise SystemExit(1)

    elif args.start or args.end:
        use_data_dir_by_time = True

    else:
        input_file = JSON_PATH

    print("\n" + "=" * 70)
    print("🏥 長者行動能力分析系統")
    print("=" * 70)

    # ─── 讀取 JSON 記錄 ──────────────────────────────────────────
    if use_data_dir_by_time:
        INPUT_JSON = load_records_from_data_dir(DATA_DIR)
    else:
        try:
            with open(input_file, "r", encoding="utf-8") as f:
                INPUT_JSON = json.load(f)
            print(f"\n✅ 讀取 JSON：{input_file}，共 {len(INPUT_JSON)} 筆記錄")
        except FileNotFoundError:
            print(f"\n❌ 找不到 JSON：{input_file}")
            raise SystemExit(1)
        except json.JSONDecodeError as e:
            print(f"\n❌ JSON 格式錯誤：{e}")
            raise SystemExit(1)

    # ─── 時間範圍過濾 ────────────────────────────────────────────
    INPUT_JSON = filter_records_by_time(INPUT_JSON, args.start, args.end)


    # ─── 執行分析管線 ────────────────────────────────────────────
    try:
        report = run_pipeline(INPUT_JSON, skip_neo4j=args.no_db)

        # 寫出 Markdown 報告檔案
        print("\n" + "=" * 70)
        print("📋 分析報告")
        print("=" * 70)
        print(report)
    except Exception as e:
        print(f"\n❌ 分析過程出現錯誤：{e}")
        import traceback
        traceback.print_exc()
        raise SystemExit(1)
    finally:
        if not args.no_db:
            driver.close()
        print("\n🔌 已安全關閉資料庫連線。")
