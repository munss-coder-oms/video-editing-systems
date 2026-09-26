"""steps.json → index.html (미리보기 쪽). 그림은 img/…png를 상대 경로로.

쪽의 약속: <html>·<head>·<body> 없이 <title>과 <style>로 시작한다 (올리는 곳이 감싼다). 바깥 스크립트와 그림은
쓰지 않고, 글꼴만 Google Fonts에서 (없으면 맑은 고딕 등으로). 밝은/어두운 모양 둘 다, 폭 400px 휴대폰에서도
옆으로 밀리지 않게. 스크립트가 없어도 모든 단계가 차례로 보이고, 스크립트가 있으면 한 단계씩 넘겨 본다
(← → 키, #s12 같은 주소, 마지막으로 본 단계 기억).
"""

from __future__ import annotations

import html
import json
import re
from pathlib import Path
from typing import Any, Dict, List

FONTS = ("https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500"
         "&family=IBM+Plex+Sans+KR:wght@400;500;600&display=swap")
LEDE = "설치 없이 보는 실제 도우미 창 화면이에요. 리졸브 안의 결과는 흉내 그림이에요."
ASK = "다르게 보이거나 헷갈리는 곳이 있으면 스레드에 단계 번호와 함께 적어 주세요 (예: \"12번: 버튼 1 아래 글이 달라요\")."
ISSUE_TITLE = "미리보기를 만들며 찾은 문제"
ISSUES_SUM = "미리보기를 만들며 찾은 창의 문제가 적힌 단계:"
KIND_LABELS = {"window": "도우미 창", "menu": "메뉴", "dialog": "묻는 창", "card": "카드만 크게",
               "timeline": "흉내 그림 · 리졸브 화면 아님"}
FOOT = (
    "창 그림은 진짜 도우미 창을 화면 없이 띄워 찍은 것이라, 글꼴이 윈도우(맑은 고딕)와 조금 달라요.",
    "리졸브 대신 가짜 리졸브와 7분짜리 흉내 녹화로 찍었어요. 표시 수와 시각은 실제 영상에서 달라져요.",
    "카드와 영수증의 시각은 이 미리보기를 만든 때예요.",
)

CSS = """
:root {
  --bg: #f2f4f8; --surface: #ffffff; --surface-2: #e9edf4; --text: #1a1d24; --muted: #586072;
  --line: #d5dbe6; --accent: #2563eb; --accent-ink: #1c4fc0; --accent-soft: #e2eafc;
  --note-bg: #fff5da; --note-line: #e0a21a; --note-ink: #5c4200;
  --issue-bg: #fdeeee; --issue-line: #d14343; --issue-ink: #7d1c1c;
  --device: #1e1f22; --device-line: #34363c; --device-ink: #a9abb0; --ui-bg: #ffffff; --ui-line: #b9c2d3;
  --shadow: 0 1px 2px rgba(20, 30, 60, .06), 0 8px 24px rgba(20, 30, 60, .08);
  --sans: "IBM Plex Sans KR", "Noto Sans KR", "Malgun Gothic", sans-serif;
  --mono: "IBM Plex Mono", ui-monospace, "Cascadia Mono", Consolas, monospace;
}
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) {
    color-scheme: dark;
    --bg: #131417; --surface: #1b1c20; --surface-2: #24262b; --text: #ececec; --muted: #a2a7b1;
    --line: #32353c; --accent: #7fb0ff; --accent-ink: #9cc2ff; --accent-soft: #1c2840;
    --note-bg: #2a2416; --note-line: #f0b429; --note-ink: #f2d48a;
    --issue-bg: #2c1718; --issue-line: #f07575; --issue-ink: #f6bcbc;
    --device: #0d0e10; --device-line: #2c2e34; --device-ink: #9a9ea8; --ui-bg: #2a2b2f; --ui-line: #4a4d55;
    --shadow: 0 1px 2px rgba(0, 0, 0, .4), 0 8px 24px rgba(0, 0, 0, .35);
  }
}
:root[data-theme="dark"] {
  color-scheme: dark;
  --bg: #131417; --surface: #1b1c20; --surface-2: #24262b; --text: #ececec; --muted: #a2a7b1;
  --line: #32353c; --accent: #7fb0ff; --accent-ink: #9cc2ff; --accent-soft: #1c2840;
  --note-bg: #2a2416; --note-line: #f0b429; --note-ink: #f2d48a;
  --issue-bg: #2c1718; --issue-line: #f07575; --issue-ink: #f6bcbc;
  --device: #0d0e10; --device-line: #2c2e34; --device-ink: #9a9ea8; --ui-bg: #2a2b2f; --ui-line: #4a4d55;
  --shadow: 0 1px 2px rgba(0, 0, 0, .4), 0 8px 24px rgba(0, 0, 0, .35);
}
body { background: var(--bg); color: var(--text); font: 400 16px/1.65 var(--sans); }
.wrap { max-width: 1200px; margin: 0 auto; padding-inline: clamp(16px, 4vw, 40px); padding-block: 28px 56px; }
a { color: var(--accent-ink); }
:focus-visible { outline: 3px solid var(--accent); outline-offset: 2px; border-radius: 4px; }
.top { display: grid; gap: 6px; padding-bottom: 22px; margin-bottom: 26px; position: relative;
  border-bottom: 1px solid var(--line); }
.top::after { content: ""; position: absolute; left: 0; right: 0; bottom: -1px; height: 7px;
  background: repeating-linear-gradient(90deg, var(--line) 0 1px, transparent 1px 60px); }
.kicker { margin: 0; font: 500 12.5px/1.4 var(--mono); letter-spacing: .06em; color: var(--muted); }
h1 { margin: 0; font-size: clamp(26px, 4.5vw, 34px); line-height: 1.25; font-weight: 600; text-wrap: balance; }
.lede { margin: 4px 0 0; font-size: 17px; max-width: 62ch; }
.ask { margin: 0; color: var(--muted); max-width: 70ch; }
.meta { margin: 6px 0 0; font: 400 13px/1.5 var(--mono); color: var(--muted); font-variant-numeric: tabular-nums; }
.layout { display: grid; grid-template-columns: minmax(0, 1fr); gap: 28px; }
.toc { font-size: 14.5px; }
.toc h2 { margin: 18px 0 6px; font: 500 12px/1.4 var(--mono); letter-spacing: .06em; color: var(--muted); }
.toc h2:first-child { margin-top: 0; }
.toc ol { list-style: none; margin: 0; padding: 0; display: grid; gap: 1px; }
.toc a { display: grid; grid-template-columns: 2.1em 1fr auto; gap: 8px; align-items: baseline; padding: 5px 8px;
  border-radius: 6px; color: var(--text); text-decoration: none; }
.toc a:hover { background: var(--surface-2); }
.toc a[aria-current="step"] { background: var(--accent-soft); color: var(--accent-ink); font-weight: 500; }
.toc .n { font: 500 12.5px/1.6 var(--mono); color: var(--muted); font-variant-numeric: tabular-nums;
  text-align: right; }
.toc a[aria-current="step"] .n { color: var(--accent-ink); }
.toc .g { font: 400 11.5px/1.6 var(--mono); color: var(--muted); white-space: nowrap; }
.stepper { display: none; }
.step { background: var(--surface); border: 1px solid var(--line); border-radius: 12px; box-shadow: var(--shadow);
  padding: clamp(18px, 3vw, 30px); margin-bottom: 22px; min-width: 0; }
.step[hidden] { display: none; }
.step-top { display: flex; flex-wrap: wrap; align-items: baseline; gap: 6px 12px; margin-bottom: 4px; }
.step-no { font: 500 15px/1 var(--mono); color: var(--accent-ink); font-variant-numeric: tabular-nums;
  padding: 5px 9px; border-radius: 6px; background: var(--accent-soft); }
.step-no small { font-size: 12px; color: var(--muted); font-weight: 400; }
.step-meta { font: 400 12.5px/1.5 var(--mono); color: var(--muted); letter-spacing: .02em; }
.step h2 { margin: 6px 0 14px; font-size: clamp(21px, 3vw, 25px); line-height: 1.35; font-weight: 600;
  text-wrap: balance; }
.step h2:focus { outline: none; }
.facts { margin: 0 0 14px; display: grid; gap: 10px; max-width: 72ch; }
.fact { display: grid; grid-template-columns: 5.5em 1fr; gap: 12px; }
.fact dt { font: 500 12.5px/2 var(--mono); color: var(--muted); letter-spacing: .04em; }
.fact dd { margin: 0; overflow-wrap: anywhere; }
.note { margin: 0 0 18px; max-width: 72ch; padding: 10px 14px; border-left: 3px solid var(--note-line);
  background: var(--note-bg); color: var(--note-ink); border-radius: 0 8px 8px 0; font-size: 15px;
  overflow-wrap: anywhere; }
.issue { margin: 0 0 18px; max-width: 72ch; padding: 10px 14px; border-left: 3px solid var(--issue-line);
  background: var(--issue-bg); color: var(--issue-ink); border-radius: 0 8px 8px 0; font-size: 15px;
  overflow-wrap: anywhere; }
.issue h3 { margin: 0 0 4px; font: 600 12.5px/1.6 var(--mono); letter-spacing: .04em; }
.issue ul { margin: 0; padding-left: 1.1em; display: grid; gap: 6px; }
.issues-sum { margin: 2px 0 0; font-size: 14.5px; color: var(--issue-ink); max-width: 70ch; }
.issues-sum a { color: inherit; font-variant-numeric: tabular-nums; padding-inline: 2px; }
.toc .flag { color: var(--issue-line); font-size: 9px; vertical-align: 1px; }
.sr { position: absolute; width: 1px; height: 1px; overflow: hidden; clip: rect(0 0 0 0); white-space: nowrap; }
.ui { display: inline-block; padding: 0 7px; margin: 1px 1px; border: 1px solid var(--ui-line); border-radius: 5px;
  background: var(--ui-bg); font-size: .92em; line-height: 1.5; white-space: nowrap; }
.shots { display: grid; grid-template-columns: minmax(0, 1fr); gap: 18px; justify-items: start; }
.shot { margin: 0; max-width: 100%; min-width: 0; display: grid; gap: 8px; }
.frame { background: var(--device); border: 1px solid var(--device-line); border-radius: 10px; padding: 10px;
  max-width: 100%; }
.frame img { display: block; max-width: 100%; height: auto; border-radius: 3px; }
.shot figcaption { font-size: 13.5px; color: var(--muted); display: flex; flex-wrap: wrap; gap: 4px 10px; }
.kind { font: 500 11.5px/1.7 var(--mono); letter-spacing: .04em; color: var(--muted); }
.shot.timeline .kind { color: var(--note-ink); }
.pager { display: none; }
.foot { margin-top: 36px; padding-top: 18px; border-top: 1px solid var(--line); color: var(--muted);
  font-size: 13.5px; }
.foot ul { margin: 0; padding-left: 1.1em; display: grid; gap: 4px; max-width: 80ch; }
button, select { font: inherit; color: inherit; }
.nav-btn { display: inline-flex; align-items: center; gap: 6px; min-height: 40px; padding: 6px 14px;
  border: 1px solid var(--line); border-radius: 8px; background: var(--surface); cursor: pointer;
  max-width: 100%; }
.nav-btn:hover:not(:disabled) { border-color: var(--accent); }
.nav-btn:disabled { opacity: .45; cursor: default; }
.nav-btn .t { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.nav-btn.primary { background: var(--accent); border-color: var(--accent); color: #fff; }
:root[data-theme="dark"] .nav-btn.primary { color: #0d1526; }
@media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) .nav-btn.primary { color: #0d1526; } }
.count { font: 500 13px/1 var(--mono); color: var(--muted); font-variant-numeric: tabular-nums; white-space: nowrap; }
.paged .stepper { display: flex; align-items: center; gap: 8px; margin-bottom: 14px; flex-wrap: wrap; }
.paged .stepper select { flex: 1 1 12rem; min-width: 0; min-height: 40px; padding: 6px 10px;
  border: 1px solid var(--line); border-radius: 8px; background: var(--surface); }
.paged .pager { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }
.paged .pager .next { justify-self: end; }
.paged .step { margin-bottom: 16px; }
@media (min-width: 920px) {
  .layout { grid-template-columns: 16.5rem minmax(0, 1fr); gap: 36px; align-items: start; }
  .toc { position: sticky; top: calc(env(safe-area-inset-top, 0px) + 16px); max-height: calc(100vh - 32px);
    overflow: auto; padding-right: 4px; }
  .paged .stepper select { display: none; }
  .paged .stepper { justify-content: space-between; }
}
@media (max-width: 919px) {
  .paged .toc { display: none; }
}
@media (max-width: 520px) {
  .fact { grid-template-columns: 1fr; gap: 0; }
  .frame { padding: 6px; border-radius: 8px; }
  .nav-btn .t { display: none; }
}
@media (prefers-reduced-motion: no-preference) {
  .nav-btn, .toc a { transition: background-color .15s ease, border-color .15s ease; }
}
"""

JS = """
(function () {
  var steps = Array.prototype.slice.call(document.querySelectorAll('.step'));
  if (!steps.length) return;
  var root = document.documentElement;
  root.classList.add('paged');
  var wrap = document.querySelector('.wrap');
  var key = 'aih-preview-' + (wrap ? wrap.getAttribute('data-build') : '') + '-step';
  var links = Array.prototype.slice.call(document.querySelectorAll('.toc a'));
  var select = document.getElementById('jump');
  var counters = document.querySelectorAll('[data-count]');
  var cur = -1;
  var reduce = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  function fromHash() {
    var m = /^#s(\\d+)$/.exec(location.hash || '');
    if (!m) return -1;
    var i = parseInt(m[1], 10) - 1;
    return i >= 0 && i < steps.length ? i : -1;
  }
  function stored() {
    try {
      var v = parseInt(localStorage.getItem(key) || '', 10) - 1;
      return v >= 0 && v < steps.length ? v : -1;
    } catch (e) { return -1; }
  }
  function titleOf(i) { return steps[i].querySelector('h2').textContent; }
  function setNav(sel, i, word) {
    Array.prototype.forEach.call(document.querySelectorAll(sel), function (b) {
      var ok = i >= 0 && i < steps.length;
      b.disabled = !ok;
      var t = b.querySelector('.t');
      if (t) t.textContent = ok ? word + ' ' + (i + 1) + '. ' + titleOf(i) : '';
      b.setAttribute('aria-label', ok ? word + ' 단계: ' + (i + 1) + '. ' + titleOf(i) : word + ' 단계 없음');
    });
  }
  function show(i, focus, first) {
    if (i < 0 || i >= steps.length) return;
    cur = i;
    steps.forEach(function (s, j) { s.hidden = j !== i; });
    links.forEach(function (a) {
      if (a.getAttribute('href') === '#' + steps[i].id) a.setAttribute('aria-current', 'step');
      else a.removeAttribute('aria-current');
    });
    if (select) select.value = steps[i].id;
    Array.prototype.forEach.call(counters, function (c) { c.textContent = (i + 1) + ' / ' + steps.length; });
    setNav('[data-go="prev"]', i - 1, '이전');
    setNav('[data-go="next"]', i + 1, '다음');
    // 처음 열 때는 주소를 바꾸지 않는다 (불러오기가 끝날 때 브라우저가 #sN 자리로 쪽을 굴려 머리말이 가려진다)
    if (!first) { try { history.replaceState(null, '', '#' + steps[i].id); } catch (e) {} }
    try { localStorage.setItem(key, String(i + 1)); } catch (e) {}
    // 옆 목록이 따로 굴러갈 때만 그 안에서 지금 단계가 보이게 (쪽 자체는 굴리지 않는다)
    var cur_link = links.filter(function (a) { return a.hasAttribute('aria-current'); })[0];
    var toc = document.querySelector('.toc');
    if (cur_link && toc && toc.scrollHeight > toc.clientHeight + 1) {
      var r = cur_link.getBoundingClientRect(), tr = toc.getBoundingClientRect();
      if (r.top < tr.top) toc.scrollTop += r.top - tr.top - 8;
      else if (r.bottom > tr.bottom) toc.scrollTop += r.bottom - tr.bottom + 8;
    }
    if (focus) {
      var h = steps[i].querySelector('h2');
      var top = document.getElementById('main-top');
      if (top && top.getBoundingClientRect().top < 0) top.scrollIntoView({ block: 'start', behavior: reduce ? 'auto' : 'smooth' });
      if (h) { try { h.focus({ preventScroll: true }); } catch (e) { h.focus(); } }
    }
  }
  function go(d) { show(cur + d, true); }
  document.addEventListener('click', function (e) {
    var b = e.target.closest ? e.target.closest('[data-go]') : null;
    if (b) { e.preventDefault(); go(b.getAttribute('data-go') === 'next' ? 1 : -1); return; }
    var a = e.target.closest ? e.target.closest('.toc a') : null;
    if (a) {
      var i = steps.map(function (s) { return '#' + s.id; }).indexOf(a.getAttribute('href'));
      if (i >= 0) { e.preventDefault(); show(i, true); }
    }
  });
  if (select) select.addEventListener('change', function () {
    var i = steps.map(function (s) { return s.id; }).indexOf(select.value);
    if (i >= 0) show(i, false);
  });
  document.addEventListener('keydown', function (e) {
    if (e.defaultPrevented || e.altKey || e.ctrlKey || e.metaKey || e.shiftKey) return;
    var t = e.target, tag = t && t.tagName;
    if (tag === 'SELECT' || tag === 'INPUT' || tag === 'TEXTAREA' || (t && t.isContentEditable)) return;
    if (e.key === 'ArrowRight') { e.preventDefault(); go(1); }
    else if (e.key === 'ArrowLeft') { e.preventDefault(); go(-1); }
  });
  window.addEventListener('hashchange', function () { var i = fromHash(); if (i >= 0 && i !== cur) show(i, true); });
  var start = fromHash();
  if (start < 0) start = stored();
  show(start < 0 ? 0 : start, false, true);
})();
"""


def esc(text: Any) -> str:
    return html.escape(str(text), quote=True)


def rich(text: str) -> str:
    """단계 글: [단추]는 단추 모양으로, 줄 바꿈은 <br>. ⚙·▶는 그림 글자가 아닌 글자로."""
    out = html.escape(text or "", quote=False)
    out = re.sub(r"\[([^\[\]\n]{1,40})\]", r'<span class="ui">\1</span>', out)
    out = re.sub("([\u2699\u25b6])(?!\ufe0e)", "\\1\ufe0e", out)
    return out.replace("\n", "<br>")


def _toc(data: Dict[str, Any]) -> str:
    parts: List[str] = ['<nav class="toc" aria-label="단계 목록">']
    for sec in data["sections"]:
        items = [s for s in data["steps"] if s["section"] == sec["id"]]
        parts.append(f'<h2>{esc(sec["title"])}</h2><ol>')
        for s in items:
            g = f'안내 {s["guide_step"]}' if s.get("guide_step") else ""
            if s.get("issues"):
                g += ' <span class="flag" aria-hidden="true">●</span><span class="sr">찾은 문제 있음</span>'
            g = f'<span class="g">{g}</span>'
            parts.append(f'<li><a href="#{esc(s["id"])}"><span class="n">{s["n"]}</span>'
                         f'<span>{rich(s["title"])}</span>{g}</a></li>')
        parts.append("</ol>")
    parts.append("</nav>")
    return "".join(parts)


def _issues_sum(steps: List[Dict[str, Any]]) -> str:
    """머리말의 한 줄: 찾은 문제가 있는 단계로 가는 고리 (없으면 빈 문자열)."""
    marked = [s for s in steps if s.get("issues")]
    if not marked:
        return ""
    links = " ".join(f'<a href="#{esc(s["id"])}">{s["n"]}</a>' for s in marked)
    return f'<p class="issues-sum">{esc(ISSUES_SUM)} {links}</p>'


def _select(data: Dict[str, Any]) -> str:
    parts = ['<select id="jump" aria-label="단계 고르기">']
    for sec in data["sections"]:
        parts.append(f'<optgroup label="{esc(sec["title"])}">')
        for s in data["steps"]:
            if s["section"] == sec["id"]:
                title = re.sub("\u2699(?!\ufe0e)", "\u2699\ufe0e", s["title"])
                parts.append(f'<option value="{esc(s["id"])}">{s["n"]}. {esc(title)}</option>')
        parts.append("</optgroup>")
    parts.append("</select>")
    return "".join(parts)


def _figure(img: Dict[str, Any], eager: bool) -> str:
    kind = img.get("kind") or "window"
    label = KIND_LABELS.get(kind, kind)
    loading = "eager" if eager else "lazy"
    return (f'<figure class="shot {esc(kind)}"><div class="frame">'
            f'<img src="{esc(img["src"])}" width="{int(img["w"])}" height="{int(img["h"])}" alt="{esc(img["alt"])}" '
            f'loading="{loading}" decoding="async"></div>'
            f'<figcaption><span class="kind">{esc(label)}</span><span>{esc(img.get("caption") or img["alt"])}</span>'
            "</figcaption></figure>")


def _step(s: Dict[str, Any], total: int) -> str:
    meta = []
    if s.get("guide_step"):
        meta.append(f'안내 {s["guide_step"]}번')
    else:
        meta.append("안내에 없는 화면")
    meta.append(s.get("section_title") or "")
    parts = [f'<article class="step" id="{esc(s["id"])}" aria-labelledby="{esc(s["id"])}-t">',
             f'<div class="step-top"><span class="step-no">{s["n"]}<small> / {total}</small></span>'
             f'<span class="step-meta">{esc(" · ".join(m for m in meta if m))}</span></div>',
             f'<h2 id="{esc(s["id"])}-t" tabindex="-1">{rich(s["title"])}</h2>',
             '<dl class="facts">',
             f'<div class="fact"><dt>할 일</dt><dd>{rich(s["do"])}</dd></div>',
             f'<div class="fact"><dt>보이는 것</dt><dd>{rich(s["see"])}</dd></div>',
             "</dl>"]
    if s.get("note"):
        parts.append(f'<p class="note">{rich(s["note"])}</p>')
    if s.get("issues"):
        parts.append(f'<div class="issue"><h3>{ISSUE_TITLE}</h3><ul>'
                     + "".join(f"<li>{rich(t)}</li>" for t in s["issues"]) + "</ul></div>")
    parts.append('<div class="shots">')
    parts += [_figure(img, eager=s["n"] == 1) for img in s["images"]]
    parts.append("</div></article>")
    return "".join(parts)


def render(data: Dict[str, Any]) -> str:
    steps = data["steps"]
    total = len(steps)
    title = data["title"]
    kicker = f'빌드 {data["build"]} · 앱 {data["app_version"]} · 리졸브 스크립트 {data["script_version"]}'
    meta = f'만든 때 {data["generated_at"]} · {total}단계 · 창 {data["window"]["w"]}×{data["window"]["h"]}'
    nav_btn = ('<button type="button" class="nav-btn{cls}" data-go="{go}">{a}<span class="t"></span>{b}</button>')
    prev_btn = nav_btn.format(cls=" prev", go="prev", a="<span aria-hidden=\"true\">←</span>", b="")
    next_btn = nav_btn.format(cls=" next primary", go="next", a="", b="<span aria-hidden=\"true\">→</span>")
    body = [
        f"<title>{esc(title)}</title>",
        f"<style>{CSS}</style>",
        '<link rel="preconnect" href="https://fonts.googleapis.com">',
        '<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>',
        f'<link rel="stylesheet" href="{esc(FONTS)}">',
        f'<div class="wrap" data-build="{esc(data["build"])}">',
        '<header class="top">',
        f'<p class="kicker">{esc(kicker)}</p>',
        f"<h1>{esc(title)}</h1>",
        f'<p class="lede">{esc(LEDE)}</p>',
        f'<p class="ask">{esc(ASK)}</p>',
        f'<p class="meta">{esc(meta)}</p>',
        _issues_sum(steps),
        "</header>",
        '<div class="layout">',
        _toc(data),
        '<main id="main-top">',
        f'<div class="stepper">{prev_btn}{_select(data)}'
        f'<span class="count" data-count aria-live="polite"></span>{next_btn}</div>',
        *[_step(s, total) for s in steps],
        f'<div class="pager">{prev_btn}{next_btn}</div>',
        "</main></div>",
        '<footer class="foot"><ul>' + "".join(f"<li>{esc(t)}</li>" for t in FOOT) + "</ul></footer>",
        "</div>",
        f"<script>{JS}</script>",
    ]
    return "\n".join(body) + "\n"


def write(out_dir: Path, data: Dict[str, Any]) -> Path:
    path = Path(out_dir) / "index.html"
    path.write_text(render(data), encoding="utf-8")
    return path


def from_steps(out_dir: Path) -> Path:
    """steps.json만 고쳤을 때 쪽만 다시 만든다."""
    data = json.loads((Path(out_dir) / "steps.json").read_text(encoding="utf-8"))
    return write(out_dir, data)
