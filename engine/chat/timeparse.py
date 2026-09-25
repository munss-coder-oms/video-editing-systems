"""한국어 시간 말 → 타임라인 프레임 (설계 B4.2 "Time grammar").

찾기(find_times)와 풀기(resolve_*)를 나눈다.
- find_times(글)은 글 속의 시간 말을 모두 찾아 자리(span)와 뜻(식)을 돌려준다. 타임라인을 몰라도 된다.
  그래서 못 알아들은 부분을 찾을 때(leftover)도 같은 찾기를 쓴다.
- resolve_point / resolve_range는 식을 지금 타임라인(TimeContext)으로 절대 프레임으로 바꾼다.

시간 말
| 말 | 뜻 |
| 3분 20초, 3분20초, 3:20, 200초, 1시간 2분 5초, 3분 반, 3분 20 | 타임라인 시작부터 지난 시간 |
| 01:03:20:00 (네 칸) | 리졸브 타임코드 → 시작 타임코드를 뺀다 |
| 1:03:20 (세 칸) | 지난 시간. 다만 그 시간이 타임라인 밖이고 리졸브 타임코드로 보면 안쪽이면 타임코드로 본다 |
|                 | (카드: "리졸브 시간 01:03:20:00 → 영상 3분 20초로 봤어요"). 둘 다 안쪽이면 되묻는다 |
| 여기, 지금, 재생 위치 | 재생 위치 |
| 여기서부터 10초 / 여기 앞뒤 2초 | [재생 위치, +10초) / [재생 위치 −2초, +2초) |
| 처음부터 5분, 처음 5분 / 끝에서 30초, 마지막 30초 | [0, 5분) / [끝 −30초, 끝) |
| A부터 B까지, A~B, A-B, A에서 B, A와 B 사이 | 구간 (5~6분처럼 앞의 단위가 없으면 뒤의 단위를 쓴다) |
| 쯤, 근처 | 찾는 말(쉬는 곳·튀는 소리)에만 ±5초 창. 표시 넣기에는 그 자리 |
| In~Out, 표시한 구간 | 리졸브의 In~Out (기능 점검 뒤에만) |
| 전체, 처음부터 끝까지 | 전체 (전체라고 말했으니 "전체에 적용돼요" 경고를 띄우지 않는다) |

프레임: 지난 시간 s초 → 시작 프레임 + round(s × fps). fps는 29.97이면 30000/1001처럼 정확한 값.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

from ..resolve_link.timecode import frames_to_tc, tc_offset, tc_to_frames

AROUND_S = 5.0  # "쯤"의 찾는 창 (앞뒤)

# ── 식 ────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Clock:
    """타임라인 시작부터 지난 시간 (초). raw는 사용자가 쓴 글."""

    seconds: float
    raw: str = ""
    has_unit: bool = True  # "5~6분"의 5처럼 단위 없는 숫자는 False (뒤의 단위를 빌린다)
    unit: str = "s"  # 가장 작은 단위: h / m / s (단위를 빌려줄 때)


@dataclass(frozen=True)
class Hms3:
    """세 칸 "1:03:20": 지난 시간일 수도, 리졸브 타임코드(01:03:20:00)일 수도 있다."""

    h: int
    m: int
    s: float
    raw: str = ""

    @property
    def seconds(self) -> float:
        return self.h * 3600 + self.m * 60 + self.s

    @property
    def tc(self) -> str:
        return f"{self.h:02d}:{self.m:02d}:{int(self.s):02d}:00"


@dataclass(frozen=True)
class TC4:
    """네 칸 리졸브 타임코드 "01:03:20:00" (드롭 프레임이면 마지막이 ';')."""

    tc: str
    raw: str = ""


@dataclass(frozen=True)
class Playhead:
    raw: str = ""


@dataclass(frozen=True)
class Edge:
    """처음(start) 또는 끝(end)."""

    which: str
    raw: str = ""


Point = Any  # Clock | Hms3 | TC4 | Playhead | Edge


@dataclass(frozen=True)
class RangeExpr:
    a: Point
    b: Point


@dataclass(frozen=True)
class Around:
    """쯤·근처: 찾는 말에는 ±5초 창, 표시 넣기에는 그 자리."""

    point: Point


@dataclass(frozen=True)
class Relative:
    """기준점에서 앞(before)·뒤(after)로 몇 초 ("여기서부터 10초", "여기 앞뒤 2초", "끝에서 30초")."""

    anchor: Point
    before: float
    after: float


@dataclass(frozen=True)
class InOut:
    raw: str = ""


@dataclass(frozen=True)
class Whole:
    raw: str = ""


@dataclass
class TimeToken:
    start: int
    end: int
    text: str
    expr: Any

    @property
    def span(self) -> Tuple[int, int]:
        return (self.start, self.end)

    @property
    def is_range(self) -> bool:
        return isinstance(self.expr, (RangeExpr, Relative, InOut, Whole))


# ── 찾기 ──────────────────────────────────────────────────────────────

_NUM = r"\d+(?:\.\d+)?"
_TC4_RE = re.compile(r"(?<![\d:;.])(\d{1,2})[:;.](\d{2})[:;.](\d{2})([:;])(\d{2})(?![\d:;])")
_HMS3_RE = re.compile(r"(?<![\d:;.])(\d{1,2}):(\d{2}):(\d{2}(?:\.\d+)?)(?![\d:;])")
_MS2_RE = re.compile(r"(?<![\d:;.])(\d{1,3}):(\d{2}(?:\.\d+)?)(?![\d:;])")
# "1시간 2분 5초", "3분20초", "3분 반", "200초", "3분 20" (초를 생략)
_KO_RE = re.compile(
    r"(?<![\d.])"
    r"(?:(?P<h>\d+)\s*시간(?:\s*(?P<hh>반))?)?\s*"
    r"(?:(?P<m>\d+)\s*분(?:\s*(?P<mh>반))?)?\s*"
    r"(?:(?P<s>" + _NUM + r")\s*초|(?P<sb>\d{1,2})(?=\s*(?:에|에서|쯤|부터|까지|으로|로|~|-|$|\s*[,;.!?])))?"
)
_BARE_RE = re.compile(r"(?<![\d.:])(" + _NUM + r")(?=\s*(?:~|〜|-|–|부터|에서)\s*\d)")
_PLAYHEAD_RE = re.compile(r"여기|지금|재생\s*위치|현재\s*위치|이\s*자리|이곳|이\s*부분|이\s*위치|재생\s*헤드|플레이\s*헤드")
_START_RE = re.compile(r"(?:맨\s*)?처음|시작\s*(?=부터|에서)|맨\s*앞")
_END_RE = re.compile(r"(?:맨\s*)?끝|마지막|맨\s*뒤")
_INOUT_RE = re.compile(r"In\s*[~\-]\s*Out|인\s*아웃|IN\s*OUT|in\s*out|I\s*/\s*O\s*구간|표시한\s*구간|지정한\s*구간|인아웃",
                       re.IGNORECASE)
_WHOLE_RE = re.compile(r"처음부터\s*끝까지|전체|전부|타임라인\s*전체|영상\s*전체")
_AROUND_RE = re.compile(r"\s*(?:쯤|근처|즈음|정도|언저리|부근)")
_CONNECT_RE = re.compile(r"\s*(?:~|〜|–|-|에서부터|서부터|부터|에서|와|과|하고|랑|이랑)\s*")
_UNTIL_RE = re.compile(r"\s*(?:까지|사이(?:에서|에)?)")
_BOTH_RE = re.compile(r"\s*(?:앞뒤|전후|앞\s*뒤로|좌우)\s*(" + _NUM + r")\s*초")
_AFTER_RE = re.compile(r"\s*(?:에서부터|서부터|부터|에서)\s*(?:(?P<m>\d+)\s*분\s*)?(?:(?P<s>" + _NUM + r")\s*초)?(?P<dur>\s*(?:동안|간))?")
_FIRST_N_RE = re.compile(r"(?:맨\s*)?처음\s*(?:부터\s*)?(?:(?P<m>\d+)\s*분\s*)?(?:(?P<s>" + _NUM + r")\s*초)?")
_LAST_N_RE = re.compile(r"(?:(?:맨\s*)?끝\s*(?:에서|부터)|마지막)\s*(?:(?P<m>\d+)\s*분\s*)?(?:(?P<s>" + _NUM + r")\s*초)?")


def _num(text: Optional[str]) -> float:
    return float(text) if text else 0.0


def _points(text: str, masked: Sequence[Tuple[int, int]]) -> List[TimeToken]:
    """겹치지 않는 시간 점들 (앞에서부터, 긴 것 먼저)."""
    taken: List[Tuple[int, int]] = list(masked)
    out: List[TimeToken] = []

    def free(a: int, b: int) -> bool:
        return all(b <= x or a >= y for x, y in taken)

    def add(a: int, b: int, expr) -> None:
        taken.append((a, b))
        out.append(TimeToken(a, b, text[a:b], expr))

    for m in _TC4_RE.finditer(text):
        if free(*m.span()):
            hh, mm, ss, sep, ff = m.groups()
            add(m.start(), m.end(), TC4(f"{int(hh):02d}:{mm}:{ss}{sep}{ff}", m.group(0)))
    for m in _HMS3_RE.finditer(text):
        if free(*m.span()):
            add(m.start(), m.end(), Hms3(int(m.group(1)), int(m.group(2)), float(m.group(3)), m.group(0)))
    for m in _MS2_RE.finditer(text):
        if free(*m.span()):
            add(m.start(), m.end(), Clock(int(m.group(1)) * 60 + float(m.group(2)), m.group(0), unit="s"))
    for m in _KO_RE.finditer(text):
        g = m.groupdict()
        if not (g["h"] or g["m"] or g["s"]):
            continue
        a, b = m.start(), m.end()
        # 뒤의 공백은 빼고
        while b > a and text[b - 1].isspace():
            b -= 1
        while a < b and text[a].isspace():
            a += 1
        if b <= a or not free(a, b):
            continue
        if g["sb"] and not g["m"]:
            continue  # "3분 20"의 20은 분 뒤에서만 초로 본다
        secs = _num(g["h"]) * 3600 + _num(g["m"]) * 60 + _num(g["s"]) + _num(g["sb"])
        if g["hh"]:
            secs += 1800
        if g["mh"]:
            secs += 30
        unit = "s" if (g["s"] or g["sb"] or g["mh"]) else ("m" if g["m"] else "h")
        add(a, b, Clock(secs, text[a:b], unit=unit))
    for m in _BARE_RE.finditer(text):
        if free(*m.span()):
            add(m.start(), m.end(), Clock(float(m.group(1)), m.group(0), has_unit=False))
    for m in _PLAYHEAD_RE.finditer(text):
        if free(*m.span()):
            add(m.start(), m.end(), Playhead(m.group(0)))
    out.sort(key=lambda t: t.start)
    return out


def _borrow_unit(a: Clock, b: Point) -> Clock:
    """"5~6분": 앞의 단위 없는 5는 뒤의 단위(분)를 쓴다."""
    if a.has_unit or not isinstance(b, Clock):
        return a
    scale = {"h": 3600.0, "m": 60.0, "s": 1.0}[b.unit if b.unit in ("h", "m") else "s"]
    # "3분 15~25초"는 앞이 이미 단위가 있으므로 여기 오지 않는다
    return Clock(a.seconds * scale, a.raw, True, b.unit)


def _inherit_high_units(a: Point, b: Point) -> Point:
    """"3분 15초~25초"(또는 "3분 15~25초"): 뒤에 분이 없고 앞보다 작으면 앞의 분을 붙인다."""
    if isinstance(a, Clock) and isinstance(b, Clock) and b.seconds < a.seconds and "분" not in b.raw \
            and "시간" not in b.raw and ":" not in b.raw and b.seconds < 60:
        whole_minutes = int(a.seconds // 60) * 60
        cand = whole_minutes + b.seconds
        if cand > a.seconds:
            return Clock(cand, b.raw, True, "s")
    return b


def find_times(text: str, masked: Sequence[Tuple[int, int]] = ()) -> List[TimeToken]:
    """글 속의 시간 말 (점, 구간, 쯤, 앞뒤, 처음·끝, In~Out, 전체). masked 자리는 보지 않는다."""
    masked = list(masked)
    specials: List[TimeToken] = []

    def free(a: int, b: int) -> bool:
        return all(b <= x or a >= y for x, y in masked + [t.span for t in specials])

    for m in _INOUT_RE.finditer(text):
        if free(*m.span()):
            specials.append(TimeToken(m.start(), m.end(), m.group(0), InOut(m.group(0))))
    for m in _WHOLE_RE.finditer(text):
        if free(*m.span()):
            specials.append(TimeToken(m.start(), m.end(), m.group(0), Whole(m.group(0))))
    for m in _FIRST_N_RE.finditer(text):
        if (m.group("m") or m.group("s")) and free(*m.span()):
            secs = _num(m.group("m")) * 60 + _num(m.group("s"))
            end = m.end()
            u = _UNTIL_RE.match(text, end)
            if u:
                end = u.end()
            specials.append(TimeToken(m.start(), end, text[m.start():end], Relative(Edge("start"), 0.0, secs)))
    for m in _LAST_N_RE.finditer(text):
        if (m.group("m") or m.group("s")) and free(*m.span()):
            secs = _num(m.group("m")) * 60 + _num(m.group("s"))
            specials.append(TimeToken(m.start(), m.end(), m.group(0), Relative(Edge("end"), secs, 0.0)))
    points = _points(text, masked + [t.span for t in specials])
    # 처음·끝 낱말 ("처음부터 여기까지", "여기부터 끝까지")
    for regex, which in ((_START_RE, "start"), (_END_RE, "end")):
        for m in regex.finditer(text):
            span = m.span()
            if all(span[1] <= x or span[0] >= y for x, y in masked + [t.span for t in specials + points]):
                points.append(TimeToken(span[0], span[1], m.group(0), Edge(which, m.group(0))))
    points.sort(key=lambda t: t.start)

    out: List[TimeToken] = []
    i = 0
    n_pts = len(points)

    def skip_to(e: int, j: int) -> int:
        """e까지 먹은 점들은 건너뛴다 ("여기 앞뒤 2초"의 2초)."""
        while j < n_pts and points[j].start < e:
            j += 1
        return j

    while i < n_pts:
        p = points[i]
        start, end, expr = p.start, p.end, p.expr
        # A부터 N초 (동안): 기간. "3분 20초부터 10초", "여기서부터 10초", "5분부터 30초 동안"
        after = _AFTER_RE.match(text, end)
        if after and (after.group("m") or after.group("s")) and not isinstance(expr, Edge) \
                and not _UNTIL_RE.match(text, after.end()):
            n = _num(after.group("m")) * 60 + _num(after.group("s"))
            is_duration = bool(after.group("dur")) or isinstance(expr, Playhead) or \
                (isinstance(expr, (Clock, Hms3, TC4)) and not after.group("m") and
                 (isinstance(expr, TC4) or n <= _secs(expr)))
            if isinstance(expr, Clock) and not expr.has_unit:
                is_duration = False
            if is_duration:
                e = after.end()
                if after.group("dur"):
                    pass
                else:
                    while e > end and text[e - 1].isspace():
                        e -= 1
                out.append(TimeToken(start, e, text[start:e], Relative(expr, 0.0, n)))
                i = skip_to(e, i + 1)
                continue
        # 구간: A ~ B (까지|사이)
        if i + 1 < n_pts:
            q = points[i + 1]
            c = _CONNECT_RE.match(text, end)
            if c and c.end() == q.start and _range_ok(p.expr, q.expr, text[c.start():c.end()]):
                a = _borrow_unit(p.expr, q.expr) if isinstance(p.expr, Clock) else p.expr
                b = _inherit_high_units(a, q.expr)
                e = q.end
                u = _UNTIL_RE.match(text, e)
                if u:
                    e = u.end()
                out.append(TimeToken(start, e, text[start:e], RangeExpr(a, b)))
                i = skip_to(e, i + 2)
                continue
        if isinstance(expr, Clock) and not expr.has_unit:
            i += 1
            continue  # 구간이 아닌 단위 없는 숫자는 시간이 아니다
        # 앞뒤 N초
        both = _BOTH_RE.match(text, end)
        if both and not isinstance(expr, Edge):
            n = float(both.group(1))
            out.append(TimeToken(start, both.end(), text[start:both.end()], Relative(expr, n, n)))
            i = skip_to(both.end(), i + 1)
            continue
        # 쯤
        around = _AROUND_RE.match(text, end)
        if around and not isinstance(expr, Edge):
            out.append(TimeToken(start, around.end(), text[start:around.end()], Around(expr)))
            i += 1
            continue
        if isinstance(expr, Edge):
            i += 1
            continue  # 처음·끝 낱말만으로는 시간이 아니다 ("끝" 같은 말이 흔해서)
        out.append(p)
        i += 1
    out += specials
    out.sort(key=lambda t: t.start)
    return out


def _secs(expr: Point) -> float:
    if isinstance(expr, (Clock, Hms3)):
        return expr.seconds
    return 0.0


def _range_ok(a: Point, b: Point, connector: str) -> bool:
    """두 점을 구간으로 이을지. "와/하고/랑"은 뒤에 "사이"가 있을 때만 (호출하는 쪽이 따로 본다)."""
    c = connector.strip()
    if isinstance(a, Edge) and isinstance(b, Edge):
        return a.which == "start" and b.which == "end"
    if c in ("와", "과", "하고", "랑", "이랑"):
        return False
    if c == "에서" and isinstance(b, Edge):
        return b.which == "end"
    if isinstance(a, Edge) and a.which == "end":
        return False
    if isinstance(b, Edge) and b.which == "start":
        return False
    return True


def find_between(text: str, tokens: List[TimeToken]) -> List[TimeToken]:
    """"5분과 6분 사이": 두 점을 와/과/하고/랑 + 사이로 이은 구간 (find_times가 놓친 것만 더한다)."""
    out = list(tokens)
    pts = [t for t in tokens if not t.is_range]
    for p, q in zip(pts, pts[1:]):
        c = re.compile(r"\s*(?:와|과|하고|랑|이랑)\s*").match(text, p.end)
        if c and c.end() == q.start:
            u = re.compile(r"\s*사이").match(text, q.end)
            if u:
                e = u.end()
                out = [t for t in out if t is not p and t is not q]
                a = _borrow_unit(p.expr, q.expr) if isinstance(p.expr, Clock) else p.expr
                out.append(TimeToken(p.start, e, text[p.start:e], RangeExpr(a, q.expr)))
    out.sort(key=lambda t: t.start)
    return out


def scan(text: str, masked: Sequence[Tuple[int, int]] = ()) -> List[TimeToken]:
    """find_times + "A와 B 사이"."""
    return find_between(text, find_times(text, masked))


# ── 풀기 ──────────────────────────────────────────────────────────────


@dataclass
class TimeContext:
    """지금 타임라인: 프레임 속도(정확한 값), 시작·끝 프레임(절대, 끝은 들어가지 않음), 재생 위치."""

    fps: float
    start: int
    end: int
    fps_text: str = ""
    drop: bool = False
    start_tc: Optional[str] = None
    playhead: Optional[int] = None
    name: str = ""

    @property
    def length(self) -> int:
        return max(0, self.end - self.start)

    @property
    def length_s(self) -> float:
        return self.length / self.fps if self.fps else 0.0

    def frame_of(self, seconds: float) -> int:
        """지난 시간(초) → 절대 프레임."""
        return self.start + int(round(float(seconds) * self.fps))

    def seconds_of(self, frame: int) -> float:
        return (int(frame) - self.start) / self.fps if self.fps else 0.0

    def frames(self, seconds: float) -> int:
        return int(round(float(seconds) * self.fps))

    def tc_of(self, frame: int) -> str:
        """절대 프레임 → 리졸브 타임코드 (시작 타임코드 기준)."""
        fps = self.fps_text or self.fps
        try:
            base = tc_to_frames(self.start_tc, fps, self.drop) if self.start_tc else self.start
            return frames_to_tc(base + (int(frame) - self.start), fps, self.drop)
        except (ValueError, TypeError):
            return ""

    def inside(self, frame: int) -> bool:
        return self.start <= frame < self.end


class TimeError(Exception):
    """시간을 풀 수 없음. code: outside / no_playhead / ambiguous / reversed / no_timeline / in_out / empty."""

    def __init__(self, code: str, **detail: Any) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


@dataclass
class Resolved:
    """풀린 점: 절대 프레임과 어떻게 봤는지 (elapsed / tc / playhead / start / end)."""

    frame: int
    how: str
    note: Optional[Tuple[str, Dict[str, Any]]] = None  # 카드에 적을 한 줄 (타임코드로 봤어요 등)


@dataclass
class ResolvedRange:
    lo: int
    hi: int
    src: str  # said / around / in_out / whole / relative
    notes: List[Tuple[str, Dict[str, Any]]] = field(default_factory=list)
    clipped_end: bool = False  # 타임라인 끝에서 잘랐음


def _tc_frame(tc: str, ctx: TimeContext) -> int:
    fps = ctx.fps_text or ctx.fps
    if ctx.start_tc:
        return ctx.start + tc_offset(tc, ctx.start_tc, fps, ctx.drop)
    return tc_to_frames(tc, fps, ctx.drop or (";" in tc))


def resolve_point(expr: Point, ctx: TimeContext, *, allow_end: bool = False) -> Resolved:
    """점 하나 → 절대 프레임. 타임라인 밖이면 TimeError("outside")."""
    if isinstance(expr, Around):
        return resolve_point(expr.point, ctx, allow_end=allow_end)
    if isinstance(expr, Playhead):
        if ctx.playhead is None:
            raise TimeError("no_playhead")
        return Resolved(int(ctx.playhead), "playhead")
    if isinstance(expr, Edge):
        return Resolved(ctx.start if expr.which == "start" else ctx.end, expr.which)
    last_ok = (lambda f: ctx.start <= f <= ctx.end) if allow_end else ctx.inside
    if isinstance(expr, Clock):
        f = ctx.frame_of(expr.seconds)
        if not last_ok(f):
            raise TimeError("outside", seconds=expr.seconds, raw=expr.raw)
        return Resolved(f, "elapsed")
    if isinstance(expr, TC4):
        try:
            f = _tc_frame(expr.tc, ctx)
        except ValueError:
            raise TimeError("bad_tc", raw=expr.raw) from None
        if not last_ok(f):
            raise TimeError("outside", tc=expr.tc, raw=expr.raw)
        return Resolved(f, "tc")
    if isinstance(expr, Hms3):
        elapsed = ctx.frame_of(expr.seconds)
        e_ok = last_ok(elapsed)
        tc_f: Optional[int] = None
        try:
            tc_f = _tc_frame(expr.tc, ctx) if expr.s == int(expr.s) else None
        except ValueError:
            tc_f = None
        t_ok = tc_f is not None and last_ok(tc_f)
        if e_ok and t_ok and tc_f != elapsed:
            raise TimeError("ambiguous", raw=expr.raw, elapsed_s=expr.seconds, tc=expr.tc,
                            tc_s=ctx.seconds_of(tc_f))
        if e_ok:
            return Resolved(elapsed, "elapsed")
        if t_ok:
            return Resolved(tc_f, "tc", ("tc_read", {"tc": expr.tc, "seconds": ctx.seconds_of(tc_f)}))
        raise TimeError("outside", seconds=expr.seconds, raw=expr.raw)
    raise TimeError("empty")


def resolve_range(expr: Any, ctx: TimeContext, *, find: bool = False) -> ResolvedRange:
    """구간 식 → [lo, hi). 점 하나("3분에")는 구간이 아니다 (호출하는 쪽이 점으로 쓴다).

    find=True(찾는 말)이면 "3분쯤"을 ±5초 창으로 본다. 끝이 타임라인 밖이면 끝에서 자르고 적는다.
    """
    notes: List[Tuple[str, Dict[str, Any]]] = []
    if isinstance(expr, Whole):
        return ResolvedRange(ctx.start, ctx.end, "whole")
    if isinstance(expr, InOut):
        raise TimeError("in_out")
    if isinstance(expr, Around):
        p = resolve_point(expr.point, ctx)
        if p.note:
            notes.append(p.note)
        half = ctx.frames(AROUND_S)
        return ResolvedRange(max(ctx.start, p.frame - half), min(ctx.end, p.frame + half), "around", notes)
    if isinstance(expr, Relative):
        p = resolve_point(expr.anchor, ctx, allow_end=True)
        if p.note:
            notes.append(p.note)
        lo = p.frame - ctx.frames(expr.before)
        hi = p.frame + ctx.frames(expr.after)
        return _clip(lo, hi, ctx, "relative", notes)
    if isinstance(expr, RangeExpr):
        a = resolve_point(expr.a, ctx, allow_end=False) if not isinstance(expr.a, Edge) else \
            Resolved(ctx.start if expr.a.which == "start" else ctx.end, expr.a.which)
        try:
            b = resolve_point(expr.b, ctx, allow_end=True)
        except TimeError as exc:
            if exc.code != "outside":
                raise
            b = Resolved(ctx.end, "end")  # 끝이 타임라인 밖: 끝까지 (잘랐다고 적는다)
            notes.append(("clipped_end", {}))
        for r in (a, b):
            if r.note:
                notes.append(r.note)
        if b.frame <= a.frame:
            raise TimeError("reversed", a=ctx.seconds_of(a.frame), b=ctx.seconds_of(b.frame))
        rr = _clip(a.frame, b.frame, ctx, "said", notes)
        return rr
    raise TimeError("empty")


def _clip(lo: int, hi: int, ctx: TimeContext, src: str, notes: List[Tuple[str, Dict[str, Any]]]) -> ResolvedRange:
    clipped = False
    if lo < ctx.start:
        lo = ctx.start
    if hi > ctx.end:
        hi, clipped = ctx.end, True
    if hi <= lo:
        raise TimeError("outside")
    if clipped and ("clipped_end", {}) not in notes:
        notes.append(("clipped_end", {}))
    return ResolvedRange(lo, hi, src, notes, clipped_end=clipped)


def context_from_info(info: Any, playhead: Optional[int] = None) -> Optional[TimeContext]:
    """ops.TimelineInfo(또는 timeline_info 답 사전) → TimeContext. 시작·끝·속도를 모르면 None.

    재생 위치는 timeline_info의 current_tc(리졸브가 준 글)에서 센다 (주어지면 그것을 쓴다).
    """
    from ..resolve_link.timecode import is_drop_frame
    from ..timeline.map import exact_fps

    get = (lambda k: info.get(k)) if isinstance(info, dict) else (lambda k: getattr(info, k, None))
    fps_text = get("fps")
    fps = exact_fps(fps_text)
    start, end = get("start_frame"), get("end_frame")
    if fps is None or not isinstance(start, int) or not isinstance(end, int) or end <= start:
        return None
    start_tc, cur = get("start_tc"), get("current_tc")
    drop = is_drop_frame(fps_text, cur, start_tc, flag=get("drop_frame")) if fps_text else False
    if playhead is None and isinstance(cur, str) and isinstance(start_tc, str):
        try:
            playhead = start + tc_offset(cur, start_tc, fps_text, drop)
        except ValueError:
            playhead = None
    return TimeContext(fps=fps, start=start, end=end, fps_text=str(fps_text), drop=bool(drop),
                       start_tc=start_tc if isinstance(start_tc, str) else None, playhead=playhead,
                       name=get("timeline") or "")
