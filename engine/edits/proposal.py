"""제안(카드 하나): 무엇을 어디에 넣을지 계산한 결과 (설계 B4.5, B2.5).

리졸브에는 아직 아무것도 하지 않은 상태다. [리졸브에 넣기]를 누르면 apply.py가 이것을 넣는다.
카드에 보이는 글은 화면(app/companion/cards.py)이 이 값들로 만든다. 여기 있는 한국어는
리졸브에 들어가는 표시 이름과 메모뿐이다.

- 제안 번호 P는 시각(밀리초)으로 만든다: 일지가 없어져도 예전 표시의 꼬리표와 겹치지 않게.
- 표시 꼬리표는 aih:<P>:<n> (n은 1부터).
- 표시 위치(MarkerSpec.frame)는 타임라인 시작부터 센 프레임 (AddMarker 기준). 카드와 일지는 절대 프레임.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from ..resolve_link.ops import MAX_MARKER_NAME, MAX_MARKER_NOTE, MarkerSpec
from .journal import marker_prefix

NOTE_SUFFIX = "· AI 도우미"
_B36 = "0123456789abcdefghijklmnopqrstuvwxyz"


def _base36(n: int) -> str:
    out = ""
    while True:
        n, r = divmod(n, 36)
        out = _B36[r] + out
        if n == 0:
            return out


_last_id = [0]


def new_proposal_id(now_ms: Optional[int] = None) -> str:
    """"P" + 밀리초의 36진수 (예: Pmfz9k2a1). 같은 밀리초에 두 번 불러도 다르게."""
    ms = int(time.time() * 1000) if now_ms is None else int(now_ms)
    if now_ms is None:
        ms = max(ms, _last_id[0] + 1)
        _last_id[0] = ms
    return "P" + _base36(ms)


def pause_name(base: str, seconds: float) -> str:
    return f"{base} {seconds:.1f}초"[:MAX_MARKER_NAME]


def spike_name(db_above: float) -> str:
    return f"튀는 소리 +{db_above:.1f}dB"[:MAX_MARKER_NAME]


def marker_note(detail: str) -> str:
    return f"{detail} {NOTE_SUFFIX}".strip()[:MAX_MARKER_NOTE]


@dataclass
class MarkerRow:
    """카드와 일지용 표시 한 줄 (절대 프레임)."""

    start: int
    end: int  # 들어가지 않는 끝
    value: float  # 쉼 길이(초) 또는 말소리보다 큰 정도(dB)
    name: str
    custom: str


@dataclass
class VoiceInfo:
    """이번 계산에 쓴 목소리 (카드의 "목소리 = 소리 2 (지난번 선택) [바꾸기]")."""

    stream: int  # 0부터
    stream_count: int
    signature: str
    remembered: bool  # 지난번에 고른 것을 그대로 썼는지 (False = 방금 고름)
    mix: Optional[int] = None  # 전체 소리로 보이는 스트림 (추정)
    doubled: bool = False  # 섞은 스트림과 목소리가 함께 켜져 있음
    path: str = ""
    profile: Dict[str, Any] = field(default_factory=dict)


@dataclass
class Proposal:
    id: str
    kind: str
    slot: Optional[int]
    origin: str  # "button:1"
    request: str  # 버튼 이름 (일지에 적는 부탁)
    params: Dict[str, Any]
    rows: List[MarkerRow]
    specs: List[MarkerSpec]
    timeline: Dict[str, Any]  # TimelineSnapshot.timeline_record()
    fps: float
    tl_start: int
    tl_end: Optional[int]
    fingerprint: str
    scope: Dict[str, Any] = field(default_factory=lambda: {"kind": "whole", "lo": None, "hi": None})
    voice: Optional[VoiceInfo] = None
    tracks: List[int] = field(default_factory=list)
    warnings: Dict[str, int] = field(default_factory=dict)
    total_s: float = 0.0
    found: int = 0  # 줄이기 전에 찾은 수 (max를 넘었으면 count보다 크다)
    point_only: bool = False  # 길이 있는 표시를 쓰지 않음 (설정이나 기능 점검 결과)
    debug: Dict[str, Any] = field(default_factory=dict)
    # 대화에서 온 제안 (설계 B4.3, B4.4): 값마다 출처, 사용자가 말한 범위, 카드에 적을 줄, 못 알아들은 부분
    provenance: Dict[str, str] = field(default_factory=dict)
    requested: Optional[Dict[str, Any]] = None  # brain.RequestedScope를 사전으로
    notes: List[Tuple[str, Dict[str, Any]]] = field(default_factory=list)
    leftovers: List[str] = field(default_factory=list)
    offer: Optional[str] = None  # 못 하는 부탁 대신 권하는 표시 ("cut" 보라 / "audio" 노랑)

    @property
    def count(self) -> int:
        return len(self.specs)

    @property
    def from_chat(self) -> bool:
        return self.origin.startswith("chat:")

    @property
    def colors(self) -> List[str]:
        """표시 색 (나온 순서, 겹치지 않게). 대화의 표시 카드는 줄마다 색이 다를 수 있다."""
        out: List[str] = []
        for s in self.specs:
            if s.color not in out:
                out.append(s.color)
        return out

    @property
    def prefix(self) -> str:
        return marker_prefix(self.id)

    @property
    def color(self) -> Optional[str]:
        return self.specs[0].color if self.specs else self.params.get("color")

    def commands(self) -> List[Dict[str, Any]]:
        return [{"op": self.kind, **self.params}]

    def expected_markers(self) -> List[Dict[str, Any]]:
        return [{"frame": self.tl_start + s.frame, "custom": s.custom, "dur": s.dur} for s in self.specs]

    def digest(self) -> str:
        raw = json.dumps({"kind": self.kind, "params": self.params, "fingerprint": self.fingerprint,
                          "specs": [s.to_dict() for s in self.specs]}, sort_keys=True, ensure_ascii=False)
        return hashlib.sha1(raw.encode("utf-8")).hexdigest()

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["specs"] = [s.to_dict() for s in self.specs]
        return d


def build_specs(pid: str, rows: List[MarkerRow], tl_start: int, color: str, *, point: bool,
                note_for) -> List[MarkerSpec]:
    """표시 줄 → 넣을 표시 (타임라인 시작 기준 프레임, 꼬리표 aih:<P>:<n>)."""
    specs: List[MarkerSpec] = []
    for n, row in enumerate(rows, start=1):
        row.custom = f"{marker_prefix(pid)}{n}"
        dur = 1 if point else max(1, row.end - row.start)
        specs.append(MarkerSpec(frame=row.start - tl_start, dur=dur, color=color, name=row.name,
                                note=marker_note(note_for(row)), custom=row.custom))
    return specs
