"""在地模型來源限制（docs/adr/0003）：不用中國開發、或以其為基底的模型。只透過 model_policy.banned_origin() 驗證。

名稱檢查只是第一道提醒；正式選型仍要看 model card 的開發者與 base model。
"""
import pytest

from radar_agent.model_policy import banned_origin


@pytest.mark.parametrize("name", [
    "qwen2.5-1.5b-instruct-q4_k_m",
    "Qwen/Qwen2.5-0.5B-Instruct",
    "ollama run qwen2.5:3b",
    "deepseek-r1-distill-llama-8b",   # 蒸餾自 DeepSeek，也算
    "THUDM/chatglm3-6b",
    "glm-4-9b-chat",
    "baichuan2-7b",
    "internlm2_5-7b-chat",
    "openbmb/MiniCPM3-4B",
    "01-ai/Yi-1.5-6B-Chat",
    "hunyuan-7b",
])
def test_chinese_model_families_are_refused(name):
    assert banned_origin(name)


@pytest.mark.parametrize("name", [
    "llama3.2:3b",
    "gemma3:4b",
    "MediaTek-Research/Llama-Breeze2-3B-Instruct",
    "taide/Llama3-TAIDE-LX-8B-Chat-Alpha1",
    "phi4-mini",
    "granite3.1-dense:2b",
    "echo",
])
def test_other_models_pass_the_name_check(name):
    assert banned_origin(name) is None
