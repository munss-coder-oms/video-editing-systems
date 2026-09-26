"""두뇌가 낸 할 일(Command)을 계획에 넘길 모양으로 (Qt 없음, 리졸브에 묻지 않음).

- 쉬는 곳·튀는 소리 (find_request): 값이 비면 그 일을 하는 첫 자동화 버튼의 설정(설정값), 버튼이 없으면
  기본값을 쓴다. 말한 범위는 SlotRequest.range로 넘겨 계산할 때 그 안쪽으로 자른다 (설계 B2.4 5).
- 표시 (mark_items): 한 부탁 안의 표시 명령을 카드 하나로 모은다 (못 하는 부탁 대신 권하는 표시는 따로).
- 지우기 (clear_args): 색·종류·범위.
값마다 출처(말씀하신 값 / 기본값 / 설정값 / 도우미가 찾음)를 함께 돌려준다 (설계 B4.4).
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence, Tuple

from ..automation import kinds as K
from ..automation.plan import SlotRequest
from .brain import DEFAULT, FOUND, SAID, SETTING, Command
from .context import AssistContext

MAIN_PARAM = {"mark_pauses": "min_s", "mark_spikes": "above_lu"}
CLAMPED = "clamped"  # 말한 값을 한도로 맞춤 (카드는 출처를 붙이지 않고 CARD_NOTES의 줄로 알린다)
CLAMP_NOTE = {"min_s": "min_s_clamped", "above_lu": "above_clamped"}


def find_request(cmd: Command, ctx: AssistContext, *, text: str,
                 origin: str = "chat:rule") -> Tuple[SlotRequest, Dict[str, str], Optional[int]]:
    """(계획 요청, 값의 출처, 설정값을 가져온 버튼 번호)."""
    kind = K.kind(cmd.op)
    if kind is None:
        raise ValueError(cmd.op)
    slot = ctx.slot_for(cmd.op)
    if slot is not None:
        params, src, slot_no = kind.normalize(slot.get("params")), SETTING, slot.get("slot")
    else:
        params, src, slot_no = kind.defaults(), DEFAULT, None
    prov: Dict[str, str] = {}
    main = MAIN_PARAM[cmd.op]
    for key in (main, "color"):
        prov[key] = src
    for key in (main, "color"):
        if key in cmd.params:
            params[key] = cmd.params[key]
            prov[key] = cmd.provenance.get(key, SAID)
    params = kind.normalize(params)
    # 말한 값이 쓸 수 있는 범위 밖이면 끝으로 맞춘다. 그 값은 "말씀하신 값"이 아니므로 출처를 떼고 카드에 한 줄 적는다
    # (예: "10초 넘게 쉰 곳" → 5초. 설계 B4.4·B4.5)
    said = cmd.params.get(main)
    p = kind.param(main)
    if isinstance(said, (int, float)) and not isinstance(said, bool) and p is not None \
            and ((p.lo is not None and said < p.lo) or (p.hi is not None and said > p.hi)):
        prov[main] = CLAMPED
        note = (CLAMP_NOTE[main], {"said": float(said), "used": params[main], "lo": p.lo, "hi": p.hi})
        if note not in cmd.notes:
            cmd.notes.append(note)
    if cmd.params.get("scope") == "in_out":
        params["scope"] = "in_out"
        prov["range"] = SAID
    elif cmd.scope.range is not None:
        prov["range"] = SAID
    else:
        prov["range"] = src if params.get("scope") == "in_out" else DEFAULT
    count = cmd.params.get("count")
    if count is not None:
        prov["count"] = SAID
    prov["places"] = FOUND
    req = SlotRequest(slot=None, kind=cmd.op, name=text, params=params, origin=origin,
                      range=tuple(cmd.scope.range) if cmd.scope.range else None,
                      range_src=cmd.scope.range_src if cmd.scope.range else "", count=count)
    return req, prov, slot_no


def mark_items(commands: Sequence[Command]) -> Tuple[List[Dict[str, Any]], Dict[str, Any], List[Tuple[str, Dict]]]:
    """표시 명령 여럿 → (항목 목록, 말한 범위, 카드에 적을 줄). 항목은 두뇌가 푼 모양 그대로."""
    items: List[Dict[str, Any]] = []
    notes: List[Tuple[str, Dict[str, Any]]] = []
    for c in commands:
        items += [dict(i) for i in c.params.get("items") or []]
        for n in c.notes:
            if n not in notes:
                notes.append(n)
    items.sort(key=lambda i: i["at"])
    tracks = sorted({t for c in commands for t in (c.scope.tracks or [])}) or None
    if not items:
        return items, {"range": None, "range_src": "none", "said_whole": False, "tracks": tracks,
                       "count_hint": None}, notes
    point = all(i["end"] - i["at"] <= 1 for i in items)
    requested = {"range": [min(i["at"] for i in items), max(i["end"] for i in items)],
                 "range_src": "point" if point else "said", "said_whole": False, "tracks": tracks,
                 "count_hint": len(items)}
    return items, requested, notes


def clear_args(cmd: Command) -> Dict[str, Any]:
    """plan_clear에 넘길 값: colors, kinds, lo, hi, point."""
    out: Dict[str, Any] = {"colors": list(cmd.params.get("colors") or []) or None,
                           "kinds": list(cmd.params.get("kinds") or []) or None}
    s = cmd.scope
    if s.range is not None and s.range_src != "whole":
        out["lo"], out["hi"] = int(s.range[0]), int(s.range[1])
        out["point"] = s.range_src == "point"
    return out


def mark_provenance(items: Sequence[Dict[str, Any]]) -> Dict[str, str]:
    """표시 카드 전체의 출처 요약 (항목마다의 출처는 항목의 src)."""
    out: Dict[str, str] = {}
    for key in ("at", "color", "name"):
        srcs = {str((i.get("src") or {}).get(key) or DEFAULT) for i in items}
        out[key] = SAID if srcs == {SAID} else (DEFAULT if srcs == {DEFAULT} else "mixed")
    return out
