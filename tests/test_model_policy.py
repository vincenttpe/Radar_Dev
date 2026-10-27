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
    "OpenGVLab/InternVL2_5-4B",
    "InternViT-300M-448px",
    "MediaTek-Research/Llama-Breeze2-3B-Instruct",   # 視覺部分是 InternViT（2026-10-27 排除）
    "llama-breeze2-8b-instruct-q4_k_m",
    "breeze_2-3b.gguf",
    "breeze-v2-3b",
])
def test_chinese_model_families_are_refused(name):
    assert banned_origin(name)


@pytest.mark.parametrize("name", [
    "llama3.2:3b",
    "gemma3:4b",
    "MediaTek-Research/Breeze-7B-Instruct-v1_0",   # 第一代 Breeze 沒有視覺部分，不在這次排除範圍
    "gemma4:e4b",
    "seabreeze2-1b",   # 名稱裡碰巧有 breeze2，不是 Breeze2
    "taide/Gemma-3-TAIDE-12b-Chat",
    "taide/Llama3-TAIDE-LX-8B-Chat-Alpha1",
    "phi4-mini",
    "granite3.1-dense:2b",
    "echo",
])
def test_other_models_pass_the_name_check(name):
    assert banned_origin(name) is None


def test_breeze2_reason_says_it_contains_a_chinese_part():
    reason = banned_origin("MediaTek-Research/Llama-Breeze2-3B-Instruct")
    assert reason and "InternViT" in reason and "含有中國開發的部分" in reason
