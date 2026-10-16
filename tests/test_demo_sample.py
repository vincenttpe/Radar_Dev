"""Demo 用模擬資料 data/samples_demo/（docs/11 G2）。

檔案要能由 generate.py 重現；radar-caregiver 跑出來的就是 Demo 要展示的內容，用語一改這裡就會失敗。
"""
import importlib.util
import json
import re

import pytest

from radar_agent.caregiver import main


@pytest.fixture(scope="module")
def demo_dir(repo):
    return repo / "data" / "samples_demo"


@pytest.fixture(scope="module")
def generator(demo_dir):
    spec = importlib.util.spec_from_file_location("demo_generate", demo_dir / "generate.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_committed_file_is_exactly_what_generate_py_produces(demo_dir, generator):
    committed = (demo_dir / "bathroom_morning.jsonl").read_text(encoding="utf-8")
    assert committed == generator.to_jsonl(generator.generate()), "改了 generate.py 要重新產生 bathroom_morning.jsonl"


def test_demo_data_has_no_real_device_identity(demo_dir):
    text = (demo_dir / "bathroom_morning.jsonl").read_text(encoding="utf-8")
    assert not re.search(r"(?i)\b[0-9a-f]{2}([:-][0-9a-f]{2}){5}\b|\b[0-9a-f]{12}\b", text), "不能出現像 MAC 的字串"
    topics = {json.loads(line)["topic"] for line in text.splitlines()}
    assert topics == {"radar10/DEMO-SIM-01"}


def test_caregiver_output_is_what_the_demo_shows(demo_dir, capsys):
    code = main([str(demo_dir / "bathroom_morning.jsonl"), "--place", "1 號廁所"])
    assert (code, capsys.readouterr().out) == (0, """\
【事件摘要】
1 號廁所 10/14：有人使用 5 次，最長一次 22 分鐘。在線期間跌倒警報 2 次（08:32、09:16）。\
在線期間主動呼救 1 次（10:03）。語音回覆沒事 1 次。在線期間久待警報 1 次（10:45）。11:10–11:25 裝置斷線，這段時間無法判斷是否有跌倒。

【處置提示】
08:32 請立即到場確認。
08:32 長者回覆沒事，警報已解除；可視情況前往關心。
09:16 長者回覆需要協助，請立即到場。
09:19 已偵測到起身，警報已解除。
10:03 長者主動呼救，請立即到場。
10:05 已偵測到起身，警報已解除。
10:45 停留超過設定時間，請前往查看。
11:10–11:25 這段時間無法判斷是否有跌倒，請通知資訊人員。

【照護紀錄草稿】
08:32 1 號廁所雷達偵測到疑似跌倒，語音詢問 10 秒未獲回應。
08:32 長者語音回覆沒事，警報解除。
09:16 1 號廁所雷達偵測到疑似跌倒，長者語音回覆需要協助。
09:19 偵測到長者起身，警報解除。
10:03 1 號廁所偵測到主動呼救。
10:05 偵測到長者起身，警報解除。
10:45 1 號廁所停留超過設定時間（久待警報）。
11:10–11:25 裝置斷線，期間狀況不明。
""")
