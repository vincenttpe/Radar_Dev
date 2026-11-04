"""版本指紋：prompt 與採用規則的雜湊（2026-11-02 從 test_rewrite、test_ask 收到這裡）。

紀錄只記 prompt_version；規則改了卻沒升版，就分不出一筆結果是哪一套規則產生的（docs/02 原則 3）。
各測試用這裡的函式算出雜湊，和寫死的值比對：值變了就要升版，再更新測試裡的版本與雜湊。
"""
from __future__ import annotations

import ast
import hashlib
import inspect
import textwrap

from radar_agent.ask import Menu, build_prompt, build_verify_prompt
from radar_agent.rewrite import RewriteKind


def rule_digest(*parts: object) -> str:
    """prompt 與採用檢查的指紋：字串照原樣，函式取語法樹還原的程式碼（不含註解、docstring 與排版）。

    用 ast.unparse 而不是 ast.dump：ast.dump 的輸出在 3.11、3.12、3.13 之間不同（CI 跑 3.11 與 3.12）。
    output_check 的 check_output、wording_problems 不在指紋裡：報告與提問也共用，改了不代表改寫規則變了。
    """
    texts = []
    for part in parts:
        if callable(part):
            tree = ast.parse(textwrap.dedent(inspect.getsource(part)))
            for node in ast.walk(tree):
                if (isinstance(node, ast.FunctionDef) and node.body and isinstance(node.body[0], ast.Expr)
                        and isinstance(node.body[0].value, ast.Constant) and isinstance(node.body[0].value.value, str)):
                    node.body = node.body[1:] or [ast.Pass()]
            texts.append(ast.unparse(tree))
        else:
            texts.append(repr(part))
    return hashlib.sha256("\n".join(texts).encode("utf-8")).hexdigest()[:16]


def menu_digest(menu: Menu) -> str:
    """一份選單的 prompt 指紋：題目套進 build_prompt 的文字（2026-11-02 起每份選單都用這個算，不另外手寫）。"""
    scope = menu.scope
    text = build_prompt("{提問}", menu.questions, scope.note if scope else "")
    if scope:  # 規則與第二次確認也決定讀者拿到哪一題，一起算
        text += scope.refuse.pattern + build_verify_prompt("{提問}", menu.questions[0], scope.verify) + repr(scope.known)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def kind_digest(kind: RewriteKind) -> str:
    """一種文字的改寫規則指紋：prompt 加上 kind.rules 列出的程式與常數（規則清單只在 RewriteKind 寫一次）。"""
    return rule_digest(kind.prompt("{原句}"), *kind.rules)
