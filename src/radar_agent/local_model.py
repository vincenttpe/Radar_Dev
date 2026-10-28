"""在地模型的接法：命令列工具共用（radar-model-eval、radar-ask）。

三種接法：照抄原文的假模型（CI、試跑）、OpenAI 相容 API（llama.cpp server、Ollama 等）、
執行一個指令（prompt 從標準輸入送入）。模型名稱屬於中國開發的家族就拒絕（docs/adr/0003）。
"""
from __future__ import annotations

import argparse
import json
import shlex
import subprocess
import urllib.request
from typing import Any

from radar_agent.model_policy import banned_origin


class EchoModel:
    """照抄 prompt 裡的原文：檢查一定通過，用來確認流程與量測沒問題。"""

    name = "echo"

    def __call__(self, prompt: str) -> str:
        return prompt.rsplit("原文：\n", 1)[-1]


class HttpChatModel:
    """OpenAI 相容的 /chat/completions。只給本機或區網的模型伺服器用，刻意不走 proxy。"""

    def __init__(self, endpoint: str, model: str, *, timeout: float = 120, max_tokens: int = 512,
                 reasoning_effort: str | None = None):
        self.url = endpoint.rstrip("/") + "/chat/completions"
        self.model, self.timeout, self.max_tokens = model, timeout, max_tokens
        # 有思維鏈的模型（例如 Gemma 4）會先想再答，慢又會把 max_tokens 用完；Ollama 用 "none" 關掉（docs/20 第 3 步）。
        # 不是每個伺服器都認得這個參數，所以預設不送
        self.reasoning_effort = reasoning_effort
        self.name = model
        self.last_tokens: int | None = None
        self._opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def __call__(self, prompt: str) -> str:
        request: dict[str, Any] = {
            "model": self.model, "messages": [{"role": "user", "content": prompt}],
            "temperature": 0, "max_tokens": self.max_tokens,
        }
        if self.reasoning_effort is not None:
            request["reasoning_effort"] = self.reasoning_effort
        body = json.dumps(request).encode("utf-8")
        req = urllib.request.Request(self.url, data=body, headers={"Content-Type": "application/json"})
        with self._opener.open(req, timeout=self.timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        usage = data.get("usage") or {}
        self.last_tokens = usage.get("completion_tokens")
        return str(data["choices"][0]["message"]["content"])


class CommandModel:
    """執行指令，prompt 從標準輸入送進去，標準輸出當成回答；結束碼不是 0 就算模型錯誤。"""

    def __init__(self, cmd: str, *, timeout: float = 120):
        self.argv, self.timeout = shlex.split(cmd), timeout
        self.name = cmd

    def __call__(self, prompt: str) -> str:
        done = subprocess.run(self.argv, input=prompt, capture_output=True, text=True, encoding="utf-8",
                              timeout=self.timeout, check=True)
        return done.stdout


def add_model_args(ap: argparse.ArgumentParser, *, required: bool) -> None:
    which = ap.add_mutually_exclusive_group(required=required)
    which.add_argument("--echo", action="store_true", help="照抄原文的假模型（CI、試跑用）")
    which.add_argument("--endpoint", help="OpenAI 相容 API 的網址，例如 http://127.0.0.1:8080/v1")
    which.add_argument("--cmd", help="執行的指令；prompt 從標準輸入送入")
    ap.add_argument("--model", help="--endpoint 時的模型名稱（會記進追溯紀錄）")
    ap.add_argument("--timeout", type=float, default=120, help="每次呼叫的逾時秒數（預設 120）")
    ap.add_argument("--reasoning-effort", help="--endpoint 時送給伺服器的 reasoning_effort；用 Ollama 跑有思維鏈的模型（例如 Gemma 4）時給 none 關掉")


def model_from_args(args: argparse.Namespace) -> Any:
    """依命令列參數建立模型；都沒指定時回傳 None。參數不合或模型不能用時丟 ValueError。"""
    banned = banned_origin(args.model or args.cmd or "")
    if banned:
        raise ValueError(banned)
    if args.echo:
        return EchoModel()
    if args.endpoint:
        if not args.model:
            raise ValueError("--endpoint 要搭配 --model")
        return HttpChatModel(args.endpoint, args.model, timeout=args.timeout, reasoning_effort=args.reasoning_effort)
    if args.cmd is not None:  # 空字串也照指令處理：每次呼叫都記成模型錯誤，不讓整個工具當掉
        return CommandModel(args.cmd, timeout=args.timeout)
    return None
