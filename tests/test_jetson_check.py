"""scripts/jetson_check.sh：在一般 Linux 上也能跑完，只讀、不印出可識別機器的資訊。"""
import re
import shutil
import subprocess

import pytest

pytestmark = pytest.mark.skipif(shutil.which("bash") is None, reason="需要 bash")


def run(repo, *args):
    return subprocess.run(
        ["bash", str(repo / "scripts" / "jetson_check.sh"), *args],
        capture_output=True, text=True, timeout=60, check=False,
    )


def test_runs_offline_and_prints_every_section(repo):
    r = run(repo, "--offline")
    assert r.returncode == 0, r.stderr
    for title in ["型號與系統（B1）", "記憶體", "磁碟", "Python 與工具", "正在執行的服務（B2 的線索）", "外網（B5）", "摘要"]:
        assert f"## {title}" in r.stdout
    assert "略過（--offline）" in r.stdout


def test_does_not_print_addresses_or_hostname(repo):
    out = run(repo, "--offline").stdout
    assert not re.search(r"\b\d{1,3}(\.\d{1,3}){3}\b", out)
    assert not re.search(r"\b[0-9a-f]{2}(:[0-9a-f]{2}){5}\b", out, re.I)
    host = subprocess.run(["uname", "-n"], capture_output=True, text=True, check=False).stdout.strip()
    if len(host) >= 4:
        assert host not in out


def test_unknown_option_is_usage_error(repo):
    r = run(repo, "--fix")
    assert r.returncode == 2
    assert "用法" in r.stderr
