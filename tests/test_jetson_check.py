"""scripts/jetson_check.sh：盤點跑得完、只讀、不印出可識別機器的資訊。

Jetson 專有的指令（nvpmodel、tegrastats）與 ss、curl、python 用 PATH 前面的假指令代替，
系統檔用 JETSON_CHECK_ROOT 指到假的資料夾，所以一般 Linux 上也能走到實機才有的分支。
"""
import os
import re
import shutil
import subprocess

import pytest

pytestmark = pytest.mark.skipif(shutil.which("bash") is None, reason="需要 bash")

IPV4 = re.compile(r"\b\d{1,3}(\.\d{1,3}){3}\b")
MAC = re.compile(r"\b[0-9a-f]{2}(:[0-9a-f]{2}){5}\b", re.I)

# 實機常見的輸出格式，故意帶 IP、MAC、介面名稱，確認腳本不會原樣印出
SS_OUT = """\
LISTEN 0 4096 192.168.10.23:1883 0.0.0.0:*
LISTEN 0 128 0.0.0.0:22 0.0.0.0:*
LISTEN 0 4096 [fe80::1%eth0]:8080 [::]:*
LISTEN 0 4096 127.0.0.1:11434 0.0.0.0:*
"""
PS_OUT = "mosquitto\npython3\npython3\nollama\nsshd\n"
TEGRASTATS_OUT = "RAM 3120/7620MB (lfb 2x4MB) SWAP 0/3810MB CPU [5%@729,3%@729] GR3D_FREQ 0% tj@41C"


def fake(bin_dir, name, body):
    path = bin_dir / name
    path.write_text("#!/bin/sh\n" + body + "\n", encoding="utf-8")
    path.chmod(0o755)
    return path


def fake_python(bin_dir, name, version, ensurepip=True):
    """回應腳本裡三種 `-c` 呼叫：印版本、和 3.11 比、import ensurepip。"""
    major, minor, _ = (int(x) for x in version.split("."))
    old = "0" if (major, minor) >= (3, 11) else "1"
    return fake(bin_dir, name, f"""case "$2" in
  *print*) echo {version} ;;
  *sys.exit*) exit {old} ;;
  *ensurepip*) exit {0 if ensurepip else 1} ;;
esac""")


@pytest.fixture
def jetson(tmp_path):
    """假的 Jetson：系統檔、Jetson 指令與外網。各測試再依情境換掉 python 或 curl。"""
    root = tmp_path / "root"
    (root / "proc" / "device-tree").mkdir(parents=True)
    (root / "proc" / "device-tree" / "model").write_bytes(b"NVIDIA Jetson Orin Nano Developer Kit\0")
    (root / "proc" / "meminfo").write_text("MemTotal:        7812000 kB\nMemAvailable:    4100000 kB\n")
    (root / "etc").mkdir()
    (root / "etc" / "nv_tegra_release").write_text("# R36 (release), REVISION: 4.3, GCID: 38968081, BOARD: generic\n")
    (root / "etc" / "os-release").write_text('PRETTY_NAME="Ubuntu 22.04.5 LTS"\n')
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake(bin_dir, "nvpmodel", "echo 'NV Power Mode: 15W'; echo 0")
    fake(bin_dir, "tegrastats", f"echo '{TEGRASTATS_OUT}'")
    fake(bin_dir, "ss", f"cat <<'EOF'\n{SS_OUT}EOF")
    fake(bin_dir, "ps", f"printf '{PS_OUT}'")
    fake(bin_dir, "systemctl", 'case "$2" in mosquitto) echo active ;; *) echo inactive; exit 3 ;; esac')
    fake(bin_dir, "dpkg-query", "printf 6.2.1+b38")
    fake(bin_dir, "curl", "exit 0")
    fake(bin_dir, "uv", "exit 2")
    fake_python(bin_dir, "python3", "3.10.12")
    for name in ["python3.11", "python3.12", "python3.13"]:
        fake(bin_dir, name, "exit 127")
    return root, bin_dir


def run(*args, root=None, bin_dir=None):
    env = dict(os.environ)
    if root is not None:
        env["JETSON_CHECK_ROOT"] = str(root)
    if bin_dir is not None:
        env["PATH"] = f"{bin_dir}{os.pathsep}{env['PATH']}"
    script = os.path.join(os.path.dirname(__file__), "..", "scripts", "jetson_check.sh")
    return subprocess.run(["bash", script, *args], capture_output=True, text=True, timeout=60, check=False, env=env)


def assert_no_identifiers(out):
    assert not IPV4.search(out), IPV4.search(out)
    assert not MAC.search(out)
    assert "eth0" not in out
    host = subprocess.run(["uname", "-n"], capture_output=True, text=True, check=False).stdout.strip()
    if len(host) >= 4:
        assert host not in out


def test_runs_on_plain_linux_offline():
    r = run("--offline")
    assert r.returncode == 0, r.stderr
    for title in ["型號與系統（B1）", "記憶體", "磁碟", "Python 與工具", "正在執行的服務（B2 的線索）", "外網（B5）", "摘要"]:
        assert f"## {title}" in r.stdout
    assert "略過（--offline）" in r.stdout
    assert_no_identifiers(r.stdout)


def test_jetson_specs_services_and_network(jetson):
    root, bin_dir = jetson
    fake_python(bin_dir, "python3.11", "3.11.9")
    r = run(root=root, bin_dir=bin_dir)
    assert r.returncode == 0, r.stderr
    out = r.stdout
    assert "- 型號：NVIDIA Jetson Orin Nano Developer Kit" in out
    assert "- L4T：# R36 (release), REVISION: 4.3" in out
    assert "- JetPack（nvidia-jetpack 套件）：6.2.1+b38" in out
    assert "- 作業系統：Ubuntu 22.04.5 LTS" in out
    assert "NV Power Mode: 15W" in out
    assert "- 總共／目前可用：7628 MiB / 4003 MiB" in out
    assert "- python3：3.10.12（低於 3.11，本 repo 不能用）" in out
    assert "- python3.11：3.11.9（可用）" in out
    assert "- python3.11 建 venv：可以" in out
    assert "- mosquitto：執行中" in out
    assert "- 有在聽的 TCP 埠：22 1883 8080 11434" in out
    assert "- MQTT：" in out
    assert "mosquitto×1 ollama×1 python3×2" in out
    assert "tj@41C" in out
    assert "- https://pypi.org：連得到" in out
    assert "要實際 pip install 一次才算數" in out
    assert "不是 Jetson" not in out
    assert_no_identifiers(out)


def test_python_without_venv_support_is_flagged(jetson):
    root, bin_dir = jetson
    fake_python(bin_dir, "python3.11", "3.11.9", ensurepip=False)
    out = run("--offline", root=root, bin_dir=bin_dir).stdout
    assert "- python3.11 建 venv：不行：缺 ensurepip" in out
    assert "python3.11 不能建 venv" in out


def test_python_from_uv_is_found_without_printing_its_path(jetson, tmp_path):
    root, bin_dir = jetson
    uv_dir = tmp_path / "home" / "radar-user" / ".local" / "share" / "uv"
    uv_dir.mkdir(parents=True)
    uv_py = fake_python(uv_dir, "python3.12", "3.12.7")
    fake(bin_dir, "uv", f"echo {uv_py}")
    out = run("--offline", root=root, bin_dir=bin_dir).stdout
    assert "- uv 管理的 Python：3.12.7（可用）" in out
    assert "- Python 3.11 以上：uv 管理的 Python" in out
    assert "radar-user" not in out


def test_no_python_and_no_network_are_flagged(jetson):
    root, bin_dir = jetson
    fake(bin_dir, "curl", "exit 7")
    out = run(root=root, bin_dir=bin_dir).stdout
    assert "- Python 3.11 以上：沒有" in out
    assert "沒有 Python 3.11 以上：要另外裝" in out
    assert "- https://pypi.org：連不到" in out
    assert "要用離線安裝" in out


def test_unreadable_memory_is_not_shown_as_zero(jetson):
    root, bin_dir = jetson
    (root / "proc" / "meminfo").unlink()
    out = run("--offline", root=root, bin_dir=bin_dir).stdout
    assert "- 總共／目前可用：讀不到 / 讀不到" in out
    assert "0 MiB" not in out


def test_missing_nvpmodel_permission(jetson):
    root, bin_dir = jetson
    fake(bin_dir, "nvpmodel", "exit 1")
    out = run("--offline", root=root, bin_dir=bin_dir).stdout
    assert "沒有權限或讀不到" in out


def test_unknown_option_is_usage_error():
    r = run("--fix")
    assert r.returncode == 2
    assert r.stderr.strip() == "用法：bash scripts/jetson_check.sh [--offline]"
