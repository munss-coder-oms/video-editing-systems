"""ResolveOps: 리졸브에 시키는 일을 이름 있는 함수와 정해진 모양의 결과로 감싼다.

화면(app)과 편집 계획(engine.edits)은 Lua 답(JSON 표)의 키 이름을 직접 보지 않고 이것만 쓴다.
보내는 길은 Transport(지금은 LuaTransport) 하나만 안다.

지키는 것
- 표시 지우기는 우리 꼬리표("aih:..." 또는 1차 시험판의 "aih_test")만 보낸다 (Lua도 한 번 더 막는다).
- 읽기는 여러 쪽으로 나눠 받아도 한 번에 모아 돌려준다 (timeline_items).
"""

from __future__ import annotations

import re
import threading
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from .protocol import LEGACY_OPS
from .timecode import parse_fps, tc_offset
from .transport import Transport

PROBE_PREFIX = "AI 도우미 점검용"
PROBE_STAGES = ("C1", "C2", "C3", "C4", "C5", "C6", "C7", "C8")
MAX_ITEM_PAGES = 200  # timeline_items를 이보다 많이 나눠 받지 않는다 (2만 개)
MAX_ADD_MARKERS = 100  # add_markers 한 번에 보내는 표시 수 (Lua AIH.MAX_ADD)
MAX_DELETE_CUSTOMS = 200  # delete_markers{customs} 한 번에 (Lua AIH.MAX_SNAPSHOT과 같다)
TC_RE = re.compile(r"^\d\d:\d\d:\d\d[:;]\d\d$")
MAX_MARKER_NAME = 40
MAX_MARKER_NOTE = 200
# 리졸브 표시 색 이름 16개 (Lua AIH.MARKER_COLORS와 같다)
MARKER_COLORS = ("Blue", "Cyan", "Green", "Yellow", "Red", "Pink", "Purple", "Fuchsia", "Rose", "Lavender",
                 "Sky", "Mint", "Lemon", "Sand", "Cocoa", "Cream")
# Lua의 "^aih:[%w:_%-]+$"와 같은 규칙 (영문·숫자만, 한글 같은 다른 글자는 안 됨)
TAG_RE = re.compile(r"^aih:[A-Za-z0-9:_\-]+$")
PREFIX_RE = re.compile(r"^aih:[A-Za-z0-9:_\-]*$")


def is_our_custom(custom: Any) -> bool:
    """우리가 붙인 표시 꼬리표인지 (Lua의 our_custom과 같은 규칙)."""
    return isinstance(custom, str) and (custom.startswith("aih:") or custom == "aih_test")


def is_tag(custom: Any) -> bool:
    """표시를 넣을 때 쓸 수 있는 꼬리표인지 (aih:P1:3 모양, 64자까지)."""
    return isinstance(custom, str) and len(custom) <= 64 and TAG_RE.match(custom) is not None


def is_tag_prefix(prefix: Any) -> bool:
    """지울 때 쓰는 꼬리표 앞부분인지 ("aih:"로 시작)."""
    return isinstance(prefix, str) and len(prefix) <= 64 and PREFIX_RE.match(prefix) is not None


def is_probe_name(name: Any) -> bool:
    return isinstance(name, str) and name.startswith(PROBE_PREFIX)


def _int(v: Any) -> Optional[int]:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    return int(v)


def _str(v: Any) -> Optional[str]:
    return v if isinstance(v, str) else None


def _bool(v: Any) -> Optional[bool]:
    return v if isinstance(v, bool) else None


@dataclass
class PingInfo:
    script_version: Optional[str]
    ops: frozenset
    page: Optional[str]
    product: Optional[str]
    product_version: Optional[str]
    owner: Optional[str]
    raw: Dict[str, Any] = field(repr=False, default_factory=dict)

    @classmethod
    def from_result(cls, r: Dict[str, Any]) -> "PingInfo":
        ops = r.get("ops")
        known = frozenset(ops) if isinstance(ops, list) else frozenset(LEGACY_OPS)
        return cls(
            script_version=_str(r.get("script_version")),
            ops=known,
            page=_str(r.get("page")),
            product=_str(r.get("product")),
            product_version=_str(r.get("product_version")) or _str(r.get("resolve_version")),
            owner=_str(r.get("owner")),
            raw=dict(r),
        )


@dataclass
class TrackInfo:
    kind: str
    index: int
    name: str
    enabled: Optional[bool]
    locked: Optional[bool]
    subtype: Optional[str]
    count: Optional[int]


@dataclass
class TimelineInfo:
    project: Optional[str]
    project_uid: Optional[str]
    timeline: Optional[str]
    timeline_uid: Optional[str]
    start_frame: Optional[int]
    end_frame: Optional[int]  # 들어가지 않는 끝 (GetEndFrame)
    start_tc: Optional[str]
    current_tc: Optional[str]
    fps: Optional[str]
    drop_frame: Optional[bool]
    page: Optional[str]
    timeline_count: Optional[int]
    tracks: Dict[str, List[TrackInfo]]
    probe: Optional[Dict[str, Any]]
    raw: Dict[str, Any] = field(repr=False, default_factory=dict)

    @classmethod
    def from_result(cls, r: Dict[str, Any]) -> "TimelineInfo":
        tracks: Dict[str, List[TrackInfo]] = {}
        raw_tracks = r.get("tracks") if isinstance(r.get("tracks"), dict) else {}
        for kind in ("video", "audio", "subtitle"):
            rows = raw_tracks.get(kind)
            out: List[TrackInfo] = []
            if isinstance(rows, list):
                for t in rows:
                    if not isinstance(t, dict):
                        continue
                    out.append(TrackInfo(
                        kind=kind, index=_int(t.get("index")) or len(out) + 1, name=_str(t.get("name")) or "",
                        enabled=_bool(t.get("enabled")), locked=_bool(t.get("locked")),
                        subtype=_str(t.get("subtype")), count=_int(t.get("count")),
                    ))
            tracks[kind] = out
        probe = r.get("probe") if isinstance(r.get("probe"), dict) else None
        return cls(
            project=_str(r.get("project")), project_uid=_str(r.get("project_uid")),
            timeline=_str(r.get("timeline")), timeline_uid=_str(r.get("timeline_uid")),
            start_frame=_int(r.get("start_frame")), end_frame=_int(r.get("end_frame")),
            start_tc=_str(r.get("start_tc")), current_tc=_str(r.get("current_tc")),
            fps=_str(r.get("fps")), drop_frame=_bool(r.get("drop_frame")), page=_str(r.get("page")),
            timeline_count=_int(r.get("timeline_count")), tracks=tracks, probe=probe, raw=dict(r),
        )

    @classmethod
    def from_state(cls, r: Dict[str, Any]) -> "TimelineInfo":
        """1.0.0 스크립트의 state 답으로 만든다 (트랙은 개수만 안다)."""
        info = cls.from_result({k: v for k, v in r.items() if k != "tracks"})
        counts = r.get("tracks") if isinstance(r.get("tracks"), dict) else {}
        for kind in ("video", "audio", "subtitle"):
            n = _int(counts.get(kind)) or 0
            info.tracks[kind] = [TrackInfo(kind, i, "", None, None, None, None) for i in range(1, n + 1)]
        return info

    @property
    def has_project(self) -> bool:
        return self.project is not None

    @property
    def has_timeline(self) -> bool:
        return self.timeline is not None

    @property
    def is_probe_copy(self) -> bool:
        return is_probe_name(self.timeline)

    @property
    def length_frames(self) -> Optional[int]:
        if self.start_frame is None or self.end_frame is None:
            return None
        return max(0, self.end_frame - self.start_frame)

    @property
    def fps_value(self) -> Optional[float]:
        if not self.fps:
            return None
        try:
            return parse_fps(self.fps)[0]
        except ValueError:
            return None

    @property
    def duration_seconds(self) -> Optional[float]:
        n, fps = self.length_frames, self.fps_value
        if n is None or not fps:
            return None
        return n / fps

    def track_count(self, kind: str) -> int:
        return len(self.tracks.get(kind, []))


@dataclass
class Item:
    uid: Optional[str]
    kind: str
    track: int
    name: Optional[str]
    start: Optional[int]
    end: Optional[int]  # 들어가지 않는 끝 (GetEnd)
    duration: Optional[int]
    left_offset: Optional[int]
    source_start: Optional[int]
    source_end: Optional[int]
    enabled: Optional[bool]
    path: Optional[str]
    clip_fps: Optional[str]
    media_uid: Optional[str]
    linked_uids: List[str]

    @classmethod
    def from_row(cls, r: Dict[str, Any]) -> "Item":
        linked = r.get("linked_uids")
        return cls(
            uid=_str(r.get("uid")), kind=_str(r.get("kind")) or "", track=_int(r.get("track")) or 0,
            name=_str(r.get("name")), start=_int(r.get("start")), end=_int(r.get("end")),
            duration=_int(r.get("duration")), left_offset=_int(r.get("left_offset")),
            source_start=_int(r.get("source_start")), source_end=_int(r.get("source_end")),
            enabled=_bool(r.get("enabled")), path=_str(r.get("path")), clip_fps=_str(r.get("clip_fps")),
            media_uid=_str(r.get("media_uid")),
            linked_uids=[x for x in linked if isinstance(x, str)] if isinstance(linked, list) else [],
        )


@dataclass
class ScopeInfo:
    page: Optional[str]
    playhead_tc: Optional[str]
    playhead_frame: Optional[int]  # 절대 프레임 (start_frame + 시작 타임코드부터 센 수)
    start_tc: Optional[str]
    start_frame: Optional[int]
    fps: Optional[str]
    drop_frame: Optional[bool]
    in_out: Optional[Dict[str, Any]]
    selected_uids: Optional[List[str]]
    selected_count: Optional[int]
    timeline_uid: Optional[str]
    raw: Dict[str, Any] = field(repr=False, default_factory=dict)

    @classmethod
    def from_result(cls, r: Dict[str, Any]) -> "ScopeInfo":
        start_frame, start_tc, tc = _int(r.get("start_frame")), _str(r.get("start_tc")), _str(r.get("playhead_tc"))
        fps, drop = _str(r.get("fps")), _bool(r.get("drop_frame"))
        playhead = None
        # 2.1 설계는 Lua가 playhead_frame을 준다고 했지만, 드롭 프레임 계산은 파이썬(timecode.py)에
        # 이미 시험된 것이 있어 여기서 센다. Lua 답에 그 값이 오면 그것을 쓴다.
        if _int(r.get("playhead_frame")) is not None:
            playhead = _int(r.get("playhead_frame"))
        elif start_frame is not None and start_tc and tc and fps:
            try:
                playhead = start_frame + tc_offset(tc, start_tc, fps, drop)
            except ValueError:
                playhead = None
        sel = r.get("selected_uids")
        return cls(
            page=_str(r.get("page")), playhead_tc=tc, playhead_frame=playhead, start_tc=start_tc,
            start_frame=start_frame, fps=fps, drop_frame=drop,
            in_out=r.get("in_out") if isinstance(r.get("in_out"), dict) else None,
            selected_uids=[x for x in sel if isinstance(x, str)] if isinstance(sel, list) else None,
            selected_count=_int(r.get("selected_count")), timeline_uid=_str(r.get("timeline_uid")), raw=dict(r),
        )


@dataclass
class ProbeResult:
    stage: str
    ok: bool
    detail: Dict[str, Any]
    fingerprint: Optional[str]
    calls: Dict[str, Any]
    probe: Optional[Dict[str, Any]]

    @classmethod
    def from_result(cls, stage: str, r: Dict[str, Any]) -> "ProbeResult":
        detail = r.get("detail") if isinstance(r.get("detail"), dict) else {}
        calls = r.get("calls") if isinstance(r.get("calls"), dict) else {}
        probe = r.get("probe") if isinstance(r.get("probe"), dict) else None
        return cls(stage=_str(r.get("stage")) or stage, ok=r.get("ok") is True, detail=detail,
                   fingerprint=_str(r.get("fingerprint")), calls=calls, probe=probe)

    def to_dict(self) -> Dict[str, Any]:
        return {"stage": self.stage, "ok": self.ok, "detail": self.detail, "fingerprint": self.fingerprint,
                "calls": self.calls}


@dataclass
class MarkerSpec:
    """넣을 표시 하나. frame은 타임라인 시작부터 센 프레임 (AddMarker의 기준, 읽으면 같은 값)."""

    frame: int
    dur: int
    color: str
    name: str
    note: str
    custom: str

    def check(self) -> None:
        if isinstance(self.frame, bool) or not isinstance(self.frame, int) or self.frame < 0:
            raise ValueError(f"표시 위치가 잘못됐습니다: {self.frame!r}")
        if isinstance(self.dur, bool) or not isinstance(self.dur, int) or self.dur < 1:
            raise ValueError(f"표시 길이가 잘못됐습니다: {self.dur!r}")
        if self.color not in MARKER_COLORS:
            raise ValueError(f"리졸브 표시 색이 아닙니다: {self.color!r}")
        if len(self.name) > MAX_MARKER_NAME or len(self.note) > MAX_MARKER_NOTE:
            raise ValueError("표시 이름이나 메모가 너무 깁니다")
        if not is_tag(self.custom):
            raise ValueError(f"도우미 꼬리표가 아닙니다: {self.custom!r}")

    def to_args(self) -> Dict[str, Any]:
        return {"frame": self.frame, "dur": self.dur, "color": self.color, "name": self.name, "note": self.note,
                "custom": self.custom}

    def to_dict(self) -> Dict[str, Any]:
        return self.to_args()

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "MarkerSpec":
        return cls(frame=int(d["frame"]), dur=int(d.get("dur", 1)), color=str(d["color"]), name=str(d.get("name", "")),
                   note=str(d.get("note", "")), custom=str(d["custom"]))


@dataclass
class PlacedMarker:
    custom: str
    frame: int
    dur: int
    shifted: int
    found: Optional[bool]
    dur_readback: Optional[int]


@dataclass
class MarkerResult:
    """add_markers 한 번(또는 여러 번을 합친) 결과. 영수증은 이것과 다시 읽은 표시로 만든다."""

    placed: List[PlacedMarker] = field(default_factory=list)
    failed: List[Dict[str, Any]] = field(default_factory=list)  # {"custom", "err"}
    skipped_existing: List[str] = field(default_factory=list)  # 이미 있던 꼬리표
    point_fallback: bool = False
    requested: int = 0
    calls: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_result(cls, specs: Sequence[MarkerSpec], r: Dict[str, Any]) -> "MarkerResult":
        out = cls(point_fallback=r.get("point_fallback") is True, requested=_int(r.get("requested")) or len(specs),
                  calls=r.get("calls") if isinstance(r.get("calls"), dict) else {})

        def spec_at(i: Any) -> Optional[MarkerSpec]:
            n = _int(i)
            return specs[n - 1] if n is not None and 1 <= n <= len(specs) else None

        for p in r.get("placed") or []:
            if not isinstance(p, dict):
                continue
            spec = spec_at(p.get("i"))
            custom = _str(p.get("custom")) or (spec.custom if spec else "")
            out.placed.append(PlacedMarker(custom=custom, frame=_int(p.get("frame")) or 0, dur=_int(p.get("dur")) or 1,
                                           shifted=_int(p.get("shifted")) or 0, found=_bool(p.get("found")),
                                           dur_readback=_int(p.get("dur_readback"))))
        for f in r.get("failed") or []:
            if isinstance(f, dict):
                spec = spec_at(f.get("i"))
                out.failed.append({"custom": spec.custom if spec else None, "err": _str(f.get("err")) or "?"})
        for i in r.get("skipped_existing") or []:
            spec = spec_at(i)
            if spec is not None:
                out.skipped_existing.append(spec.custom)
        return out

    def merge(self, other: "MarkerResult") -> None:
        self.placed += other.placed
        self.failed += other.failed
        self.skipped_existing += other.skipped_existing
        self.point_fallback = self.point_fallback or other.point_fallback
        self.requested += other.requested
        for k, v in other.calls.items():
            if self.calls.get(k) in (None, "ok"):
                self.calls[k] = v


class ResolveOps:
    """리졸브에 시키는 일. 모든 함수는 BridgeTimeout / BridgeError / OldScript를 그대로 올려 보낸다."""

    def __init__(self, transport: Transport) -> None:
        self.transport = transport

    def _req(self, op: str, args: Optional[Dict[str, Any]] = None, *, timeout: Optional[float] = None,
             cancel: Optional[threading.Event] = None) -> Dict[str, Any]:
        return self.transport.request(op, args, timeout=timeout, cancel=cancel)

    def supports(self, op: str) -> Optional[bool]:
        return self.transport.supports(op)

    # --- 읽기 ---

    def ping(self, *, timeout: Optional[float] = None, cancel=None) -> PingInfo:
        return PingInfo.from_result(self._req("ping", timeout=timeout, cancel=cancel))

    def state(self, *, cancel=None) -> Dict[str, Any]:
        return self._req("state", cancel=cancel)

    def timeline_info(self, *, cancel=None) -> TimelineInfo:
        return TimelineInfo.from_result(self._req("timeline_info", cancel=cancel))

    def timeline_items(self, kind: str = "audio", track_from: Optional[int] = None,
                       track_to: Optional[int] = None, *, cancel=None) -> List[Item]:
        """클립 목록 전체 (여러 쪽을 이어 받음)."""
        out: List[Item] = []
        offset = 0
        for _ in range(MAX_ITEM_PAGES):
            args: Dict[str, Any] = {"kind": kind, "offset": offset, "limit": 100}
            if track_from is not None:
                args["track_from"] = int(track_from)
            if track_to is not None:
                args["track_to"] = int(track_to)
            r = self._req("timeline_items", args, cancel=cancel)
            rows = r.get("items") if isinstance(r.get("items"), list) else []
            out += [Item.from_row(x) for x in rows if isinstance(x, dict)]
            nxt = _int(r.get("next"))
            if nxt is None or nxt <= offset:
                break
            offset = nxt
        return out

    def scope(self, *, cancel=None) -> ScopeInfo:
        return ScopeInfo.from_result(self._req("scope", cancel=cancel))

    def probe_read(self, *, cancel=None) -> Dict[str, Any]:
        return self._req("probe_read", cancel=cancel)

    def get_markers(self, prefix: Optional[str] = None, *, limit: Optional[int] = None,
                    cancel=None) -> List[Dict[str, Any]]:
        """표시 목록 (frame은 타임라인 시작부터 센 프레임). prefix: 꼬리표 앞부분으로 거르기."""
        return self.read_markers(prefix, limit=limit, cancel=cancel)[0]

    def read_markers(self, prefix: Optional[str] = None, *, limit: Optional[int] = None,
                     cancel=None) -> Tuple[List[Dict[str, Any]], Optional[int]]:
        """(표시 목록, 맞는 전체 수). 전체 수는 목록이 limit에서 잘렸어도 모두 센 값 (못 읽으면 None)."""
        args: Dict[str, Any] = {}
        if prefix is not None:
            args["prefix"] = prefix
        if limit is not None:
            args["limit"] = int(limit)
        r = self._req("get_markers", args or None, cancel=cancel)
        rows = [m for m in r.get("markers", []) if isinstance(m, dict)] if isinstance(r.get("markers"), list) else []
        total = _int(r.get("total"))
        if total is None and "total" not in r:
            total = len(rows)  # 1.0.0 스크립트: 거르지 않고 모두 준다
            if prefix is not None:
                rows = [m for m in rows if isinstance(m.get("custom"), str) and m["custom"].startswith(prefix)]
                total = len(rows)
        return rows, total

    def marker_total(self, prefix: str, *, cancel=None) -> Optional[int]:
        """꼬리표가 prefix로 시작하는 표시 수 (목록은 받지 않는다)."""
        return self.read_markers(prefix, limit=0, cancel=cancel)[1]

    # --- 기능 점검과 보기 ---

    def probe_copy(self, stage: str, *, cancel=None, **args: Any) -> ProbeResult:
        if stage not in PROBE_STAGES:
            raise ValueError(f"알 수 없는 점검 단계: {stage!r}")
        payload = {k: v for k, v in args.items() if v is not None}
        payload["stage"] = stage
        return ProbeResult.from_result(stage, self._req("probe_copy", payload, cancel=cancel))

    def switch_timeline(self, uid: Optional[str] = None, name: Optional[str] = None, *, cancel=None) -> Dict[str, Any]:
        if not uid and not name:
            raise ValueError("돌아갈 타임라인 번호나 이름이 없습니다")
        return self._req("switch_timeline", {"uid": uid or None, "name": name or None}, cancel=cancel)

    # --- 고치기 (연결 점검 쪽 시험 도구) ---

    def delete_markers(self, custom: Optional[str] = None, *, prefix: Optional[str] = None,
                       colors: Optional[Iterable[str]] = None, customs: Optional[Sequence[str]] = None,
                       snapshot: bool = False, cancel=None) -> Dict[str, Any]:
        """우리 표시 지우기: custom(꼬리표 하나와 똑같은 것) 또는 prefix("aih:"로 시작하는 앞부분).

        customs: prefix와 함께, 이 꼬리표 목록에 있는 표시만 (대화의 "파란 표시 지워줘": 카드에 보인 것만 지운다).
        """
        if (custom is None) == (prefix is None):
            raise ValueError("custom과 prefix 가운데 하나만 주세요")
        if custom is not None:
            if not is_our_custom(custom):
                raise ValueError(f"도우미 꼬리표가 아닌 표시는 지우지 않습니다: {custom!r}")
            return self._req("delete_markers", {"custom": custom}, cancel=cancel)
        if not is_tag_prefix(prefix):
            raise ValueError(f"도우미 꼬리표가 아닌 표시는 지우지 않습니다: {prefix!r}")
        args: Dict[str, Any] = {"prefix": prefix}
        if colors is not None:
            cl = list(colors)
            if any(c not in MARKER_COLORS for c in cl):
                raise ValueError(f"리졸브 표시 색이 아닙니다: {cl!r}")
            args["colors"] = cl
        if customs is not None:
            cl = list(customs)
            if not cl or len(cl) > MAX_DELETE_CUSTOMS or any(not is_tag(c) or not c.startswith(prefix) for c in cl):
                raise ValueError(f"지울 꼬리표 목록이 잘못됐습니다: {len(cl)}개")
            args["customs"] = cl
        if snapshot:
            args["snapshot"] = True
        return self._req("delete_markers", args, cancel=cancel)

    # --- 보기 (편집이 아님: 확인 없이) ---

    def jump_to(self, frame: int, tc: str, *, cancel=None) -> Dict[str, Any]:
        """재생 위치 옮기기. frame: 절대 프레임 (확인용), tc: 그 프레임의 리졸브 타임코드.

        미디어·퓨전 화면이면 Lua가 옮기지 않고 {ok: false, reason: "page"}를 준다.
        """
        if isinstance(frame, bool) or not isinstance(frame, int) or frame < 0:
            raise ValueError(f"재생 위치가 잘못됐습니다: {frame!r}")
        if not isinstance(tc, str) or not TC_RE.match(tc):
            raise ValueError(f"타임코드가 잘못됐습니다: {tc!r}")
        return self._req("jump_to", {"frame": int(frame), "tc": tc}, cancel=cancel)

    def add_markers(self, specs: Sequence[MarkerSpec], *, point_only: bool = False, cancel=None,
                    timeout: Optional[float] = None) -> MarkerResult:
        """표시를 한 번에 100개까지 넣는다. 더 많으면 부르는 쪽이 나눠서 부른다."""
        specs = list(specs)
        if not specs or len(specs) > MAX_ADD_MARKERS:
            raise ValueError(f"표시는 한 번에 1~{MAX_ADD_MARKERS}개만 넣습니다: {len(specs)}")
        seen = set()
        for sp in specs:
            sp.check()
            if sp.custom in seen:
                raise ValueError(f"같은 꼬리표가 두 번 있습니다: {sp.custom}")
            seen.add(sp.custom)
        args: Dict[str, Any] = {"markers": [sp.to_args() for sp in specs]}
        if point_only:
            args["point_only"] = True
        r = self._req("add_markers", args, cancel=cancel, timeout=timeout)
        return MarkerResult.from_result(specs, r)

    def remove_audio(self, track_name: str, *, switch_page: bool = False, cancel=None) -> Dict[str, Any]:
        """1차 시험판이 넣은 시험 소리 트랙 빼기 (클립이 모두 우리 파일인 트랙만, Lua가 확인).

        편집(Edit) 화면이 아니면 Lua가 need_edit_page로 거절한다. switch_page=True는
        사용자가 [바꿔서 빼기]를 누른 뒤에만: 편집 화면으로 바꿔서 하고 원래 화면으로 돌린다.
        """
        if not track_name:
            raise ValueError("트랙 이름이 없습니다")
        args: Dict[str, Any] = {"track_name": track_name}
        if switch_page:
            args["switch_page"] = True
        return self._req("remove_audio", args, cancel=cancel)
