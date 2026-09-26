"""범위 지킴이 (설계 B4.3): 카드의 일이 부탁보다 크면 막거나 경고하고, 부탁보다 작으면 알린다.

| 경우 | 하는 일 |
|---|---|
| 바뀌는 자리가 말한 범위 밖 (0.5초 여유, "쯤"은 앞뒤 5초 창) | 막음: [리졸브에 넣기]를 누를 수 없다 |
| 바뀌는 트랙이 말한(또는 버튼에 정한) 트랙 밖 | 막음 |
| 말한 수("여기", "한 곳", "3개만")보다 많음 | 경고 (주황) |
| 전체에 적용되는데 "전체"라고 말하지 않음 | 경고 |
| 말한 범위가 타임라인의 절반보다 넓음 | 경고 |
| 못 알아들은 부분이 있음 | 경고: 알아들은 줄만 넣는다 |

쉬는 곳·튀는 소리는 계산할 때 이미 말한 범위 안쪽으로 잘랐으므로(설계 B2.4 5) 범위에 걸친 쉼 때문에 막히지 않는다.
화면 글은 app/companion/strings_ko.GUARD가 code로 고른다. 여기는 리졸브에 묻지 않는다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

TOLERANCE_S = 0.5
HALF = 0.5
BLOCK = "block"
WARN = "warn"


@dataclass
class GuardLine:
    code: str  # outside_range / outside_tracks / more_than_asked / whole / half / leftover
    level: str  # block / warn
    data: Dict[str, Any] = field(default_factory=dict)


@dataclass
class GuardResult:
    lines: List[GuardLine] = field(default_factory=list)

    @property
    def blocked(self) -> bool:
        return any(x.level == BLOCK for x in self.lines)

    @property
    def blocks(self) -> List[GuardLine]:
        return [x for x in self.lines if x.level == BLOCK]

    @property
    def warnings(self) -> List[GuardLine]:
        return [x for x in self.lines if x.level == WARN]

    def codes(self) -> List[str]:
        return [x.code for x in self.lines]

    def to_list(self) -> List[Dict[str, Any]]:
        return [{"code": x.code, "level": x.level, **x.data} for x in self.lines]


def _get(requested: Any, key: str, default: Any = None) -> Any:
    if requested is None:
        return default
    if isinstance(requested, dict):
        return requested.get(key, default)
    return getattr(requested, key, default)


def check(requested: Any, *, effects: Sequence[Tuple[int, int]], fps: float, tl_start: int, tl_end: int,
          count: int, tracks: Iterable[int] = (), slot_tracks: Optional[Iterable[int]] = None,
          whole: bool = False, leftovers: Sequence[str] = ()) -> GuardResult:
    """requested: 사용자가 말한 범위 (brain.RequestedScope 또는 그 사전: range, range_src, said_whole, tracks, count_hint).

    effects: 바뀌는 자리 [시작, 끝) 절대 프레임 목록 (표시 하나마다).
    whole: 이 일이 타임라인 전체에 적용되는지 (범위 없이 찾기·지우기).
    """
    out = GuardResult()
    fps = float(fps) if fps else 1.0
    tol = int(round(TOLERANCE_S * fps))
    rng = _get(requested, "range")
    src = _get(requested, "range_src", "none")
    if rng is not None and src != "whole":
        lo, hi = int(rng[0]), int(rng[1])
        outside = [e for e in effects if e[0] < lo - tol or e[1] > hi + tol]
        if outside:
            first = min(outside)
            out.lines.append(GuardLine("outside_range", BLOCK, {
                "n": len(outside), "lo_s": (lo - tl_start) / fps, "hi_s": (hi - tl_start) / fps,
                "at_s": (first[0] - tl_start) / fps}))
    said_tracks = _get(requested, "tracks")
    used = sorted(set(int(t) for t in tracks))
    if said_tracks:
        allowed = set(int(t) for t in said_tracks) | set(int(t) for t in (slot_tracks or ()))
        extra = [t for t in used if t not in allowed]
        if extra:
            out.lines.append(GuardLine("outside_tracks", BLOCK, {"tracks": extra, "said": sorted(said_tracks)}))
    hint = _get(requested, "count_hint")
    if isinstance(hint, int) and hint > 0 and count > hint:
        out.lines.append(GuardLine("more_than_asked", WARN, {"n": count, "hint": hint}))
    length_s = max(0.0, (tl_end - tl_start) / fps)
    if whole and not _get(requested, "said_whole", False) and count > 0:
        out.lines.append(GuardLine("whole", WARN, {"length_s": length_s}))
    elif rng is not None and src in ("said", "relative") and tl_end > tl_start:
        share = (int(rng[1]) - int(rng[0])) / float(tl_end - tl_start)
        if share > HALF and not _get(requested, "said_whole", False):
            out.lines.append(GuardLine("half", WARN, {"percent": int(round(share * 100)), "length_s": length_s}))
    if leftovers:
        out.lines.append(GuardLine("leftover", WARN, {"text": " · ".join(leftovers), "parts": list(leftovers)}))
    return out


def requested_dict(scope: Any) -> Optional[Dict[str, Any]]:
    """brain.RequestedScope → 제안에 적어 두는 사전 (결과 파일에도)."""
    if scope is None:
        return None
    rng = _get(scope, "range")
    return {"range": [int(rng[0]), int(rng[1])] if rng else None, "range_src": _get(scope, "range_src", "none"),
            "said_whole": bool(_get(scope, "said_whole", False)), "tracks": _get(scope, "tracks"),
            "count_hint": _get(scope, "count_hint")}


def check_proposal(p: Any, *, slot_tracks: Optional[Iterable[int]] = None) -> GuardResult:
    """제안(Proposal 또는 ClearPlan)을 그 제안에 적힌 부탁(requested)과 견준다."""
    requested = getattr(p, "requested", None)
    if hasattr(p, "effects"):
        effects = p.effects()
    else:
        effects = [(r.start, r.end) for r in p.rows]
    whole = False
    kind = getattr(p, "kind", "")
    if kind in ("mark_pauses", "mark_spikes", "clear_marks"):
        rng = _get(requested, "range")
        scope = getattr(p, "scope", {}) or {}
        whole = rng is None and scope.get("kind") in ("whole", None)
    return check(requested, effects=effects, fps=p.fps, tl_start=p.tl_start, tl_end=p.tl_end or p.tl_start,
                 count=p.count, tracks=getattr(p, "tracks", ()) or (), slot_tracks=slot_tracks, whole=whole,
                 leftovers=getattr(p, "leftovers", ()) or ())
