"""pyproject.toml：指令入口指向真的存在、可無參數呼叫的函式，依賴與 requirements-dev.txt 一致。"""
import importlib
import sys
import tomllib

import pytest


@pytest.fixture(scope="module")
def project(repo):
    return tomllib.loads((repo / "pyproject.toml").read_text(encoding="utf-8"))["project"]


def test_declared_scripts(project):
    assert project["scripts"] == {
        "radar-weekly-report": "radar_agent.weekly_report:main",
        "radar-kg-manifest": "radar_agent.kg_manifest:main",
        "radar-kg-build": "radar_agent.kg_source:main",
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
    assert "本週跌倒 **1** 次。" in capsys.readouterr().out


def test_runtime_dependencies_cover_what_the_package_imports(project):
    deps = " ".join(project["dependencies"])
    assert "jsonschema" in deps and "rfc3339-validator" in deps


def test_requirements_dev_installs_this_package_editable_with_dev_extra(repo):
    lines = [l.strip() for l in (repo / "requirements-dev.txt").read_text(encoding="utf-8").splitlines()]
    assert "-e .[dev]" in lines


def test_python_version_floor_matches_ci(repo, project):
    assert project["requires-python"] == ">=3.11"
    assert 'python-version: "3.12"' in (repo / ".github" / "workflows" / "tests.yml").read_text(encoding="utf-8")


@pytest.mark.parametrize("module,args", [
    ("radar_agent.kg_manifest", ["--check"]),
    ("radar_agent.kg_source", ["--check"]),
    ("radar_agent.weekly_report", ["--input", "data/samples_fall/2026-06-03.json"]),
])
def test_modules_run_as_scripts(repo, module, args):
    """python -m <模組> 的進入點（if __name__ == '__main__'）真的能跑，結束碼為 0。"""
    import os
    import subprocess

    env = {**os.environ, "PYTHONPATH": str(repo / "src")}
    result = subprocess.run([sys.executable, "-m", module, *args], cwd=repo, env=env, capture_output=True, text=True, encoding="utf-8", check=False)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip()
