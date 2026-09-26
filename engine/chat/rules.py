"""RuleBrain "기본 도우미 (AI 아님)" (설계 B4.2). 정해진 말만 알아듣고, 못 알아들은 부분은 숨기지 않는다.

순서
1. 정리 (NFC, 전각 → 반각, 빈칸 하나로).
2. "빼고/말고"(부정)는 2.1에서 알아듣지 않는다 → 되묻기.
3. 따옴표 안의 글(표시 이름·메모)과 양("2초 넘게", "6dB", "3개만")을 먼저 찾아 가려 두고, 시간 말을 찾는다.
4. 부탁을 마디로 나눈다: 하고/해 주고/그리고/쉼표/마침표 ... (시간 말 "5분하고 6분 사이" 안에서는 나누지 않는다).
   "랑/이랑"은 양쪽에 동작이나 시간이 있을 때만, 다른 동사 뒤의 "-고"(키우고, 자르고, 옮기고, 걸고 ...)는 앞에 동작이
   있고 뒤에 새 부탁(시간·대상·색)이 있을 때만 나눈다. 나눈 자리에 걸친 동작 낱말("찍고")은 앞 마디의 것으로 본다.
5. 마디마다 규칙을 차례로 본다 (못 하는 부탁 → 도움말 → 상태 → 되돌리기 → 저장 → 모두 빼기 → 소리 바꾸기 →
   지우기 → 재생 위치 → 쉬는 곳 → 튀는 소리 → 표시). 규칙은 실제로 쓴 글자 자리(consumed)만 적는다: 표시 규칙은
   표시 동작만, 도움말·되돌리기·저장은 그 낱말만. 권하는 표시(자르기·소리)는 말한 시간마다 하나씩.
   시간만 있는 마디("3분,")는 옆 표시 마디에 붙고, 색·메모만 있는 마디("빨간색으로")는 앞 마디를 꾸민다.
6. 모자람 검사: 쓰지 않은 자리에 시간·동작·색 낱말이 남았거나, 다른 마디는 알아들었는데 한 마디를 못 알아들었으면
   "못 알아들은 부분"으로 남긴다 (카드의 주황 줄). 나눈 뒤 동작만 남은 마디("…하고 지워")가 있으면 부탁 전체를 되묻는다
   (취소·되돌려·저장처럼 앞뒤 말로 대상을 아는 동작은 빼고).
7. 타임라인(TimeContext)이 있으면 시간을 절대 프레임으로 푼다. 없으면 NeedTimeline (화면이 연결 확인 뒤 다시 부른다).

리졸브에 들어가는 표시 이름("표시", "여기 6dB 줄이기", "자르기 후보")만 여기서 만든다. 화면 글은 code로 돌려준다.
"""

from __future__ import annotations

import re
import threading
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

from . import intents_ko as K
from .brain import (DEFAULT, SAID, SETTING, BrainResult, Chip, Command, NeedTimeline, NotUnderstood, ProposalDraft,
                    Question, Reply, RequestedScope)
from .context import AssistContext
from .intents_ko import Found, Span, inside, normalize, overlaps
from .timeparse import (AROUND_S, Around, Clock, Edge, Hms3, InOut, Playhead, RangeExpr, Relative, TC4, TimeContext,
                        TimeError, TimeToken, Whole, resolve_point, resolve_range, scan)

MAX_MARK_ITEMS = 200  # 한 카드에 넣는 표시 (설계 B4.5)
GAIN_MIN_DB, GAIN_MAX_DB = -24.0, 12.0  # 설계 B4.5 clamps
POINT_TOL_S = 0.5  # 점 하나를 지울 때 앞뒤로 보는 여유 (설계 B4.3 tolerance)
MAX_NAME = 40
DEFAULT_MARK_NAME = "표시"
CUT_MARK_NAME = "자르기 후보"
AUDIO_MARK_NAME = "여기 {db}dB {verb}"
AUDIO_VERB_WORD = {"down": "줄이기", "up": "키우기"}
OFFER_COLOR = {"cut": "Purple", "audio": "Yellow"}

# 마디가 비었는지 볼 때 지우는 말 (조사, 부탁 끝말)
_FILLER_RE = re.compile(r"해\s*주세요|해\s*줘|주세요|해줄래|해\s*줄래|부탁해|제발|please|좀|줘|요|해|하|에서|에|을|를|은|는|이|가|도|만"
                        r"|으로|로|것|거|곳|부분|데|그리고|그|저|이제|다음|또|꼭|한번|한\s*번|[\s,;.!?~'\"]", re.IGNORECASE)
_SPLIT_RE = re.compile(r"\s*(?:그리고\s*나서|그리고|그\s*다음에|그\s*다음|,|;|(?<!\d)\.(?!\d)|!|\?|\n)\s*")
_STEM = r"(?:(?<=하)|(?<=찍)|(?<=지우)|(?<=달)|(?<=없애)|(?<=넣)|(?<=가)|(?<=해)|(?<=주))"
_AND_VERB_RE = re.compile(_STEM + r"고(?:\s*나서)?(?=\s)")
# 다른 동사 뒤의 "-고" ("키우고", "자르고", "옮기고", "걸고" ...): 앞에 동작(또는 못 하는 부탁 낱말)이 있고
# 뒤에 새 부탁(시간·대상·색·못 하는 부탁 낱말)이 있을 때만 나눈다 ("쉬는 곳 찾고 표시해줘"는 한 부탁).
_AND_ANY_RE = re.compile(r"(?<=[가-힣])고(?:\s*나서)?(?=\s)")
_REFUSAL_RES = (K.STUDIO_RE, K.KEYFRAME_RE, K.NEEDS_AI_RE, K.CUT_RE, K.OUT_OF_SCOPE_RE, K.AUDIO_NOT_READY_RE)
_RANG_RE = re.compile(r"(?<=[가-힣])(?:이랑|랑)(?=\s)")
_SAVE_WORD_RE = re.compile(r"자동화|버튼|이대로|그대로")
_EDGE_JUMP_RE = re.compile(r"(?:맨\s*)?(처음|시작|앞|끝|마지막|뒤)\s*(?:으로|로)")


# ── 글 전체에서 찾은 낱말 ──────────────────────────────────────────────


@dataclass
class _Tokens:
    text: str
    masked: List[Span]
    times: List[TimeToken]
    verbs: List[Found]
    objects: List[Found]
    colors: List[Found]
    unknown_colors: List[Found]
    quotes: List[Found]
    min_s: List[Found]
    above: List[Found]
    db: List[Found]
    lufs: List[Found]
    counts: List[Found]
    ones: List[Found]
    amounts: List[Found]
    tracks: List[Found]

    @classmethod
    def read(cls, text: str) -> "_Tokens":
        quotes = K.find_quotes(text)
        q_spans = [q.span for q in quotes]

        def outside(items: Sequence[Found]) -> List[Found]:
            return [f for f in items if not overlaps(f.span, q_spans)]

        min_s = outside(K.find_min_s(text))
        above = outside(K.find_above_db(text))
        db = [f for f in outside(K.find_db(text)) if not overlaps(f.span, [a.span for a in above])]
        lufs = outside(K.find_lufs(text))
        counts_all = outside(K.find_counts(text))
        counts = [c for c in counts_all if c.kind == "count"]
        masked = q_spans + [f.span for f in min_s + above + db + lufs + counts]
        times = scan(text, masked)
        # "2초 (정도) 쉰 곳": 초만 있는 시간 바로 뒤에 "쉰/쉬는"이 붙으면 자리가 아니라 쉰 길이다
        lengths = [_pause_length(text, t) for t in times]
        if any(lengths):
            min_s = sorted(min_s + [f for f in lengths if f], key=lambda f: f.start)
            times = [t for t, f in zip(times, lengths) if f is None]
        t_spans = [t.span for t in times]
        ones = [c for c in counts_all if c.kind == "one" and not overlaps(c.span, t_spans)]
        objects = outside(K.find_objects(text))
        ours = [o.span for o in objects if o.kind == "ours"]
        verbs = [v for v in outside(K.find_verbs(text)) if not overlaps(v.span, ours)]
        return cls(text=text, masked=masked, times=times, verbs=verbs, objects=objects,
                   colors=outside(K.find_colors(text)), unknown_colors=outside(K.find_unknown_colors(text)),
                   quotes=quotes, min_s=min_s, above=above, db=db, lufs=lufs, counts=counts, ones=ones,
                   amounts=outside(K.find_word_amounts(text)), tracks=outside(K.find_tracks(text)))


_PAUSE_AFTER_RE = re.compile(r"\s*(?:동안\s*)?(?:쉰|쉬는|쉬었던|쉬고\s*있는)")


def _pause_length(text: str, t: TimeToken) -> Optional[Found]:
    """"2초 쉰 곳", "2초 정도 쉬는 곳"의 "2초 (정도)" → 쉰 길이(min_s). "3분 20초 쉬는 곳"(분이 있음)이나
    "3초에 쉬는 곳"(조사가 붙음)은 자리로 둔다."""
    clk = t.expr.point if isinstance(t.expr, Around) else t.expr
    if not isinstance(clk, Clock) or not clk.has_unit or (clk.lead or clk.unit) != "s" or "초" not in clk.raw:
        return None
    if not _PAUSE_AFTER_RE.match(text, t.end):
        return None
    return Found(t.start, t.end, "min_s", float(clk.seconds), t.text)


def _in(items, a: int, b: int):
    return [x for x in items if a <= x.start and x.end <= b]


@dataclass
class _Clause:
    start: int
    end: int
    text: str
    times: List[TimeToken]
    verbs: List[Found]
    objects: List[Found]
    colors: List[Found]
    unknown_colors: List[Found]
    quotes: List[Found]
    min_s: List[Found]
    above: List[Found]
    db: List[Found]
    lufs: List[Found]
    counts: List[Found]
    ones: List[Found]
    amounts: List[Found]
    tracks: List[Found]

    @classmethod
    def cut(cls, tok: _Tokens, a: int, b: int) -> "_Clause":
        return cls(a, b, tok.text[a:b], _in(tok.times, a, b), _in(tok.verbs, a, b), _in(tok.objects, a, b),
                   _in(tok.colors, a, b), _in(tok.unknown_colors, a, b), _in(tok.quotes, a, b), _in(tok.min_s, a, b),
                   _in(tok.above, a, b), _in(tok.db, a, b), _in(tok.lufs, a, b), _in(tok.counts, a, b),
                   _in(tok.ones, a, b), _in(tok.amounts, a, b), _in(tok.tracks, a, b))

    @property
    def span(self) -> Span:
        return (self.start, self.end)

    def obj(self, *kinds: str) -> List[Found]:
        return [o for o in self.objects if o.kind in kinds]

    def verb(self, *kinds: str) -> List[Found]:
        return [v for v in self.verbs if v.kind in kinds]

    def search(self, regex: "re.Pattern[str]") -> Optional[Tuple[int, int]]:
        m = regex.search(self.text)
        return (self.start + m.start(), self.start + m.end()) if m else None

    @property
    def empty(self) -> bool:
        return not _FILLER_RE.sub("", self.text)

    @property
    def has_tokens(self) -> bool:
        return bool(self.times or self.verbs or self.objects or self.colors or self.unknown_colors)


@dataclass
class _Result:
    clause: _Clause
    kind: str  # command / reply / time_only / modifier / verb_only / empty / unmatched / question
    command: Optional[Command] = None
    reply: Optional[Reply] = None
    question: Optional[Question] = None
    consumed: List[Span] = field(default_factory=list)
    left: List[str] = field(default_factory=list)  # 이 마디에서 쓰지 않은 부탁 (못 알아들은 부분으로)


def _split(tok: _Tokens) -> List[Tuple[int, int]]:
    """마디 자리 목록. 시간 말과 따옴표 안에서는 나누지 않는다."""
    text = tok.text
    protected = [t.span for t in tok.times] + [q.span for q in tok.quotes]
    cuts: List[Tuple[int, int]] = []
    for regex in (_SPLIT_RE, _AND_VERB_RE):
        for m in regex.finditer(text):
            if m.end() > m.start() and not overlaps(m.span(), protected):
                cuts.append(m.span())
    # 랑/이랑: 양쪽 모두에 동작이나 시간이 있을 때만
    for m in _RANG_RE.finditer(text):
        if overlaps(m.span(), protected):
            continue
        left_a = max([c[1] for c in cuts if c[1] <= m.start()], default=0)
        right_b = min([c[0] for c in cuts if c[0] >= m.end()], default=len(text))

        def lively(a: int, b: int) -> bool:
            return bool(_in(tok.verbs, a, b) or _in(tok.times, a, b))

        if lively(left_a, m.start()) and lively(m.end(), right_b):
            cuts.append(m.span())
    # 다른 동사 뒤의 "-고": 앞에 동작, 뒤에 새 부탁이 있을 때만
    for m in _AND_ANY_RE.finditer(text):
        if overlaps(m.span(), protected) or overlaps(m.span(), cuts):
            continue
        left_a = max([c[1] for c in cuts if c[1] <= m.start()], default=0)
        right_b = min([c[0] for c in cuts if c[0] >= m.end()], default=len(text))

        def refusal(a: int, b: int) -> bool:
            return any(r.search(text[a:b]) for r in _REFUSAL_RES)

        acts = any(v.start < m.start() and left_a < v.end for v in tok.verbs) or refusal(left_a, m.start())
        fresh = bool(_in(tok.times, m.end(), right_b) or _in(tok.objects, m.end(), right_b)
                     or _in(tok.colors, m.end(), right_b) or refusal(m.end(), right_b))
        if acts and fresh:
            cuts.append(m.span())
    cuts.sort()
    spans: List[Tuple[int, int]] = []
    pos = 0
    for a, b in cuts:
        if a < pos:
            pos = max(pos, b)
            continue
        spans.append((pos, a))
        pos = b
    spans.append((pos, len(text)))
    out = []
    for a, b in spans:
        while a < b and text[a].isspace():
            a += 1
        while b > a and text[b - 1].isspace():
            b -= 1
        if b > a:
            out.append((a, b))
    return out


def _residue_text(c: "_Clause", spans: Sequence[Span]) -> str:
    """마디에서 쓴 자리를 뺀 나머지 글 (사용자가 쓴 말 그대로, 빈칸 하나로). 조사·끝말뿐이면 빈 글."""
    chars = list(c.text)
    for a, b in spans:
        for i in range(max(a, c.start), min(b, c.end)):
            chars[i - c.start] = " "
    rest = re.sub(r"\s+", " ", "".join(chars)).strip(" ,;.!?")
    return rest if _FILLER_RE.sub("", rest) else ""


def _trim_verbs(verbs: Sequence[Found], spans: Sequence[Tuple[int, int]], text: str) -> List[Found]:
    """나눈 자리에 걸친 동작 낱말("찍고"의 "고"에서 나눔)은 앞 마디 안쪽만 남긴다. 나눈 말 안에만 있으면 버린다."""
    out: List[Found] = []
    for v in verbs:
        if any(a <= v.start and v.end <= b for a, b in spans):
            out.append(v)
            continue
        part = next(((max(a, v.start), min(b, v.end)) for a, b in spans if v.start < b and a < v.end), None)
        if part is not None and part[1] > part[0]:
            out.append(Found(part[0], part[1], v.kind, v.value, text[part[0]:part[1]]))
    return out


def _residue(c: "_Clause", spans: Sequence[Span]) -> str:
    """마디에서 찾은 낱말 자리와 조사·끝말을 뺀 나머지 (시간만·색만 있는 마디가 정말 그것뿐인지 볼 때)."""
    chars = list(c.text)
    for a, b in spans:
        for i in range(max(a, c.start), min(b, c.end)):
            chars[i - c.start] = " "
    return _FILLER_RE.sub("", "".join(chars))


_STEM_ENDINGS = (("없애", "없애줘"), ("지우", "지워줘"), ("하", "해줘"), ("찍", "찍어줘"), ("달", "달아줘"), ("넣", "넣어줘"),
                 ("가", "가줘"), ("해", "해줘"), ("주", "줘"), ("자르", "잘라줘"), ("줄이", "줄여줘"), ("키우", "키워줘"),
                 ("옮기", "옮겨줘"), ("찾", "찾아줘"), ("걸", "걸어줘"), ("올리", "올려줘"), ("낮추", "낮춰줘"),
                 ("맞추", "맞춰줘"), ("바꾸", "바꿔줘"), ("끄", "꺼줘"), ("켜", "켜줘"))


def spoken(text: str) -> str:
    """나눌 때 "…하고"의 "하"만 남은 마디 끝을 말하는 모양으로 ("3분 20초에 표시하" → "3분 20초에 표시해줘")."""
    for stem, full in _STEM_ENDINGS:
        if text.endswith(stem) and len(text) > len(stem):
            return text[: -len(stem)] + full
    return text


def _word_at(text: str, span: Span) -> str:
    """낱말 자리를 빈칸 사이 말 하나로 넓힌다 ("초록" → "초록도")."""
    a, b = span
    while a > 0 and not text[a - 1].isspace() and text[a - 1] not in ",;.!?":
        a -= 1
    while b < len(text) and not text[b].isspace() and text[b] not in ",;.!?":
        b += 1
    return text[a:b]


def _first_value(items: Sequence[Found]) -> Any:
    return items[0].value if items else None


# ── 두뇌 ──────────────────────────────────────────────────────────────


@dataclass
class Parsed:
    """글만 보고 한 풀이 (타임라인 없이). handle이 시간을 풀고 결과를 만든다."""

    text: str
    results: List[_Result]
    leftovers: List[str]
    question: Optional[Question] = None

    @property
    def commands(self) -> List[Command]:
        return [r.command for r in self.results if r.command is not None]

    @property
    def replies(self) -> List[Reply]:
        return [r.reply for r in self.results if r.reply is not None]


class RuleBrain:
    """기본 도우미 (AI 아님). 인터넷 없이, 10ms 안에."""

    name = "rules"

    def handle(self, text: str, ctx: Optional[AssistContext] = None,
               cancel: Optional[threading.Event] = None) -> BrainResult:
        ctx = ctx or AssistContext()
        norm = normalize(text)
        if not norm:
            return NotUnderstood(_examples(), text=norm)
        parsed = self.parse(norm)
        if parsed.question is not None:
            return parsed.question
        commands = parsed.commands
        replies = parsed.replies
        if not commands:
            if replies:
                rep = replies[0]
                if parsed.leftovers:
                    # 답만 하는 부탁에 다른 부탁이 섞였으면 그 부분을 숨기지 않는다 (화면이 주황 줄로 알린다)
                    rep = Reply(rep.code, {**rep.data, "leftovers": list(parsed.leftovers)}, rep.chips)
                return rep
            if any(r.clause.has_tokens for r in parsed.results):
                return _what_to_do(parsed)
            return NotUnderstood(_examples(), text=norm)
        notes = [("refused", {"code": r.code, **r.data}) for r in replies]
        needs_time = any(c.op not in ("save_slot", "help") for c in commands)
        if needs_time and ctx.time is None:
            return NeedTimeline("need_timeline")
        resolved: List[Command] = []
        for c in commands:
            try:
                out = self._resolve(c, ctx)
            except TimeError as exc:
                return _time_reply(exc, c, norm, ctx)
            if isinstance(out, (Reply, Question)):
                return out
            resolved.append(out)
        for c in resolved:
            for it in c.params.get("items") or []:
                if not it.get("note"):
                    it["note"] = norm
        # 한 가지만 부탁한 상태 묻기는 바로 답한다
        if len(resolved) == 1 and resolved[0].op == "status":
            return Reply("status", _status(ctx))
        # 12dB보다 크게 키우기: 12dB로 할지 묻는다 (설계 부록 A)
        for c in resolved:
            if c.offer == "audio" and c.params.get("direction") == "up" and c.params.get("db", 0) > GAIN_MAX_DB:
                said = c.params["db"]
                c.params["db"] = GAIN_MAX_DB
                _rename_audio(c)
                return Question("over_gain", {"db": said, "max": GAIN_MAX_DB},
                                draft=ProposalDraft(resolved, norm, parsed.leftovers, notes))
        return ProposalDraft(resolved, norm, parsed.leftovers, notes)

    # ── 글 풀이 ───────────────────────────────────────────────────────

    def parse(self, text: str) -> Parsed:
        neg = K.NEGATION_RE.search(text)
        if neg:
            return Parsed(text, [], [], Question("negation", {"word": neg.group(0)},
                                                 [Chip("example:mark_time", {"time": "3분 20초"}),
                                                  Chip("example:range_pauses")]))
        tok = _Tokens.read(text)
        spans = _split(tok)
        tok.verbs = _trim_verbs(tok.verbs, spans, text)
        clauses = [_Clause.cut(tok, a, b) for a, b in spans]
        results = [self._clause(c) for c in clauses]
        self._join(results)
        # 나눈 뒤 동작만 남은 마디 → 부탁 전체를 되묻는다 (설계 B4.2)
        if len(results) > 1:
            lone = [r for r in results if r.kind == "verb_only"]
            if lone:
                rest = " ".join(spoken(r.clause.text) for r in results if r.kind == "command")
                chips = [Chip("text", {"text": rest})] if rest else []
                chips.append(Chip("example:clear_ours"))
                return Parsed(text, results, [], Question("verb_only", {"clause": spoken(lone[0].clause.text),
                                                                        "text": text}, chips))
        consumed: List[Span] = [s for r in results for s in r.consumed]
        leftovers: List[str] = []
        good = any(r.command is not None or r.reply is not None for r in results)
        for r in results:
            if good and r.kind in ("unmatched", "verb_only", "question", "time_only", "modifier"):
                leftovers.append(spoken(r.clause.text))
                consumed.append(r.clause.span)
            for word in r.left:
                if word not in leftovers:
                    leftovers.append(word)
        if good:
            loose = [t.span for t in tok.times] + [v.span for v in tok.verbs] + \
                [c.span for c in tok.colors + tok.unknown_colors]
            seen: List[Span] = []
            for span in sorted(loose):
                if inside(span, consumed) or overlaps(span, seen):
                    continue
                word = _word_at(text, span)
                seen.append(span)
                if word and word not in leftovers:
                    leftovers.append(word)
        # 한 마디뿐이고 그 마디가 되묻기면 그대로
        if not good:
            q = next((r.question for r in results if r.question is not None), None)
            if q is not None:
                return Parsed(text, results, [], q)
        return Parsed(text, results, leftovers)

    def _join(self, results: List[_Result]) -> None:
        """시간만 있는 마디는 옆 표시 마디에, 색·메모만 있는 마디는 앞(없으면 뒤) 마디에 붙인다."""
        for i, r in enumerate(results):
            if r.kind == "time_only":
                target = _neighbour(results, i, ("mark",))
                if target is not None:
                    _merge_times(target, r)
                    continue
                # "3분 20, 표시해줘": 시간 없이 "어디에 표시할까요?"가 된 옆 마디에 그 시간을 준다
                ask = next((results[j] for j in (i + 1, i - 1) if 0 <= j < len(results)
                            and results[j].question is not None and results[j].question.code == "mark_where"), None)
                if ask is not None:
                    ask.clause.times = list(r.clause.times)
                    new = self._mark(ask.clause)
                    if new.command is not None:
                        ask.kind, ask.command, ask.question = new.kind, new.command, None
                        ask.consumed = new.consumed + r.consumed
                        r.kind = "merged"
            elif r.kind == "modifier":
                target = _neighbour(results, i, ("mark", "mark_pauses", "mark_spikes", "clear_marks"), prefer_prev=True)
                if target is not None:
                    _merge_modifier(target, r)
                else:
                    r.kind = "unmatched"

    # ── 마디 하나 ────────────────────────────────────────────────────

    def _clause(self, c: _Clause) -> _Result:
        if c.empty:
            return _Result(c, "empty", consumed=[c.span])
        whole = [c.span]
        text = c.text
        # 못 하는 부탁 (설계 부록 A Refusals)
        if K.STUDIO_RE.search(text):
            subtitles = bool(K.SUBTITLE_AUTO_RE.search(text))
            return _Result(c, "reply", reply=Reply("studio", {"subtitles": subtitles}), consumed=whole)
        if K.KEYFRAME_RE.search(text):
            return _Result(c, "reply", reply=Reply("keyframe"), consumed=whole)
        if K.NEEDS_AI_RE.search(text):
            where = c.times[0].text if c.times else ""
            chip = Chip("example:spikes_at", {"time": where}) if where else Chip("example:spikes")
            return _Result(c, "reply", reply=Reply("needs_ai", {}, [chip]), consumed=whole)
        finds = c.obj("pause", "spike", "marker", "ours")
        if K.CUT_RE.search(text) or (K.MOVE_CLIP_RE.search(text) and not finds):
            if finds and not c.times:
                # "쉬는 곳 잘라줘": 자를 자리를 하나로 말하지 않음 → 못 한다고만 (표시 찾기 예문)
                return _Result(c, "reply", reply=Reply("cut_no_place", {}, [Chip("example:pauses")]), consumed=whole)
            cmd = self._offer(c, "cut", CUT_MARK_NAME)
            return _Result(c, "command", command=cmd, consumed=whole)
        if K.OUT_OF_SCOPE_RE.search(text):
            return _Result(c, "reply", reply=Reply("out_of_scope"), consumed=whole)
        if K.AUDIO_NOT_READY_RE.search(text):
            return _Result(c, "reply", reply=Reply("not_ready"), consumed=whole)
        subtitles = c.obj("subtitle")
        # 자막 넣기는 아직 없다 (시간을 말해도 "표시"로 바꿔 넣지 않는다. "자막 확인 표시"처럼 표시를 말하면 표시)
        if subtitles and (c.verb("mark", "find") or re.search(r"만들|생성|달아|넣어|뽑아", text)) \
                and not c.obj("marker"):
            return _Result(c, "reply", reply=Reply("subtitles_later"), consumed=whole)
        # 도움말·상태. 도움말은 그 낱말만 쓴다: 같은 마디의 다른 부탁("도움말 3분에 표시")은 못 알아들은 부분으로
        h = c.search(K.HELP_RE)
        if h:
            return self._context(c, "reply", [h], reply=Reply("help", {}, _examples()))
        if K.STATUS_RE.search(text):
            return _Result(c, "command", command=Command("status", clause=text, span=c.span), consumed=whole)
        # 되돌리기 ("방금 거 취소", "방금 넣은 거 빼줘")
        last = c.search(K.UNDO_LAST_RE)
        kinds_said = c.obj("pause", "spike") or c.colors
        if last and c.verb("clear", "undo"):
            if c.colors:
                # "방금 넣은 빨간 표시 빼줘": 방금 것 전체인지, 그 색 표시 모두인지 모름 → 되묻기
                return self._recent_question(c)
            return self._undo(c, [last])
        if c.verb("undo") and not kinds_said:
            every = c.search(K.UNDO_ALL_RE)
            if every or any(isinstance(t.expr, Whole) for t in c.times):
                # "다 취소": 되돌리기는 한 번에 하나 → 방금 것만인지, 도우미 것 모두인지 묻는다
                chips = [Chip("example:undo"), Chip("example:remove_all")]
                return _Result(c, "question", question=Question("undo_all", {"text": text}, chips), consumed=whole)
            if c.times and c.obj("marker", "ours"):
                # "3분에 표시 취소", "5분~6분 표시 취소": 말한 자리의 도우미 표시 지우기 (범위 지킴이를 거친다)
                return self._clear(c)
            if c.times:
                chip = Chip("example:clear_at", {"time": c.times[0].text})
                return _Result(c, "question", question=Question("clear_what", {"text": text}, [chip]),
                               consumed=whole)
            return self._undo(c, [])
        # 자동화 버튼에 저장 (구간은 저장하지 않는다)
        m = K.SAVE_SLOT_RE.search(text)
        if m or (c.verb("save") and _SAVE_WORD_RE.search(text)):
            slot = None
            if m:
                g = next((x for x in m.groups() if x), None)
                slot = int(g) if g else None
            cmd = Command("save_slot", {"slot": slot}, {"slot": SAID} if slot else {}, clause=text, span=c.span)
            used = [c.search(K.SAVE_SLOT_RE) if m else c.search(_SAVE_WORD_RE)]
            return self._context(c, "command", used + [v.span for v in c.verb("save")], command=cmd)
        # 도우미가 넣은 것 모두 빼기
        r = c.search(K.REMOVE_ALL_RE)
        if r and not (c.colors or c.obj("pause", "spike") or c.times):
            cmd = Command("remove_all_ours", clause=text, span=c.span)
            return self._context(c, "command", [r] + [v.span for v in c.verb("clear")], command=cmd)
        # 소리 바꾸기: 2.1에서는 못 한다 → 그 자리에 노란 표시를 권한다
        audio_verbs = c.verb("down", "up", "even", "press")
        if audio_verbs and not c.obj("pause", "marker"):
            return self._audio(c, audio_verbs)
        if c.lufs:
            return _Result(c, "reply", reply=Reply("balance_not_ready"), consumed=whole)
        # 지우기 (도우미 표시만)
        clear_verbs = c.verb("clear", "undo")
        if clear_verbs:
            if c.search(K.OWN_MARKS_RE):
                # "내가 찍은 표시 지워줘": 사용자가 넣은 표시는 도우미가 지우지 않는다 (모든 도우미 표시로 바꾸지 않는다)
                return _Result(c, "reply", reply=Reply("own_markers", {}, [Chip("example:clear_ours")]),
                               consumed=whole)
            if c.search(K.RECENT_RE):
                # "방금 파란 표시 지워": 방금 넣은 것인지, 그 색 표시 모두인지 모름 → 되묻기
                return self._recent_question(c)
            target = c.obj("marker", "pause", "spike", "ours") or c.colors
            if target:
                return self._clear(c)
            if c.times:
                chip = Chip("example:clear_at", {"time": c.times[0].text})
                return _Result(c, "question", question=Question("clear_what", {"text": text}, [chip]),
                               consumed=whole)
            if not (c.objects or c.colors):
                return _Result(c, "verb_only", consumed=[v.span for v in clear_verbs])
        # 재생 위치 옮기기 ("처음으로 가줘"의 처음·끝도)
        if c.verb("jump") and not c.times and not c.obj("pause", "spike", "marker"):
            e = _EDGE_JUMP_RE.search(text)
            if e:
                which = "end" if e.group(1) in ("끝", "마지막", "뒤") else "start"
                c.times = [TimeToken(c.start + e.start(), c.start + e.end(1), e.group(1), Edge(which, e.group(1)))]
        if c.verb("jump") and c.times and not c.obj("pause", "spike", "marker"):
            return self._jump(c)
        # 쉬는 곳 / 튀는 소리 표시
        if c.obj("pause"):
            return self._find(c, "mark_pauses")
        if c.obj("spike"):
            return self._find(c, "mark_spikes")
        # 표시
        if c.obj("marker") or c.verb("mark"):
            return self._mark(c)
        # 동작만
        edit_verbs = c.verb(*K.EDIT_VERBS)
        if edit_verbs and not (c.times or c.objects or c.colors):
            return _Result(c, "verb_only", consumed=[v.span for v in edit_verbs])
        # 시간만 (옆 마디에 붙을 수 있다). 모르는 말이 섞였으면 붙이지 않는다
        if c.times and not c.verbs and not c.objects and not c.unknown_colors:
            spans = [t.span for t in c.times] + [x.span for x in c.colors]
            if not _residue(c, spans):
                return _Result(c, "time_only", consumed=spans)
        # 색·메모·양만 (앞 마디를 꾸민다)
        mods = c.colors + c.quotes + c.min_s + c.above + c.counts + c.unknown_colors
        if mods and not c.verbs and not c.times and not c.objects:
            if not _residue(c, [x.span for x in mods]):
                return _Result(c, "modifier", consumed=[x.span for x in c.quotes + c.min_s + c.above + c.counts])
        return _Result(c, "unmatched")

    # ── 규칙마다 ──────────────────────────────────────────────────────

    def _context(self, c: _Clause, kind: str, used: List[Optional[Span]], **out: Any) -> _Result:
        """시간·범위를 쓰지 않는 부탁(도움말·되돌리기·저장·모두 빼기): 쓴 낱말만 쓴 자리로 적는다.
        같은 마디에 시간·동작·색이 더 있으면 그 나머지를 못 알아들은 부분으로 (조용히 버리지 않는다)."""
        used = [u for u in used if u]
        left_tokens = [x for x in c.times + c.verbs + c.colors if not overlaps(x.span, used)]
        res = _Result(c, kind, consumed=[c.span], **out)
        if left_tokens:
            rest = _residue_text(c, used + [x.span for x in c.objects if x.kind == "ours"])
            if rest:
                res.left = [spoken(rest)]
        return res

    def _undo(self, c: _Clause, used: List[Span]) -> _Result:
        cmd = Command("undo", {"which": "last"}, {"which": SAID}, clause=c.text, span=c.span)
        used = used + [v.span for v in c.verb("clear", "undo")]
        return self._context(c, "command", used, command=cmd)

    def _recent_question(self, c: _Clause) -> _Result:
        rest = _residue_text(c, [s for s in [c.search(K.UNDO_LAST_RE) or c.search(K.RECENT_RE)] if s])
        chips = [Chip("example:undo")] + ([Chip("text", {"text": spoken(rest)})] if rest else [])
        return _Result(c, "question", question=Question("recent_clear", {"text": c.text}, chips), consumed=[c.span])

    def _mark(self, c: _Clause) -> _Result:
        # 표시 동작만 쓴 자리로 (같은 마디의 "가줘"·"키워" 같은 다른 동작은 못 알아들은 부분으로 남긴다)
        consumed = [x.span for x in c.objects + c.verb("mark", "find") + c.times + c.quotes + c.tracks + c.ones]
        if c.unknown_colors:
            # 리졸브에 없는 색을 말함: 다른 색으로 넣지 않는다 (마디 전체를 못 알아들음)
            return _Result(c, "unmatched")
        if not c.times:
            chips = [Chip("example:mark_here"), Chip("example:mark_time", {"time": "3분 20초"})]
            return _Result(c, "question", question=Question("mark_where", {"text": c.text}, chips),
                           consumed=[c.span])
        cmd = Command("mark", clause=c.text, span=c.span)
        name, note, name_src = _names(c)
        colors = [f.value for f in c.colors]
        items = []
        for i, t in enumerate(c.times):
            color = colors[i] if len(colors) == len(c.times) else (colors[0] if colors else None)
            items.append({"time": t, "color": color, "name": name, "note": note,
                          "src": {"at": SAID, "color": SAID if color else DEFAULT, "name": name_src}})
        used_colors = c.colors if len(colors) in (1, len(c.times)) else c.colors[:1]
        consumed += [x.span for x in used_colors]
        cmd.params = {"items": items}
        if c.tracks:
            cmd.scope.tracks = [t.value for t in c.tracks if t.kind == "track"] or None
        return _Result(c, "command", command=cmd, consumed=consumed)

    def _offer(self, c: _Clause, offer: str, name: str) -> Command:
        """못 하는 부탁 대신 그 자리에 표시 (자르기 → 보라, 소리 → 노랑). 시간을 말하지 않았으면 재생 위치.
        시간을 여럿 말하면 ("3분과 5분에서 잘라줘") 시간마다 하나씩 (앞의 하나만 넣고 나머지를 버리지 않는다)."""
        times = list(c.times) or [TimeToken(c.start, c.start, "", Playhead(""))]
        items = [{"time": t, "color": OFFER_COLOR[offer], "name": name, "note": None,
                  "src": {"at": SAID if t.text else DEFAULT, "color": DEFAULT, "name": DEFAULT}} for t in times]
        return Command("mark", {"items": items}, clause=c.text, span=c.span, offer=offer)

    def _audio(self, c: _Clause, verbs: List[Found]) -> _Result:
        whole = [c.span]
        kind = verbs[0].kind
        if kind in ("even", "press") or c.lufs:
            return _Result(c, "reply", reply=Reply("balance_not_ready"), consumed=whole)
        if not c.times:
            return _Result(c, "reply", reply=Reply("audio_not_ready"), consumed=whole)
        if c.db:
            db, src = abs(float(c.db[0].value)), SAID
        elif c.amounts:
            db, src = float(c.amounts[0].value), DEFAULT
        else:
            db, src = K.BARE_AMOUNT_DB, DEFAULT
        cmd = self._offer(c, "audio", "")
        cmd.params["db"] = db
        cmd.params["direction"] = kind
        cmd.provenance["db"] = src
        if kind == "down" and db > -GAIN_MIN_DB:
            cmd.notes.append(("gain_clamped", {"db": db, "max": -GAIN_MIN_DB}))
            cmd.params["db"] = -GAIN_MIN_DB
        _rename_audio(cmd)
        return _Result(c, "command", command=cmd, consumed=whole)

    def _clear(self, c: _Clause) -> _Result:
        params: Dict[str, Any] = {}
        prov: Dict[str, str] = {}
        colors = sorted({f.value for f in c.colors})
        if colors:
            params["colors"] = colors
            prov["colors"] = SAID
        kinds = []
        if c.obj("pause"):
            kinds.append("mark_pauses")
        if c.obj("spike"):
            kinds.append("mark_spikes")
        if kinds:
            params["kinds"] = kinds
            prov["kinds"] = SAID
        if len(c.times) > 1:
            return _Result(c, "unmatched")
        cmd = Command("clear_marks", params, prov, clause=c.text, span=c.span)
        if c.times:
            cmd.params["time"] = c.times[0]
        cmd.scope.said_whole = any(isinstance(t.expr, Whole) for t in c.times) or bool(c.search(K.ALL_RE))
        consumed = [x.span for x in c.objects + c.verbs + c.times + c.colors + c.ones]
        m = c.search(K.ALL_RE)
        if m:
            consumed.append(m)
        return _Result(c, "command", command=cmd, consumed=consumed)

    def _jump(self, c: _Clause) -> _Result:
        if len(c.times) > 1:
            return _Result(c, "unmatched")
        cmd = Command("jump_to", {"time": c.times[0]}, {"at": SAID}, clause=c.text, span=c.span)
        return _Result(c, "command", command=cmd, consumed=[x.span for x in c.verbs + c.times])

    def _find(self, c: _Clause, op: str) -> _Result:
        # 마디 맨 앞의 조사 없는 "지금"("지금 쉬는 곳 표시해줘")은 재생 위치가 아니라 "이제"라는 뜻으로 본다
        now = [t for t in c.times if _adverb_now(c, t)]
        if now:
            c.times = [t for t in c.times if t not in now]
        if len(c.times) > 1 or c.unknown_colors:
            return _Result(c, "unmatched")
        params: Dict[str, Any] = {}
        prov: Dict[str, str] = {}
        if op == "mark_pauses" and c.min_s:
            params["min_s"], prov["min_s"] = float(c.min_s[0].value), SAID
        if op == "mark_spikes" and c.above:
            params["above_lu"], prov["above_lu"] = float(c.above[0].value), SAID
        if c.colors:
            params["color"], prov["color"] = c.colors[0].value, SAID
        cmd = Command(op, params, prov, clause=c.text, span=c.span)
        if c.times:
            cmd.params["time"] = c.times[0]
            prov["range"] = SAID
            cmd.scope.said_whole = isinstance(c.times[0].expr, Whole)
        if c.counts:
            n = int(c.counts[0].value)
            cmd.params["count"], prov["count"] = n, SAID
            cmd.scope.count_hint = n
        elif c.ones:
            cmd.scope.count_hint = 1
        tracks = [t.value for t in c.tracks if t.kind == "track"]
        if any(t.kind == "game_track" for t in c.tracks):
            return _Result(c, "unmatched")
        if tracks:
            # 2.1의 찾기는 목소리로 고른 소리만 듣는다. 말한 트랙만 골라 듣지 못하므로, 늘 막히는 카드 대신 먼저 알린다
            chip = Chip("example:pauses" if op == "mark_pauses" else "example:spikes")
            return _Result(c, "reply", reply=Reply("find_track", {"tracks": tracks}, [chip]), consumed=[c.span])
        consumed = [x.span for x in c.objects + c.verb("mark", "find", "jump") + c.times + c.colors[:1] + c.min_s
                    + c.above + c.counts + c.ones + c.tracks + now]
        return _Result(c, "command", command=cmd, consumed=consumed)

    # ── 시간 풀기 ─────────────────────────────────────────────────────

    def _resolve(self, c: Command, ctx: AssistContext):
        tc = ctx.time
        if c.op == "mark":
            return self._resolve_mark(c, ctx)
        if c.op == "jump_to":
            expr = c.params.pop("time").expr
            if isinstance(expr, (RangeExpr, Relative, Whole, InOut)):
                rr = resolve_range(expr, tc)
                frame, notes = rr.lo, rr.notes
            else:
                p = resolve_point(expr, tc)
                frame, notes = p.frame, [p.note] if p.note else []
            # "끝으로 가줘": 끝 프레임(들어가지 않음)이 아니라 마지막 프레임으로 (Lua jump_to는 끝을 밖으로 본다)
            frame = max(tc.start, min(frame, tc.end - 1))
            c.params["frame"] = frame
            c.notes += notes
            c.scope.range, c.scope.range_src = (frame, frame + 1), "point"
            return c
        if c.op in ("mark_pauses", "mark_spikes", "clear_marks"):
            t = c.params.pop("time", None)
            if t is not None:
                self._resolve_scope(c, t, ctx, find=c.op != "clear_marks")
            return c
        return c

    def _resolve_scope(self, c: Command, t: TimeToken, ctx: AssistContext, *, find: bool) -> None:
        tc = ctx.time
        expr = t.expr
        s = c.scope
        if isinstance(expr, Whole):
            s.range, s.range_src, s.said_whole = (tc.start, tc.end), "whole", True
            return
        if isinstance(expr, InOut):
            # In~Out은 기능 점검에서 읽을 수 있다고 확인된 뒤에만, 찾기(쉬는 곳·튀는 소리)에만
            if not find or ctx.cap("in_out") is not True:
                raise TimeError("in_out")
            c.params["scope"], c.provenance["scope"] = "in_out", SAID
            s.range_src = "in_out"
            return
        if isinstance(expr, (RangeExpr, Relative)):
            rr = resolve_range(expr, tc)
            s.range, s.range_src = (rr.lo, rr.hi), "relative" if isinstance(expr, Relative) else "said"
            c.notes += rr.notes
            return
        # 점 하나 (쯤, 여기, 3분 20초): 찾기는 앞뒤 5초 창, 지우기는 앞뒤 0.5초
        point = expr.point if isinstance(expr, Around) else expr
        p = resolve_point(point, tc)
        if p.note:
            c.notes.append(p.note)
        half = tc.frames(AROUND_S if find else POINT_TOL_S)
        s.range = (max(tc.start, p.frame - half), min(tc.end, p.frame + half + (0 if find else 1)))
        s.range_src = "around" if find else "point"
        if find and s.count_hint is None:
            s.count_hint = 1
        if find:
            c.notes.append(("around", {"seconds": AROUND_S}))

    def _resolve_mark(self, c: Command, ctx: AssistContext) -> Command:
        tc = ctx.time
        items = []
        lo_all, hi_all = None, None
        for it in c.params["items"]:
            expr = it.pop("time").expr
            if isinstance(expr, (RangeExpr, Relative, Whole)):
                rr = resolve_range(expr, tc)
                at, end = rr.lo, rr.hi
                c.notes += rr.notes
            elif isinstance(expr, InOut):
                raise TimeError("in_out")
            else:
                point = expr.point if isinstance(expr, Around) else expr
                p = resolve_point(point, tc, allow_end=True)
                at = min(p.frame, tc.end - 1)
                end = at + 1
                if p.note:
                    c.notes.append(p.note)
            color = it.get("color") or ctx.default_color or "Green"
            items.append({"at": at, "end": end, "color": color, "name": (it.get("name") or DEFAULT_MARK_NAME)[:MAX_NAME],
                          "note": it.get("note"), "src": it["src"]})
            lo_all = at if lo_all is None else min(lo_all, at)
            hi_all = end if hi_all is None else max(hi_all, end)
        if len(items) > MAX_MARK_ITEMS:
            c.notes.append(("too_many_marks", {"n": len(items), "cap": MAX_MARK_ITEMS}))
            items = items[:MAX_MARK_ITEMS]
        c.params["items"] = items
        c.scope.range = (lo_all, hi_all) if lo_all is not None else None
        c.scope.range_src = "point" if all(i["end"] - i["at"] <= 1 for i in items) else "said"
        c.scope.count_hint = len(items)
        return c


# ── 도우미 ────────────────────────────────────────────────────────────


_NOW_PLACE_RE = re.compile(r"\s+(?:위치|자리|앞뒤|전후|쯤|근처|부근|즈음)")


def _adverb_now(c: _Clause, t: TimeToken) -> bool:
    """마디 맨 앞, 조사 없이 빈칸이 뒤따르는 "지금" (자리 낱말이 뒤에 오면 재생 위치 그대로)."""
    if not isinstance(t.expr, Playhead) or t.text != "지금" or t.start != c.start:
        return False
    after = c.text[t.end - c.start:]
    return bool(after[:1].isspace()) and not _NOW_PLACE_RE.match(after)


def _neighbour(results: List[_Result], i: int, ops: Sequence[str], prefer_prev: bool = False) -> Optional[_Result]:
    prev = next((results[j] for j in range(i - 1, -1, -1) if results[j].command is not None), None)
    nxt = next((results[j] for j in range(i + 1, len(results)) if results[j].command is not None), None)
    order = (prev, nxt) if prefer_prev or prev is not None else (nxt, prev)
    for r in order:
        if r is not None and r.command.op in ops:
            return r
    return None


def _merge_times(target: _Result, r: _Result) -> None:
    items = target.command.params["items"]
    base = items[0]
    colors = [f.value for f in r.clause.colors]
    new = []
    for i, t in enumerate(r.clause.times):
        color = colors[i] if i < len(colors) else base["color"]
        new.append({"time": t, "color": color, "name": base["name"], "note": base["note"],
                    "src": {"at": SAID, "color": SAID if color else DEFAULT, "name": base["src"]["name"]}})
    items += new
    items.sort(key=lambda it: it["time"].start)
    target.consumed += r.consumed
    r.kind = "merged"


def _merge_modifier(target: _Result, r: _Result) -> None:
    cmd = target.command
    c = r.clause
    used: List[Span] = list(r.consumed)
    colors = list(c.colors)
    if cmd.op == "mark":
        free = [it for it in cmd.params["items"] if not it.get("color")]
        if colors and free:
            col = colors.pop(0)
            for it in free:
                it["color"], it["src"]["color"] = col.value, SAID
            used.append(col.span)
        if c.quotes:
            q = c.quotes[0]
            for it in cmd.params["items"]:
                it["name"] = q.value[:MAX_NAME]
                it["note"] = q.value
                it["src"]["name"] = SAID
    elif cmd.op in ("mark_pauses", "mark_spikes"):
        if colors and "color" not in cmd.params:
            col = colors.pop(0)
            cmd.params["color"], cmd.provenance["color"] = col.value, SAID
            used.append(col.span)
        if c.min_s and cmd.op == "mark_pauses" and "min_s" not in cmd.params:
            cmd.params["min_s"], cmd.provenance["min_s"] = float(c.min_s[0].value), SAID
        if c.above and cmd.op == "mark_spikes" and "above_lu" not in cmd.params:
            cmd.params["above_lu"], cmd.provenance["above_lu"] = float(c.above[0].value), SAID
        if c.counts and "count" not in cmd.params:
            cmd.params["count"], cmd.provenance["count"] = int(c.counts[0].value), SAID
            cmd.scope.count_hint = int(c.counts[0].value)
    elif cmd.op == "clear_marks":
        if colors:
            cmd.params["colors"] = sorted(set(cmd.params.get("colors", [])) | {f.value for f in colors})
            cmd.provenance["colors"] = SAID
            used += [f.span for f in colors]
            colors = []
    target.consumed += used
    r.consumed = used
    r.kind = "merged"


def _names(c: _Clause) -> Tuple[Optional[str], Optional[str], str]:
    """표시 이름과 메모: 따옴표 안의 글. 없으면 기본 이름 ("표시")."""
    if c.quotes:
        body = c.quotes[0].value
        return body[:MAX_NAME], body, SAID
    return None, None, DEFAULT


def audio_mark_name(db: float, direction: Optional[str]) -> str:
    """권하는 노란 표시의 이름 ("여기 6dB 줄이기"). 리졸브에 들어가는 글이라 여기서 만든다."""
    word = AUDIO_VERB_WORD.get(direction or "down", AUDIO_VERB_WORD["down"])
    shown = int(db) if float(db) == int(db) else db
    return AUDIO_MARK_NAME.format(db=shown, verb=word)[:MAX_NAME]


def _rename_audio(cmd: Command) -> None:
    name = audio_mark_name(cmd.params.get("db", K.BARE_AMOUNT_DB), cmd.params.get("direction"))
    for it in cmd.params["items"]:
        it["name"] = name
        it["src"]["name"] = DEFAULT


def _examples() -> List[Chip]:
    return [Chip("example:mark_time", {"time": "3분 20초"}), Chip("example:range_pauses"), Chip("example:undo")]


def _what_to_do(parsed: Parsed) -> Question:
    times = [t for r in parsed.results for t in r.clause.times]
    if times:
        raw = times[0].text
        chips = [Chip("example:mark_time", {"time": raw}), Chip("example:jump_time", {"time": raw})]
        return Question("what_to_do_time", {"time": raw}, chips)
    return Question("what_to_do", {}, _examples())


def _status(ctx: AssistContext) -> Dict[str, Any]:
    tc = ctx.time
    info = ctx.info
    return {"timeline": getattr(info, "timeline", None) or (tc.name if tc else ""),
            "length_s": tc.length_s if tc else None, "fps": tc.fps_text if tc else None,
            "playhead_s": tc.seconds_of(tc.playhead) if tc and tc.playhead is not None else None,
            "playhead_tc": tc.tc_of(tc.playhead) if tc and tc.playhead is not None else None}


def _time_reply(exc: TimeError, c: Command, text: str, ctx: AssistContext) -> BrainResult:
    d = dict(exc.detail)
    tc = ctx.time
    if exc.code == "ambiguous":
        raw = d.get("raw", "")
        chips = [Chip("tc_elapsed", {"text": text, "raw": raw, "seconds": d.get("elapsed_s")}),
                 Chip("tc_resolve", {"text": text, "raw": raw, "tc": d.get("tc"), "seconds": d.get("tc_s")})]
        return Question("tc_ambiguous", d, chips)
    if exc.code == "outside":
        d["length_s"] = tc.length_s if tc else None
    if exc.code == "in_out":
        d["allowed"] = ctx.cap("in_out")
    return Reply("time_" + exc.code, d)
