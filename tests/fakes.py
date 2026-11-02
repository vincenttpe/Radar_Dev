"""測試共用的在地模型替身（Model seam 的第二個 adapter；正式的是 local_model 的 HttpChatModel 等）。

依 prompt 內容計算回覆的（test_model_eval 的 Oracle、Padding）留在各自的測試檔；照抄原文的用正式的 local_model.EchoModel。
"""
from __future__ import annotations


class FakeModel:
    """依序回 replies（用完就一直回最後一段），或丟出指定的例外；記下收到的 prompt。"""

    def __init__(self, *replies: str, error: BaseException | None = None, name: str = "fake-0.1") -> None:
        self.replies, self.error, self.name = list(replies) or [""], error, name
        self.prompts: list[str] = []

    def __call__(self, prompt: str) -> str:
        self.prompts.append(prompt)
        if self.error is not None:
            raise self.error
        return self.replies[min(len(self.prompts), len(self.replies)) - 1]
