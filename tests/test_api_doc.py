"""docs/api/jetson-api.openapi.yaml 要和 radar_agent.jetson_api 一致（數字、網址、題號），範例要真的跑得過。"""
from pathlib import Path

import fake_dashboard
import yaml

from radar_agent import jetson_api
from radar_agent.dashboard_link import KINDS, handle
from radar_agent.ask import MAX_ASKED
from radar_agent.event_ask import EVENT_QUESTIONS

DOC = Path(__file__).resolve().parent.parent / "docs" / "api" / "jetson-api.openapi.yaml"
SPEC = yaml.safe_load(DOC.read_text(encoding="utf-8"))
SCHEMAS = SPEC["components"]["schemas"]


def test_paths_and_port_match_the_server():
    assert set(SPEC["paths"]) == {jetson_api.JOBS_PATH, jetson_api.HEALTH_PATH}
    assert set(SPEC["paths"][jetson_api.JOBS_PATH]) == {"post"} and set(SPEC["paths"][jetson_api.HEALTH_PATH]) == {"get"}
    assert all(s["url"].endswith(f":{jetson_api.PORT}") for s in SPEC["servers"])


def test_limits_match_the_server():
    body = SPEC["paths"][jetson_api.JOBS_PATH]["post"]["requestBody"]["description"]
    assert f"{jetson_api.MAX_BODY} 位元組" in body and f"{jetson_api.MAX_RECORDS} 筆" in body
    assert SCHEMAS["JobRequest"]["properties"]["records"]["maxItems"] == jetson_api.MAX_RECORDS
    assert f"{jetson_api.UNAUTHORIZED_READ // 1024} KB" in SPEC["components"]["responses"]["Unauthorized"]["description"]
    busy = SPEC["paths"][jetson_api.JOBS_PATH]["post"]["responses"]["503"]
    assert f"{jetson_api.BUSY_WAIT:g} 秒" in busy["description"] and busy["headers"]["Retry-After"]["schema"]["const"] == "5"
    question = SCHEMAS["JobRequest"]["properties"]["question"]
    assert "maxLength" not in question and f"超過 {MAX_ASKED} 字時仍回 200" in question["description"]
    assert f"最多 {jetson_api.MAX_CONNECTIONS} 條" in SPEC["info"]["description"]
    assert f"至少 {jetson_api.MIN_TOKEN} 個字元" in SPEC["components"]["securitySchemes"]["token"]["description"]


def test_every_status_code_the_server_sends_is_documented():
    """jetson_api.py 裡出現的狀態碼都要寫進文件；兩支 API 的 401 都附 WWW-Authenticate。"""
    import re
    source = Path(jetson_api.__file__).read_text(encoding="utf-8")
    sent = {int(c) for c in re.findall(r"_(?:send|refuse)\((\d{3})", source)}
    jobs = SPEC["paths"][jetson_api.JOBS_PATH]["post"]["responses"]
    health = SPEC["paths"][jetson_api.HEALTH_PATH]["get"]["responses"]
    assert sent <= {int(c) for c in jobs} | {int(c) for c in health}
    assert jobs["401"] == health["401"] == {"$ref": "#/components/responses/Unauthorized"}


def test_kinds_and_question_ids_match_the_code():
    props = SCHEMAS["JobRequest"]["properties"]
    assert props["kind"]["enum"] == list(KINDS)
    assert props["question_id"]["enum"] == [q.id for q in EVENT_QUESTIONS]


def test_every_example_request_is_answered_done():
    sample = fake_dashboard.read_records(fake_dashboard.page_data.SAMPLE)
    examples = SPEC["paths"][jetson_api.JOBS_PATH]["post"]["requestBody"]["content"]["application/json"]["examples"]
    assert set(examples) == {"report", "ask", "pick"}
    for name, example in examples.items():
        job = dict(example["value"])
        if name == "ask":
            continue  # 打字提問要模型；沒有模型時回 notice，另由 test_jetson_api 測
        # 範例只放一筆紀錄示意；這裡換成模擬上午整份，確認同樣的需求欄位真的產生得出文字
        got = handle(job, jetson_api._Given({**job, "records": sample}), None)
        assert got["status"] == "done", (name, got["problems"])
        assert set(got) == set(SCHEMAS["JobResult"]["required"])
        assert set(SCHEMAS[{"report": "CaregiverReport", "pick": "EventAsk"}[name]]["required"]) <= set(got["result"])
