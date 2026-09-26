"""자동화 버튼에 넣을 수 있는 일 (설계 B2.1, B2.2).

종류마다 설정 항목의 모양(형식, 기본값, 범위, 걸음)을 적는다. ⚙ 설정 쪽은 이 표로 그려지므로
새 자동화는 종류 하나를 더하면 된다. 화면에 보이는 이름·설명 글은 app/companion/strings_ko.py에 있다
(PARAM_TEXT, KIND_NAMES). 표시 이름처럼 리졸브에 들어가는 글의 기본값만 여기 있다.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

from ..resolve_link.ops import MARKER_COLORS

# 한 제안(카드)에 넣는 표시의 최대 수 (설계 B4.5). mark_pauses의 max도 이 안에서만 고른다.
MAX_MARKERS_PER_PROPOSAL = 200
SCOPES = ("whole", "in_out")


@dataclass(frozen=True)
class Param:
    key: str
    type: str  # float, int, bool, color, choice, text
    default: Any
    lo: Optional[float] = None
    hi: Optional[float] = None
    step: Optional[float] = None
    choices: Tuple[str, ...] = ()
    max_len: int = 20
    requires_cap: Optional[str] = None  # 이 값(scope=in_out)은 기능 점검에서 확인된 뒤에만

    def normalize(self, value: Any) -> Any:
        """저장된 값을 이 항목에 맞게 (범위 밖은 끝으로, 모양이 틀리면 기본값)."""
        if self.type in ("float", "int"):
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                return self.default
            v = float(value)
            if self.lo is not None:
                v = max(self.lo, v)
            if self.hi is not None:
                v = min(self.hi, v)
            if self.step:
                base = self.lo if self.lo is not None else 0.0
                v = base + round((v - base) / self.step) * self.step
                v = round(v, 6)
            return int(round(v)) if self.type == "int" else v
        if self.type == "bool":
            return value if isinstance(value, bool) else self.default
        if self.type == "color":
            return value if value in MARKER_COLORS else self.default
        if self.type == "choice":
            return value if value in self.choices else self.default
        if self.type == "text":
            if not isinstance(value, str) or not value.strip():
                return self.default
            return value.strip()[: self.max_len]
        return value


@dataclass(frozen=True)
class Kind:
    key: str
    params: Tuple[Param, ...]
    ready: bool  # 이 판에서 쓸 수 있는지 (아니면 버튼에 "준비 중")
    uses_voice: bool = True
    marker_kind: bool = False  # 표시만 넣는 일 (다시 누르면 [바꾸기]/[더하기])

    def param(self, key: str) -> Optional[Param]:
        return next((p for p in self.params if p.key == key), None)

    def defaults(self) -> Dict[str, Any]:
        return {p.key: p.default for p in self.params}

    def normalize(self, params: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        params = params or {}
        return {p.key: p.normalize(params.get(p.key, p.default)) for p in self.params}


def _scope() -> Param:
    return Param("scope", "choice", "whole", choices=SCOPES, requires_cap="in_out")


KINDS: Dict[str, Kind] = {
    "mark_pauses": Kind("mark_pauses", (
        Param("min_s", "float", 1.5, 0.5, 5.0, 0.1),
        Param("pad_s", "float", 0.2, 0.0, 1.0, 0.1),
        Param("below_lu", "float", 25.0, 15.0, 40.0, 1.0),
        Param("as_range", "bool", True),
        Param("color", "color", "Blue"),
        Param("name", "text", "쉼", max_len=20),
        # 설계 B2.2는 10..500이지만 한 카드에 넣는 표시는 200개까지라서(B4.5) 200까지만 고른다
        Param("max", "int", 200, 10, MAX_MARKERS_PER_PROPOSAL, 10),
        _scope(),
    ), ready=True, marker_kind=True),
    "mark_spikes": Kind("mark_spikes", (
        Param("above_lu", "float", 8.0, 4.0, 20.0, 0.5),
        Param("merge_s", "float", 1.0, 0.0, 3.0, 0.1),
        Param("color", "color", "Red"),
        Param("max", "int", 50, 10, MAX_MARKERS_PER_PROPOSAL, 10),
        _scope(),
    ), ready=True, marker_kind=True),
    "balance_voice": Kind("balance_voice", (
        Param("target_lufs", "float", -14.0, -30.0, -5.0, 0.5),
        Param("true_peak", "float", -1.0, -9.0, 0.0, 0.5),
        Param("peaks", "choice", "medium", choices=("light", "medium", "strong")),
        Param("lift", "choice", "medium", choices=("off", "light", "medium", "strong")),
        Param("originals", "choice", "disable", choices=("disable", "keep")),
        Param("mark_tamed", "bool", True),
        Param("marker_color", "color", "Yellow"),
        _scope(),
    ), ready=False),
    # ⚙ "할 일"에서 고를 수 있지만 아직 준비 중인 일 (설계 B2.2 표)
    "name_tracks": Kind("name_tracks", (), ready=False, uses_voice=False),
    "make_subtitles": Kind("make_subtitles", (), ready=False, uses_voice=False),
    "final_check": Kind("final_check", (), ready=False, uses_voice=False),
    "duck_music": Kind("duck_music", (), ready=False, uses_voice=False),
}

# ⚙에서 보이는 순서
KIND_ORDER = ("mark_pauses", "mark_spikes", "balance_voice", "name_tracks", "make_subtitles", "final_check",
              "duck_music")
READY_KINDS = tuple(k for k in KIND_ORDER if KINDS[k].ready)


def kind(key: Optional[str]) -> Optional[Kind]:
    return KINDS.get(key or "")


def is_ready(key: Optional[str]) -> bool:
    k = kind(key)
    return bool(k and k.ready)


def normalize_params(key: str, params: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    k = kind(key)
    if k is None:
        return dict(params or {})
    return k.normalize(params)


def changed_params(key: str, old: Dict[str, Any], new: Dict[str, Any]) -> List[str]:
    k = kind(key)
    keys: Sequence[str] = [p.key for p in k.params] if k else sorted(set(old) | set(new))
    return [x for x in keys if old.get(x) != new.get(x)]
