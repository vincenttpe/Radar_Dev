"""舊雛形的標註資料 data/labels/sorted_data.csv。

這份資料在新系統會捨棄（待確認事項第 11 項），測試只固定它目前的狀態，
讓 docs/01_現況架構.md 寫的數字有依據。
"""
from collections import Counter

PROTOTYPE_CLASSES = {
    "下肢肌力", "單腳站立", "心肺耐力", "步行速度",
    "來回行走", "上肢與軀幹活動", "空間方位位移", "其他",
}

# 已知互相矛盾的重複句（見 docs/01_現況架構.md 已知問題第 3 點）。
# 新增矛盾會讓測試失敗，提醒要一起更新文件。
KNOWN_CONFLICTS = {
    "A person walks in a clockwise circle.": {("來回行走", "3"), ("來回行走+空間方位位移", "2")},
}


def test_columns(label_rows):
    assert set(label_rows[0]) == {"coarsed_sentence", "class", "score"}


def test_row_count_matches_docs(label_rows):
    assert len(label_rows) == 518


def test_scores_are_0_to_4(label_rows):
    assert {r["score"] for r in label_rows} == {"0", "1", "2", "3", "4"}


def test_class_components_belong_to_eight_prototype_classes(label_rows):
    components = {c.strip() for r in label_rows for c in r["class"].split("+")}
    assert components == PROTOTYPE_CLASSES


def test_combined_class_count_matches_docs(label_rows):
    combined = [r for r in label_rows if "+" in r["class"]]
    assert len(combined) == 80


def test_single_label_counts_quoted_in_docs(label_rows):
    single = Counter(r["class"] for r in label_rows if "+" not in r["class"])
    assert single["單腳站立"] == 1
    assert single["步行速度"] == 3
    assert single["心肺耐力"] == 9
    assert single["空間方位位移"] == 219


def test_duplicate_sentences_are_only_the_known_conflicts(label_rows):
    by_sentence: dict[str, set] = {}
    for r in label_rows:
        by_sentence.setdefault(r["coarsed_sentence"], set()).add((r["class"], r["score"]))
    duplicates = {s: labels for s, labels in by_sentence.items() if len(labels) > 1}
    assert duplicates == KNOWN_CONFLICTS
