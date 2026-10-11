"""規則引擎：量測值 → 知識圖譜等級。只在恰好落在一個等級時給分，其餘一律說明原因。"""
import json
import re
import shutil
from decimal import Decimal

import pytest

from radar_agent import kg_rules
from radar_agent.kg_manifest import sha256_of


@pytest.fixture(scope="module")
def kg(repo):
    return kg_rules.load("v1", repo)


# ── 讀入與版本 ────────────────────────────────────────────

def test_load_records_version_and_approval(kg):
    assert kg.info == {"version": "v1", "approval": "pending", "approved": False}
    assert set(kg.thresholds) == {"下肢肌力", "單腳站立", "來回行走", "心肺耐力", "步行速度"}


@pytest.fixture
def repo_copy(repo, tmp_path):
    """只複製規則引擎會讀的檔案，讓測試可以改內容而不動到真的 repo。"""
    for rel in ("kg/releases.json", "kg/v1/thresholds.json", "kg/v1/metrics.json"):
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(repo / rel, tmp_path / rel)
    return tmp_path


def test_files_that_differ_from_the_manifest_are_refused(repo_copy):
    path = repo_copy / "kg" / "v1" / "thresholds.json"
    path.write_text(path.read_text(encoding="utf-8").replace('"min": 18', '"min": 17'), encoding="utf-8")
    with pytest.raises(kg_rules.KgIntegrityError, match=re.escape("kg/v1/thresholds.json 與 kg/releases.json 登記的雜湊不同")):
        kg_rules.load("v1", repo_copy)


def test_unknown_version_is_refused(repo):
    with pytest.raises(kg_rules.KgIntegrityError, match="沒有登記版本 'v9'"):
        kg_rules.load("v9", repo)


def test_approved_release_is_reported_as_approved(repo_copy):
    manifest_path = repo_copy / "kg" / "releases.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["releases"][0]["approval"] = {"status": "approved", "approver": "審核人", "date": "2026-10-09"}
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    kg = kg_rules.load("v1", repo_copy)
    assert kg.info["approved"] is True
    text = kg_rules.render_text(kg_rules.assess(kg, "來回行走", "5.4"))
    assert "知識圖譜 v1：已核准" in text and "僅供測試" not in text


# ── 給分 ─────────────────────────────────────────────────

def test_every_level_boundary_written_in_the_source_maps_to_that_level(kg):
    """來源寫出的每個下限與上限都要回到該等級；除了已知的重疊邊界（見 test_thresholds.KNOWN_ISSUES）。"""
    overlapping = {("步行速度", Decimal("0.5")), ("步行速度", Decimal("0.8")), ("步行速度", Decimal("1.0"))}
    checked = 0
    for name, spec in kg.thresholds.items():
        for level in spec["levels"]:
            for edge in (level["min"], level["max"]):
                if edge is None or (name, Decimal(str(edge))) in overlapping:
                    continue
                a = kg_rules.assess(kg, name, str(edge))
                assert (a["status"], a["score"]) == ("assessed", level["score"]), (name, edge, a["reason"])
                checked += 1
    assert checked == 37  # 24 個下限＋19 個上限，扣掉步行速度 3 個共用邊界各出現兩次


def test_assessed_result_carries_the_rule_and_the_advice(kg, metrics_json):
    a = kg_rules.assess(kg, "來回行走", "5.4")
    level = next(lv for m in metrics_json["metrics"] if m["name"] == "來回行走" for lv in m["levels"] if lv["score"] == 4)
    assert a == {
        "metric": "來回行走", "value": "5.4", "unit": "秒（2.44 公尺）", "status": "assessed",
        "score": 4, "rank": "很好", "source_text": level["name"],
        "suggestion": level["suggestion"], "recommended_dose": level["recommended_dose"],
        "reason": None, "candidates": [], "kg": {"version": "v1", "approval": "pending", "approved": False},
    }


@pytest.mark.parametrize("metric,value,score", [
    ("下肢肌力", 100, 4),          # 開放上界
    ("下肢肌力", "0", 0),
    ("步行速度", "0.95", 2),        # 連續量，兩邊界之間
    ("步行速度", "2.5", 3),
    ("來回行走", "6.10", 3),        # 寫法不同但數值相同，仍在精度內
    ("心肺耐力", "1e2", None),      # 100：缺口，下一題另測
])
def test_values_inside_a_single_level(kg, metric, value, score):
    a = kg_rules.assess(kg, metric, value)
    assert a["score"] == score


# ── 不給分的情況：缺口、重疊、精度、無效值、沒有規則 ──────────

@pytest.mark.parametrize("value", ["100", "105", "111"])
def test_cardio_gap_is_unassessable(kg, value):
    a = kg_rules.assess(kg, "心肺耐力", value)
    assert a["status"] == "unassessable" and a["score"] is None and a["suggestion"] is None
    assert a["reason"] == f"數值 {value} 不屬於任何等級（門檻缺口，待審核人決定，會議清單 E5）"


@pytest.mark.parametrize("value,low,high", [("0.5", 0, 1), ("0.8", 1, 2), ("1.0", 2, 3), ("1", 2, 3)])
def test_walking_speed_shared_edges_are_unassessable_and_list_both_levels(kg, value, low, high):
    a = kg_rules.assess(kg, "步行速度", value)
    assert a["status"] == "unassessable" and a["score"] is None
    assert [c["score"] for c in a["candidates"]] == [low, high]
    assert "門檻重疊" in a["reason"] and "E5" in a["reason"]


@pytest.mark.parametrize("metric,value,resolution", [("來回行走", "5.45", "0.1"), ("下肢肌力", "13.5", "1")])
def test_values_finer_than_the_source_resolution_are_not_rounded(kg, metric, value, resolution):
    a = kg_rules.assess(kg, metric, value)
    assert a["status"] == "unassessable"
    assert a["reason"].startswith(f"數值 {value} 比來源寫出的精度（{resolution}）更細")


def test_value_below_every_level(kg):
    assert kg_rules.assess(kg, "下肢肌力", "-1")["reason"] == "數值 -1 低於所有等級的下限 0"


@pytest.mark.parametrize("metric,value,score", [("來回行走", "-1e100", None), ("來回行走", "1e100", 0), ("步行速度", "-1e-100", None)])
def test_extreme_values_never_raise(kg, metric, value, score):
    assert kg_rules.assess(kg, metric, value)["score"] == score


@pytest.mark.parametrize("value", ["abc", "", "nan", "inf", "-Infinity", True, None])
def test_invalid_values(kg, value):
    a = kg_rules.assess(kg, "單腳站立", value)
    assert a["status"] == "unassessable" and a["reason"].endswith("不是有效的數字")


def test_metric_without_rule_goes_to_review(kg):
    a = kg_rules.assess(kg, "握力", "20")
    assert a["status"] == "no_rule" and a["unit"] is None and a["score"] is None
    assert a["reason"] == "知識圖譜 v1 沒有「握力」這個指標，不輸出等級；請送審核佇列"


def test_contains_respects_exclusive_bounds():
    """目前的來源全是含邊界；不含邊界的寫法日後出現時也要正確。"""
    level = {"min": 1, "max": 2, "min_inclusive": False, "max_inclusive": False}
    assert [kg_rules._contains(level, Decimal(v)) for v in ("1", "1.5", "2")] == [False, True, False]


def test_assessment_is_json_serializable(kg):
    json.dumps(kg_rules.assess(kg, "步行速度", "0.8"), ensure_ascii=False)


# ── 輸出與命令列 ──────────────────────────────────────────

def test_render_assessed(kg):
    text = kg_rules.render_text(kg_rules.assess(kg, "來回行走", "5.4"))
    assert text.startswith("來回行走 5.4 秒（2.44 公尺） → 4 分「很好」（2.44公尺行走 0~5.4秒）\n建議：")
    assert "\n建議數值：" in text
    assert text.endswith("知識圖譜 v1：未核准（pending），結果僅供測試\n")


def test_render_without_dose(kg):
    a = {**kg_rules.assess(kg, "來回行走", "5.4"), "recommended_dose": None}
    assert "建議數值" not in kg_rules.render_text(a)


def test_render_unassessable_and_no_rule(kg):
    assert kg_rules.render_text(kg_rules.assess(kg, "心肺耐力", "105")).startswith("心肺耐力 105 次/2 分鐘 → 無法評估：")
    assert kg_rules.render_text(kg_rules.assess(kg, "握力", "20")).startswith("握力 20 → 沒有規則：")


def test_render_list(kg):
    text = kg_rules.render_list(kg)
    assert text.startswith("知識圖譜 v1：未核准")
    assert "心肺耐力（次/2 分鐘）\n  4 分「很好」：" in text
    assert text.count("分「") == sum(len(m["levels"]) for m in kg.thresholds.values())


@pytest.mark.parametrize("args,code,needle", [
    (["來回行走", "5.4"], 0, "→ 4 分「很好」"),
    (["心肺耐力", "105"], 1, "無法評估"),
    (["握力", "20"], 1, "沒有規則"),
    (["--list"], 0, "步行速度（m/s）"),
])
def test_cli(args, code, needle, capsys):
    assert kg_rules.main(args) == code
    assert needle in capsys.readouterr().out


def test_cli_json(capsys):
    assert kg_rules.main(["步行速度", "0.8", "--json"]) == 1
    out = json.loads(capsys.readouterr().out)
    assert out["status"] == "unassessable" and len(out["candidates"]) == 2


def test_cli_requires_metric_and_value(capsys):
    assert kg_rules.main(["來回行走"]) == 2
    assert "需要指標名稱與量測值" in capsys.readouterr().err


def test_cli_reports_integrity_errors(repo_copy, monkeypatch, capsys):
    (repo_copy / "kg" / "v1" / "metrics.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(kg_rules, "REPO", repo_copy)
    assert kg_rules.main(["來回行走", "5.4"]) == 2
    assert "metrics.json 與 kg/releases.json 登記的雜湊不同" in capsys.readouterr().err


# 檔案不存在也是「知識圖譜檔案有誤」：結束碼 2，不是 traceback，也不和「無法評估」的 1 混在一起。

def test_missing_manifest_is_an_integrity_error_with_exit_code_2(repo_copy, monkeypatch, capsys):
    (repo_copy / "kg" / "releases.json").unlink()
    with pytest.raises(kg_rules.KgIntegrityError, match=re.escape("找不到清單：kg/releases.json")):
        kg_rules.load("v1", repo_copy)
    monkeypatch.setattr(kg_rules, "REPO", repo_copy)
    assert kg_rules.main(["來回行走", "5.4"]) == 2
    assert capsys.readouterr().err == "錯誤：找不到清單：kg/releases.json\n"


@pytest.mark.parametrize("name", ["thresholds.json", "metrics.json"])
def test_missing_kg_file_is_an_integrity_error_with_exit_code_2(repo_copy, monkeypatch, capsys, name):
    (repo_copy / "kg" / "v1" / name).unlink()
    message = f"kg/v1/{name} 不存在，請先執行 radar-kg-build 與 radar-kg-manifest"
    with pytest.raises(kg_rules.KgIntegrityError, match=re.escape(message)):
        kg_rules.load("v1", repo_copy)
    monkeypatch.setattr(kg_rules, "REPO", repo_copy)
    assert kg_rules.main(["來回行走", "5.4"]) == 2
    assert capsys.readouterr().err == f"錯誤：{message}\n"


def test_main_reads_sys_argv_when_called_without_arguments(monkeypatch, capsys):
    monkeypatch.setattr("sys.argv", ["radar-kg-level", "來回行走", "5.4"])
    assert kg_rules.main() == 0
    assert "4 分" in capsys.readouterr().out


def test_repo_copy_fixture_matches_the_manifest(repo_copy):
    """fixture 本身要是一份通過檢查的副本，上面的拒絕測試才有意義。"""
    manifest = json.loads((repo_copy / "kg" / "releases.json").read_text(encoding="utf-8"))
    rel = "kg/v1/thresholds.json"
    assert manifest["releases"][0]["files"][rel] == sha256_of(repo_copy / rel)
    kg_rules.load("v1", repo_copy)
