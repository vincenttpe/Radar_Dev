"""pyproject.toml：指令入口指向真的存在、可無參數呼叫的函式，依賴與 requirements-dev.txt 一致。"""
import importlib
import sys
import tomllib

import pytest
import yaml
from packaging.requirements import Requirement


@pytest.fixture(scope="module")
def project(repo):
    return tomllib.loads((repo / "pyproject.toml").read_text(encoding="utf-8"))["project"]


def test_declared_scripts(project):
    assert project["scripts"] == {
        "radar-weekly-report": "radar_agent.weekly_report:main",
        "radar-kg-manifest": "radar_agent.kg_manifest:main",
        "radar-kg-build": "radar_agent.kg_source:main",
        "radar-kg-level": "radar_agent.kg_rules:main",
    }


def test_every_script_target_exists_and_is_callable(project):
    for target in project["scripts"].values():
        module, _, func = target.partition(":")
        assert callable(getattr(importlib.import_module(module), func)), target


def test_kg_manifest_main_reads_sys_argv_when_called_without_arguments(monkeypatch):
    """console script 呼叫 main() 時不帶參數，要自己從 sys.argv 取。"""
    from radar_agent import kg_manifest

    monkeypatch.setattr(sys, "argv", ["radar-kg-manifest", "--check"])
    assert kg_manifest.main() == 0


def test_weekly_report_main_reads_sys_argv_when_called_without_arguments(repo, monkeypatch, capsys):
    from radar_agent import weekly_report

    path = repo / "data" / "samples_fall" / "2026-06-03.json"
    monkeypatch.setattr(sys, "argv", ["radar-weekly-report", "--input", str(path)])
    assert weekly_report.main() == 0
    assert "本次輸入的紀錄中，跌倒標記共 **1** 筆。" in capsys.readouterr().out


def _names(requirements: list[str]) -> set[str]:
    """['ruff>=0.16,<0.17', 'pytest-cov>=5'] → {'ruff', 'pytest-cov'}。逐一解析，不用子字串。"""
    return {Requirement(r).name for r in requirements}


def test_runtime_dependencies_are_exactly_what_the_package_imports(project):
    assert _names(project["dependencies"]) == {"jsonschema", "rfc3339-validator"}


def test_requirements_dev_installs_this_package_editable_with_dev_extra(repo):
    lines = [line.strip() for line in (repo / "requirements-dev.txt").read_text(encoding="utf-8").splitlines()]
    assert "-e .[dev]" in lines


@pytest.fixture(scope="module")
def ci_job(repo):
    workflow = yaml.safe_load((repo / ".github" / "workflows" / "tests.yml").read_text(encoding="utf-8"))
    return workflow["jobs"]["pytest"]


def test_ci_matrix_covers_the_declared_python_floor(ci_job, project):
    floor = project["requires-python"].removeprefix(">=")
    versions = ci_job["strategy"]["matrix"]["python-version"]
    # YAML 裡沒加引號的 3.10 會變成浮點數 3.1，所以要求每個版本都是字串
    assert all(isinstance(v, str) for v in versions), versions
    assert floor in versions, f"CI 沒有測到宣告的下限 {floor}，只測了 {versions}"


def test_lint_and_type_targets_follow_the_python_floor(repo, project):
    floor = project["requires-python"].removeprefix(">=")
    tools = tomllib.loads((repo / "pyproject.toml").read_text(encoding="utf-8"))["tool"]
    assert tools["mypy"]["python_version"] == floor
    assert tools["ruff"]["target-version"] == "py" + floor.replace(".", "")


def _gate_steps(ci_job: dict) -> dict[str, dict]:
    """{指令: 步驟}，只算「無條件執行」的步驟；被註解掉的步驟不在 YAML 裡，有 if 條件的不算。"""
    return {step["run"].strip(): step for step in ci_job["steps"] if "run" in step and "if" not in step}


@pytest.mark.parametrize("command", ["ruff check src tests", "mypy", "pytest --cov"])
def test_ci_runs_every_quality_gate_unconditionally_and_without_swallowing_failures(ci_job, command):
    steps = _gate_steps(ci_job)
    assert command in steps, f"CI 沒有無條件執行 {command!r}"
    assert not steps[command].get("continue-on-error"), f"{command!r} 失敗時不應該被忽略"


def test_dev_extra_has_the_tools_ci_uses(project):
    """逐一比對套件名稱：pytest 不會被 pytest-cov 蓋過，也不會被註解或別的套件名稱湊數。"""
    assert {"pytest", "pytest-cov", "ruff", "mypy", "pyyaml"} <= _names(project["optional-dependencies"]["dev"])


def test_coverage_gate_is_configured_and_matches_the_readme(repo):
    coverage = tomllib.loads((repo / "pyproject.toml").read_text(encoding="utf-8"))["tool"]["coverage"]
    threshold = coverage["report"]["fail_under"]
    assert threshold == 95, "要改門檻時，請連 README 的說明一起改，並更新這個測試"
    assert coverage["run"]["branch"] is True
    assert f"低於 {threshold}%" in (repo / "README.md").read_text(encoding="utf-8")


@pytest.mark.parametrize("module,args", [
    ("radar_agent.kg_manifest", ["--check"]),
    ("radar_agent.kg_source", ["--check"]),
    ("radar_agent.weekly_report", ["--input", "data/samples_fall/2026-06-03.json"]),
    ("radar_agent.kg_rules", ["來回行走", "5.4"]),
])
def test_modules_run_as_scripts(repo, module, args):
    """python -m <模組> 的進入點（if __name__ == '__main__'）真的能跑，結束碼為 0。"""
    import os
    import subprocess

    env = {**os.environ, "PYTHONPATH": str(repo / "src")}
    result = subprocess.run([sys.executable, "-m", module, *args], cwd=repo, env=env, capture_output=True, text=True, encoding="utf-8", check=False)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip()
