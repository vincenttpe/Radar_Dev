"""產生真人試測用紙 demo/usability_kit.html（可列印；docs/17_介面易用性評估.md「評估方式」）。

任務、做對的標準、第一關的三個問題與 SUS 題目都從 docs/17 讀，不在這裡另寫一份：docs/17 改了，重新產生就跟著改。
用紙分兩種：主持人用的記錄表（每種讀者一頁，有做對的標準）與受測者填的 SUS 量表（沒有標準答案）。
受測者只寫代號（例如 P1），不寫姓名（docs/17「安全」、REVIEW.md 3.3）。

重新產生（tests/test_usability_kit.py 會檢查）：
    python demo/usability_kit.py
"""
from __future__ import annotations

import html
import re
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
SOURCE = REPO / "docs" / "17_介面易用性評估.md"
OUT = HERE / "usability_kit.html"

# 讀者 → (看板網址的 #, 任務代號開頭)；順序就是用紙的順序
ROLES = {"居服員": ("caregiver", "C"), "家屬": ("family", "F"), "系統管理人員": ("admin", "A"), "維運後台": ("ops", "O")}
SUS_ITEMS = 10


def _section(text: str, heading: str) -> str:
    """從 heading 那一行到下一個同級或更高級的標題。"""
    level = heading.split(" ", 1)[0]
    start = text.index(heading + "\n")
    rest = text[start + len(heading) + 1:]
    stop = re.search(rf"^#{{1,{len(level)}}} ", rest, re.M)
    return rest[:stop.start()] if stop else rest


def load_plan(path: Path = SOURCE) -> dict[str, object]:
    """docs/17 的試測內容：第一關的問題、各讀者的任務、SUS 題目。格式不對就停下來，不產生缺頁的用紙。"""
    text = path.read_text(encoding="utf-8")
    method = _section(text, "## 評估方式：任務測試加 SUS 量表")
    first = method.split("**第一關（10 秒判讀）**", 1)[1].split("\n\n", 1)[0]
    first_pass = re.findall(r"^- (.+)$", first, re.M)
    tasks: dict[str, list[dict[str, str]]] = {r: [] for r in ROLES}
    for tid, role, task, done in re.findall(r"^\| ([A-Z]\d+) \| (\S+) \| (.+?) \| (.+?) \|$", _section(text, "### 任務"), re.M):
        if role not in ROLES or not tid.startswith(ROLES[role][1]):
            raise ValueError(f"docs/17 的任務 {tid} 讀者對不上：{role}")
        tasks[role].append({"id": tid, "task": task, "done": done})
    for role, rows in tasks.items():
        if not rows:
            raise ValueError(f"docs/17 沒有 {role} 的任務")
        rows.sort(key=lambda r: int(r["id"][1:]))
    sus = re.findall(r"^\d+\. (.+)$", _section(text, "### SUS 量表（System Usability Scale）"), re.M)
    if len(first_pass) != 3 or len(sus) != SUS_ITEMS:
        raise ValueError(f"docs/17 的第一關問題應有 3 題、SUS 應有 {SUS_ITEMS} 題：{len(first_pass)}、{len(sus)}")
    return {"first_pass": first_pass, "tasks": tasks, "sus": sus}


def _inline(text: str) -> str:
    """docs 表格裡的文字：跳脫 HTML，保留 `程式碼` 與 **粗體**。"""
    out = html.escape(text)
    out = re.sub(r"`(.+?)`", r"<code>\1</code>", out)
    return re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", out)


def _blank(label: str, width: str = "8em") -> str:
    return f'<span class="field"><span>{label}</span><span class="line" style="width:{width}"></span></span>'


def page(plan: dict[str, object]) -> str:
    first_pass: list[str] = plan["first_pass"]  # type: ignore[assignment]
    tasks: dict[str, list[dict[str, str]]] = plan["tasks"]  # type: ignore[assignment]
    sus: list[str] = plan["sus"]  # type: ignore[assignment]
    head_fields = "".join([_blank("受測者代號", "5em"), _blank("日期", "7em"),
                           '<span class="field">先用 <span class="box"></span> 官網 <span class="box"></span> 試做頁</span>'])
    sheets = []
    for role, (anchor, _) in ROLES.items():
        rows = []
        for t in tasks[role]:
            for i, ui in enumerate(("官網", "試做頁")):
                lead = (f'<td rowspan="2" class="id">{t["id"]}</td><td rowspan="2">{_inline(t["task"])}</td>'
                        f'<td rowspan="2" class="done">{_inline(t["done"])}</td>') if i == 0 else ""
                rows.append(f'<tr>{lead}<td class="ui">{ui}</td><td></td><td class="check"><span class="box"></span> 對'
                            f' <span class="box"></span> 錯</td><td class="check"><span class="box"></span></td><td></td></tr>')
        checks = "".join(f'<li><span class="pair">官網 <span class="box"></span> 試做頁 <span class="box"></span></span>{_inline(q)}</li>'
                         for q in first_pass)
        sheets.append(f"""<section class="sheet">
  <header><p class="kicker">主持人記錄表・有標準答案，不要給受測者看</p><h2>{role}</h2>
  <p class="url">試做頁：<code>demo/dashboard.html#{anchor}</code>（線上預覽網址加 <code>#{anchor}</code>）</p></header>
  <div class="fields">{head_fields}</div>
  <h3>第一關：打開畫面 10 秒內說出三件事</h3>
  <ul class="checks">{checks}</ul>
  <p class="note">兩種介面各做一次，說得出來的在那個介面打勾。說不出來，就表示第一屏還要改。</p>
  <h3>任務</h3>
  <p class="note">每個任務開始前先重新整理頁面。秒數從看到題目算到說出答案或完成操作。</p>
  <table><thead><tr><th>#</th><th>任務</th><th>做對的標準</th><th>介面</th><th class="w-sec">秒數</th><th>做對？</th><th class="w-help">求助</th><th class="w-note">備註</th></tr></thead>
  <tbody>{"".join(rows)}</tbody></table>
</section>""")
    scale = "".join(f'<span class="opt"><span class="circle">{n}</span></span>' for n in range(1, 6))
    items = "".join(f'<li><span class="q"><span class="n">{i}.</span>{_inline(q)}</span><span class="scale">{scale}</span></li>'
                    for i, q in enumerate(sus, 1))
    sus_sheet = f"""<section class="sheet">
  <header><p class="kicker">受測者填寫・每種介面做完各填一張</p><h2>使用感受問卷（SUS）</h2></header>
  <div class="fields">{_blank("受測者代號", "5em")}<span class="field">介面 <span class="box"></span> 官網 <span class="box"></span> 試做頁</span></div>
  <p class="note">每一題圈一個數字：1 是非常不同意，5 是非常同意。不用想太久，憑第一印象就好。</p>
  <p class="ends"><span>1 非常不同意</span><span>5 非常同意</span></p>
  <ol class="sus">{items}</ol>
  <div class="score"><p class="kicker">主持人計分（受測者不用填）</p>
  <p>單數題：選的分數 − 1；雙數題：5 − 選的分數。十題加總 × 2.5 ＝ 0～100 分。</p>
  <p>{_blank("加總", "4em")} × 2.5 ＝ {_blank("SUS 分數", "4em")}</p></div>
  <p class="note">譯文是本 repo 自己翻的，不是經過驗證的正式中文版（docs/17）。人數少，只能看兩種介面差得多不多，不能當驗收數字。</p>
</section>"""
    cover = f"""<section class="sheet cover">
  <header><p class="kicker">介面易用性評估・模擬資料</p><h1>介面試測用紙</h1>
  <p>內容產生自 <code>docs/17_介面易用性評估.md</code>；那份文件改了，執行 <code>python demo/usability_kit.py</code> 重新產生。</p></header>
  <h3>怎麼進行</h3>
  <ul class="plain">
    <li>每種讀者找 1～2 人。每人用官網 Dashboard 和試做頁各做一次同一組任務；一半的人先用官網，另一半先用試做頁。</li>
    <li>每個畫面先做「第一關」，再做任務。兩種介面都做完，各填一張使用感受問卷（SUS）。</li>
    <li>每個任務開始前先重新整理頁面：試做頁上示意的接手與處置只存在分頁裡，會影響下一個任務。</li>
    <li>全部用模擬資料。記錄表與問卷只寫受測者代號（例如 P1），不寫姓名。</li>
    <li>還沒拿到官網 Dashboard 的畫面時，可以先只測試做頁，確認任務本身設計得合不合理。</li>
  </ul>
  <h3>用紙</h3>
  <ul class="plain">{"".join(f"<li>主持人記錄表：{r}（{len(tasks[r])} 個任務）</li>" for r in ROLES)}<li>使用感受問卷（SUS）：每人每種介面一張</li></ul>
</section>"""
    return TEMPLATE.replace("<!--SHEETS-->", cover + "".join(sheets) + sus_sheet)


TEMPLATE = """<!doctype html>
<html lang="zh-Hant">
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>介面試測用紙</title>
<style>
:root { --ink: #16232a; --muted: #56676f; --line: #78878e; --soft: #d3dce0; --bg: #eef2f3; --paper: #ffffff; }
@media (prefers-color-scheme: dark) { :root { --ink: #e2eaed; --muted: #94a5ad; --line: #667a83; --soft: #2a3940; --bg: #0f161a; --paper: #172227; } }
* { box-sizing: border-box; }
body { margin: 0; background: var(--bg); color: var(--ink); font: 14px/1.6 "Noto Sans TC", "PingFang TC", "Microsoft JhengHei", sans-serif; }
.sheet { background: var(--paper); max-width: 210mm; margin: 16px auto; padding: 14mm 12mm; border: 1px solid var(--soft); }
h1 { font-size: 24px; margin: 4px 0 8px; } h2 { font-size: 20px; margin: 2px 0 4px; } h3 { font-size: 15px; margin: 18px 0 6px; }
.kicker { font-size: 12px; letter-spacing: .08em; color: var(--muted); margin: 0; }
.url, .note { color: var(--muted); font-size: 13px; margin: 4px 0; }
code { font-family: ui-monospace, Menlo, monospace; font-size: .92em; }
.fields { display: flex; flex-wrap: wrap; gap: 8px 24px; margin: 10px 0; }
.field { display: inline-flex; align-items: flex-end; gap: 6px; }
.line { display: inline-block; border-bottom: 1px solid var(--line); height: 1.2em; }
.box { display: inline-block; width: 13px; height: 13px; border: 1.5px solid var(--line); border-radius: 2px; vertical-align: -2px; }
ul.checks, ul.plain { margin: 0; padding-left: 0; list-style: none; display: grid; gap: 4px; }
ul.plain { padding-left: 1.2em; list-style: disc; }
table { width: 100%; border-collapse: collapse; font-size: 12.5px; }
th, td { border: 1px solid var(--line); padding: 5px 6px; text-align: left; vertical-align: top; }
th { font-weight: 500; color: var(--muted); }
td.id { font-family: ui-monospace, Menlo, monospace; white-space: nowrap; }
td.done { color: var(--muted); }
td.ui, td.check { white-space: nowrap; }
.w-sec { width: 4.5em; } .w-note { width: 8em; }
ol.sus { padding: 0; list-style: none; display: grid; gap: 10px; }
ol.sus .n { display: inline-block; width: 1.8em; color: var(--muted); }
.ends { display: flex; justify-content: flex-end; gap: 70px; font-size: 12px; color: var(--muted); margin: 8px 0 0; }
.pair { display: inline-flex; gap: 4px; align-items: center; font-size: 12px; color: var(--muted); margin-right: 12px; }
.w-help { width: 3.2em; white-space: nowrap; }
ol.sus li { display: grid; grid-template-columns: minmax(0, 1fr) auto; gap: 12px; align-items: center; }
.scale { display: inline-flex; gap: 8px; }
.circle { display: inline-grid; place-items: center; width: 26px; height: 26px; border: 1.5px solid var(--line); border-radius: 50%; font-size: 12px; }
.score { border-top: 1px dashed var(--line); margin-top: 16px; padding-top: 8px; }
.score p { margin: 4px 0; }
@media (max-width: 640px) { .sheet { margin: 0; border: 0; padding: 16px; } table { display: block; overflow-x: auto; } ol.sus li { grid-template-columns: minmax(0, 1fr); } }
@media print {
  :root { --ink: #000; --muted: #333; --line: #555; --soft: #999; --bg: #fff; --paper: #fff; }
  @page { size: A4; margin: 12mm; }
  body { background: #fff; }
  .sheet { margin: 0; padding: 0; border: 0; max-width: none; break-after: page; }
  .sheet:last-child { break-after: auto; }
  tr { break-inside: avoid; }
}
</style>
<main>
<!--SHEETS-->
</main>
</html>
"""


if __name__ == "__main__":
    OUT.write_text(page(load_plan()), encoding="utf-8")
    print(f"寫入 {OUT}")
