"""대화의 표시 넣기(mark)와 도우미 표시 지우기(clear_marks) (설계 B3의 mark, clear_marks, B6).

표시 넣기 (mark_proposal)
- 두뇌가 푼 항목(절대 프레임 at/end, 색, 이름, 메모)을 제안(Proposal, kind "mark")으로 만든다.
  넣기·영수증·되돌리기는 자동화 버튼과 같은 apply_markers / undo_proposal을 쓴다.
- 클립에서 찾은 표시가 아니므로 지문(fingerprint)을 비워 둔다: 넣기 직전에 클립 목록을 다시 읽지 않는다.

도우미 표시 지우기 (plan_clear → apply_clear → undo_clear)
- 계산: 지금 타임라인의 "aih:" 표시를 읽어 색·종류(일지의 제안 번호로 앎)·범위로 고른다. 리졸브는 바꾸지 않는다.
  범위를 말했으면 범위 안에 다 들어가는 표시만 고르고, 범위에 걸친 표시는 세기만 한다(그대로 둔다).
  한 점("3분 20초에 있는 표시")을 말했으면 그 점 앞뒤 0.5초 안에서 시작하는 표시를 고른다.
- 넣기: 일지에 먼저 적고(지울 꼬리표와 그 표시의 모양), 카드에 보인 꼬리표만 delete_markers{prefix: "aih:",
  customs: [...]}로 지운다 (200개씩). 다시 읽어 없어진 것으로 영수증을 만든다.
  지운 표시가 어떤 제안의 전부였으면 그 제안은 일지에서 "뺌"(undo.by = "clear:<P>")이 된다.
- 되돌리기: 적어 둔 모양대로 같은 꼬리표로 다시 넣는다. 그사이 사용자가 따로 뺀 제안의 표시는 다시 넣지 않는다.
  다시 넣은 뒤 "뺌"이 됐던 제안은 다시 "넣음"이 된다.
- 사용자가 찍은 표시(꼬리표 없음)나 다른 프로그램의 표시는 고르지도, 지우지도 않는다 (Lua도 거절한다).
"""

from __future__ import annotations

import hashlib
import json
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from ..resolve_link.bridge import BridgeError, BridgeTimeout
from ..resolve_link.ops import (MARKER_COLORS, MAX_ADD_MARKERS, MAX_DELETE_CUSTOMS, MAX_MARKER_NAME, MAX_MARKER_NOTE,
                                MarkerSpec, ResolveOps, TimelineInfo)
from ..timeline.map import exact_fps
from ..timeline.snapshot import TimelineSnapshot
from .journal import Journal, entry_op, in_flight, key_for, marker_prefix, proposal_of
from .proposal import MarkerRow, Proposal, marker_note, new_proposal_id

OUR_PREFIX = "aih:"
MAX_MARK_ITEMS = 200  # 한 카드에 넣는 표시 (설계 B4.5)


def timeline_record(info: TimelineInfo) -> Dict[str, Any]:
    """일지·카드에 적는 타임라인 정보 (TimelineSnapshot.timeline_record와 같은 모양)."""
    return TimelineSnapshot(info).timeline_record()


def _fps_start_end(info: TimelineInfo) -> Tuple[float, int, int]:
    fps = exact_fps(info.fps)
    if not fps or info.start_frame is None or info.end_frame is None:
        raise ValueError("타임라인의 시작·끝·속도를 모릅니다")
    return float(fps), int(info.start_frame), int(info.end_frame)


# ── 표시 넣기 ──────────────────────────────────────────────────────────


def mark_proposal(items: Sequence[Dict[str, Any]], info: TimelineInfo, *, origin: str = "chat:rule",
                  request: str = "", point_only: bool = False, pid: Optional[str] = None,
                  offer: Optional[str] = None, provenance: Optional[Dict[str, str]] = None,
                  requested: Optional[Dict[str, Any]] = None, notes: Optional[List[Tuple[str, Dict[str, Any]]]] = None,
                  leftovers: Optional[List[str]] = None, extra: Optional[Dict[str, Any]] = None) -> Proposal:
    """항목 {at, end, color, name, note, src} (절대 프레임, end는 들어가지 않음) → 표시 제안.

    point_only: 길이 있는 표시를 쓰지 않음 (기능 점검에서 안 된다고 나왔을 때). 점 표시는 늘 길이 1.
    extra: params에 함께 적을 값 (권하는 노란 표시의 {"offer": {"db", "direction"}}).
    """
    fps, tl_start, tl_end = _fps_start_end(info)
    pid = pid or new_proposal_id()
    rows: List[MarkerRow] = []
    specs: List[MarkerSpec] = []
    kept: List[Dict[str, Any]] = []
    for n, it in enumerate(list(items)[:MAX_MARK_ITEMS], start=1):
        at = max(tl_start, min(int(it["at"]), tl_end - 1))
        end = max(at + 1, min(int(it.get("end") or at + 1), tl_end))
        color = it.get("color") if it.get("color") in MARKER_COLORS else "Green"
        name = str(it.get("name") or "")[:MAX_MARKER_NAME]
        note_text = str(it.get("note") or "")
        custom = f"{marker_prefix(pid)}{n}"
        dur = 1 if point_only or end - at <= 1 else end - at
        rows.append(MarkerRow(at, end, 0.0, name, custom))
        specs.append(MarkerSpec(frame=at - tl_start, dur=dur, color=color, name=name,
                                note=marker_note(note_text[: MAX_MARKER_NOTE - 20]), custom=custom))
        kept.append({"at": at, "end": end, "color": color, "name": name, "note": note_text,
                     "src": dict(it.get("src") or {})})
    return Proposal(
        id=pid, kind="mark", slot=None, origin=origin, request=request, params={"items": kept, **(extra or {})},
        rows=rows,
        specs=specs, timeline=timeline_record(info), fps=fps, tl_start=tl_start, tl_end=tl_end, fingerprint="",
        scope=_items_scope(kept), point_only=point_only, provenance=dict(provenance or {}), requested=requested,
        notes=list(notes or []), leftovers=list(leftovers or []), offer=offer,
    )


def _items_scope(items: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    if not items:
        return {"kind": "whole", "lo": None, "hi": None}
    return {"kind": "items", "lo": min(i["at"] for i in items), "hi": max(i["end"] for i in items)}


def remade_mark(p: Proposal, items: Sequence[Dict[str, Any]], **extra: Any) -> Proposal:
    """같은 제안 번호로 항목만 바꾼 제안 (카드에서 고침). 부탁 범위는 고친 항목을 따른다."""
    info = TimelineInfo.from_result(dict(p.timeline))
    requested = dict(p.requested or {})
    if items:
        requested.update({"range": [min(i["at"] for i in items), max(i["end"] for i in items)],
                          "range_src": "edited"})
    params = {k: v for k, v in p.params.items() if k != "items"}
    params.update(extra)
    return mark_proposal(items, info, origin=p.origin, request=p.request, point_only=p.point_only, pid=p.id,
                         offer=p.offer, provenance=p.provenance, requested=requested, notes=p.notes,
                         leftovers=p.leftovers, extra=params)


def moved_mark(p: Proposal, index: int, frames: int) -> Proposal:
    """카드의 [−][+]: 항목 하나의 시각을 frames만큼 옮긴 새 제안 (같은 제안 번호, 리졸브에 묻지 않음).

    옮긴 시각은 사용자가 카드에서 정한 값이므로 출처가 "말씀하신 값"이 된다.
    """
    items = [dict(i, src=dict(i.get("src") or {})) for i in p.params.get("items") or []]
    if not (0 <= index < len(items)):
        return p
    it = items[index]
    length = it["end"] - it["at"]
    at = max(p.tl_start, min(it["at"] + int(frames), (p.tl_end or it["at"] + 1) - max(1, length)))
    it["at"], it["end"] = at, at + max(1, length)
    it["src"]["at"] = "said"
    return remade_mark(p, items)


# ── 도우미 표시 지우기 ─────────────────────────────────────────────────


@dataclass
class ClearPlan:
    """지울 도우미 표시 (카드 하나). targets의 frame은 절대 프레임."""

    id: str
    targets: List[Dict[str, Any]]
    timeline: Dict[str, Any]
    fps: float
    tl_start: int
    tl_end: int
    request: str = ""
    origin: str = "chat:rule"
    params: Dict[str, Any] = field(default_factory=dict)  # colors, kinds, lo, hi, point
    scope: Dict[str, Any] = field(default_factory=lambda: {"kind": "whole", "lo": None, "hi": None})
    straddling: int = 0  # 범위에 걸쳐 있어서 그대로 두는 표시
    total_ours: int = 0  # 이 타임라인의 도우미 표시 전체
    provenance: Dict[str, str] = field(default_factory=dict)
    requested: Optional[Dict[str, Any]] = None
    notes: List[Tuple[str, Dict[str, Any]]] = field(default_factory=list)
    leftovers: List[str] = field(default_factory=list)
    kind: str = "clear_marks"
    slot: Optional[int] = None

    @property
    def count(self) -> int:
        return len(self.targets)

    @property
    def customs(self) -> List[str]:
        return [t["custom"] for t in self.targets]

    @property
    def colors(self) -> List[str]:
        out: List[str] = []
        for t in self.targets:
            if t.get("color") not in out:
                out.append(t.get("color"))
        return out

    @property
    def from_chat(self) -> bool:
        return self.origin.startswith("chat:")

    def which(self) -> str:
        """설계 B3 clear_marks의 which (일지에 적는 요약)."""
        if self.params.get("kinds"):
            return "kind:" + ",".join(self.params["kinds"])
        if self.params.get("colors"):
            return "color:" + ",".join(self.params["colors"])
        return "all_ours"

    def commands(self) -> List[Dict[str, Any]]:
        return [{"op": "clear_marks", "which": self.which(), **{k: v for k, v in self.params.items()}}]

    def effects(self) -> List[Tuple[int, int]]:
        """범위 지킴이가 볼 자리: 한 점을 말했으면 표시의 시작, 아니면 표시 전체."""
        if self.params.get("point"):
            return [(t["frame"], t["frame"] + 1) for t in self.targets]
        return [(t["frame"], t["frame"] + max(1, int(t.get("duration") or 1))) for t in self.targets]

    def snapshot_rows(self) -> List[Dict[str, Any]]:
        """되돌리기용 모양 (frame은 타임라인 시작부터 센 프레임: add_markers에 그대로 쓴다)."""
        return [{"frame": t["frame"] - self.tl_start, "color": t.get("color"), "name": t.get("name"),
                 "note": t.get("note"), "duration": t.get("duration"), "custom": t["custom"]} for t in self.targets]

    def digest(self) -> str:
        raw = json.dumps({"params": self.params, "customs": self.customs}, sort_keys=True, ensure_ascii=False)
        return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def plan_clear(ops: ResolveOps, *, colors: Optional[Iterable[str]] = None, kinds: Optional[Iterable[str]] = None,
               lo: Optional[int] = None, hi: Optional[int] = None, point: bool = False,
               root: Optional[Path] = None, origin: str = "chat:rule", request: str = "",
               pid: Optional[str] = None, cancel: Optional[threading.Event] = None) -> ClearPlan:
    """지울 표시를 고른다 (읽기만). kinds: 그 일(mark_pauses …)로 넣은 표시만 (일지의 제안 번호로 안다)."""
    info = ops.timeline_info(cancel=cancel)
    if not info.has_timeline:
        raise ValueError("no_timeline")
    fps, tl_start, tl_end = _fps_start_end(info)
    colors = sorted(set(colors)) if colors else []
    kinds = sorted(set(kinds)) if kinds else []
    op_of: Dict[str, Optional[str]] = {}
    if kinds:
        for e in Journal.for_timeline(info, root).entries:
            op_of[str(e.get("proposal_id"))] = entry_op(e)
    markers = ops.get_markers(OUR_PREFIX, cancel=cancel)
    targets: List[Dict[str, Any]] = []
    straddling = 0
    for m in markers:
        custom = m.get("custom")
        if not isinstance(custom, str) or not custom.startswith(OUR_PREFIX):
            continue
        frame = tl_start + int(m.get("frame") or 0)
        dur = max(1, int(m.get("duration") or 1))
        if colors and m.get("color") not in colors:
            continue
        pid_of = proposal_of(custom)
        kind = op_of.get(pid_of or "") if kinds else None
        if kinds and kind not in kinds:
            continue
        if lo is not None and hi is not None:
            if point:
                if not (lo <= frame < hi):
                    continue
            elif not (lo <= frame and frame + dur <= hi):
                if frame < hi and frame + dur > lo:
                    straddling += 1
                continue
        targets.append({"frame": frame, "duration": dur, "color": m.get("color"), "name": m.get("name"),
                        "note": m.get("note"), "custom": custom, "pid": pid_of, "kind": kind})
    params: Dict[str, Any] = {}
    if colors:
        params["colors"] = colors
    if kinds:
        params["kinds"] = kinds
    scope = {"kind": "whole", "lo": None, "hi": None}
    if lo is not None and hi is not None:
        params.update({"lo": int(lo), "hi": int(hi)})
        if point:
            params["point"] = True
        scope = {"kind": "point" if point else "range", "lo": int(lo), "hi": int(hi)}
    return ClearPlan(id=pid or new_proposal_id(), targets=targets, timeline=timeline_record(info), fps=fps,
                     tl_start=tl_start, tl_end=tl_end, request=request, origin=origin, params=params, scope=scope,
                     straddling=straddling, total_ours=len(markers))


@dataclass
class ClearOutcome:
    status: str  # applied / partial / undone / other_timeline / failed / unknown (답이 끊겨 모름)
    proposal_id: str
    expected: int = 0
    deleted: int = 0
    left: int = 0  # 지우려 했는데 남은 것
    at: str = ""
    closed: List[str] = field(default_factory=list)  # 표시가 모두 지워져 "뺌"이 된 제안
    timeline_name: Optional[str] = None
    error: Optional[str] = None
    calls: Dict[str, Any] = field(default_factory=dict)
    receipt: Dict[str, Any] = field(default_factory=dict)


def _same_timeline(info: TimelineInfo, timeline: Dict[str, Any]) -> bool:
    uid = timeline.get("timeline_uid")
    if uid and info.timeline_uid:
        return uid == info.timeline_uid
    return key_for(info) == timeline.get("key")


def apply_clear(ops: ResolveOps, plan: ClearPlan, *, root: Optional[Path] = None,
                cancel: Optional[threading.Event] = None) -> ClearOutcome:
    """[리졸브에 넣기]를 누른 뒤: 카드에 보인 꼬리표의 표시만 지운다. 영수증은 다시 읽은 결과로."""
    out = ClearOutcome(status="failed", proposal_id=plan.id, expected=plan.count, at=time.strftime("%H:%M"))
    out.timeline_name = plan.timeline.get("timeline")
    info = ops.timeline_info(cancel=cancel)
    if not info.has_timeline or not _same_timeline(info, plan.timeline):
        out.status = "other_timeline"
        return out
    if not plan.targets:
        out.status = "applied"
        return out
    journal = Journal.for_timeline(info, root)
    with in_flight(plan.id):
        return _apply_clear(ops, plan, journal, out, cancel)


def _apply_clear(ops: ResolveOps, plan: ClearPlan, journal: Journal, out: ClearOutcome,
                 cancel: Optional[threading.Event]) -> ClearOutcome:
    journal.begin_clear(plan.id, origin=plan.origin, request=plan.request, commands=plan.commands(),
                        expected_deleted=plan.customs, snapshot=plan.snapshot_rows(), plan_digest=plan.digest())
    snapshot: List[Dict[str, Any]] = []
    customs = plan.customs
    for i in range(0, len(customs), MAX_DELETE_CUSTOMS):
        chunk = customs[i:i + MAX_DELETE_CUSTOMS]
        try:
            r = ops.delete_markers(prefix=OUR_PREFIX, customs=chunk, snapshot=True, cancel=cancel)
        except BridgeTimeout as exc:
            out.error = f"timeout:{exc}"
            break
        except BridgeError as exc:
            out.error = f"{exc.error}:{exc.func}"
            break
        rows = r.get("snapshot") if isinstance(r.get("snapshot"), list) else []
        snapshot += [x for x in rows if isinstance(x, dict) and x.get("custom") in set(chunk)]
        if isinstance(r.get("calls"), dict):
            out.calls.update(r["calls"])
    try:
        present = {m.get("custom") for m in ops.get_markers(OUR_PREFIX, cancel=cancel)}
        readback = True
    except (BridgeError, BridgeTimeout):
        if out.error is not None:
            # 답이 끊겼고 다시 읽지도 못함: 몇 개가 지워졌는지 모른다. "지우는 중"으로 두고(지우며 받은 모양은 적어 둔다)
            # 다음 연결 확인이 꼬리표로 맞춰 본다. 그래야 지운 표시를 되돌리기로 다시 넣을 수 있다.
            out.receipt = {"deleted": None, "expected": out.expected, "readback": False, "unknown": True,
                           "at": out.at, "error": out.error, "calls": out.calls}
            journal.hold(plan.id, receipt=out.receipt, snapshot=snapshot)
            out.status = "unknown"
            return out
        present = set()
        readback = False
    gone = [c for c in customs if c not in present]
    out.deleted = len(gone)
    out.left = len(customs) - len(gone)
    out.receipt = {"deleted": out.deleted, "expected": out.expected, "left": out.left, "readback": readback,
                   "at": out.at, "error": out.error, "calls": out.calls}
    entry = journal.finish_clear(plan.id, gone=gone, snapshot=snapshot, receipt=out.receipt)
    out.status = entry["status"]
    out.closed = _close_cleared(journal, plan.id, set(gone))
    return out


def _created(e: Dict[str, Any]) -> List[str]:
    rows = (e.get("created") or {}).get("markers") or []
    return [str(r.get("custom")) for r in rows if isinstance(r, dict) and r.get("custom")]


def _close_cleared(journal: Journal, pid: str, gone: set) -> List[str]:
    """지우기로 표시가 모두 없어진 제안은 "뺌"으로 적는다 (지우기를 되돌리면 다시 "넣음")."""
    closed: List[str] = []
    for e in journal.entries:
        if e.get("proposal_id") == pid or entry_op(e) == "clear_marks":
            continue
        if e.get("status") not in ("applied", "partial"):
            continue
        created = _created(e)
        if created and all(c in gone for c in created):
            e["undo"] = {"at": time.strftime("%Y-%m-%dT%H:%M:%S"), "by": f"clear:{pid}",
                         "prev_status": e.get("status")}
            e["status"] = "undone"
            closed.append(str(e.get("proposal_id")))
    if closed:
        journal.save()
    return closed


def _spec_from_row(r: Dict[str, Any]) -> Optional[MarkerSpec]:
    custom = r.get("custom")
    color = r.get("color")
    if not isinstance(custom, str) or not custom.startswith(OUR_PREFIX) or color not in MARKER_COLORS:
        return None
    try:
        frame = int(r.get("frame"))
    except (TypeError, ValueError):
        return None
    if frame < 0:
        return None
    dur = r.get("duration")
    dur = int(dur) if isinstance(dur, (int, float)) and not isinstance(dur, bool) and dur >= 1 else 1
    return MarkerSpec(frame=frame, dur=dur, color=color, name=str(r.get("name") or "")[:MAX_MARKER_NAME],
                      note=str(r.get("note") or "")[:MAX_MARKER_NOTE], custom=custom)


def undo_clear(ops: ResolveOps, journal: Journal, e: Dict[str, Any], *, cancel: Optional[threading.Event] = None):
    """지우기를 되돌린다: 지운 표시를 같은 꼬리표·위치·색·이름으로 다시 넣는다."""
    from .apply import UndoOutcome  # apply가 이 파일을 부르므로 여기서

    pid = str(e.get("proposal_id"))
    out = UndoOutcome(status="undone", proposal_id=pid, restored=True)
    gone = set(e.get("deleted") or [])
    rows = [r for r in e.get("deleted_snapshot") or [] if r.get("custom") in gone]
    # 그사이 사용자가 따로 뺀 제안(되돌리기, 모두 빼기)의 표시는 다시 넣지 않는다
    by_pid = {str(x.get("proposal_id")): x for x in journal.entries}

    def wanted(r: Dict[str, Any]) -> bool:
        owner = by_pid.get(proposal_of(r.get("custom")) or "")
        if owner is None or owner.get("status") != "undone":
            return True
        return (owner.get("undo") or {}).get("by") == f"clear:{pid}"

    rows = [r for r in rows if wanted(r)]
    out.expected = len(rows)
    present = {m.get("custom") for m in ops.get_markers(OUR_PREFIX, cancel=cancel)}
    specs = [s for s in (_spec_from_row(r) for r in rows if r.get("custom") not in present) if s is not None]
    out.already_gone = len(rows) - len(specs)  # 이미 있어서 다시 넣지 않은 것
    for i in range(0, len(specs), MAX_ADD_MARKERS):
        r = ops.add_markers(specs[i:i + MAX_ADD_MARKERS], cancel=cancel)
        out.calls.update(r.calls)
    after = {m.get("custom") for m in ops.get_markers(OUR_PREFIX, cancel=cancel)}
    back = [s.custom for s in specs if s.custom in after]
    out.deleted = len(back)  # restored=True일 때는 다시 넣은 수
    out.remaining = len(specs) - len(back)
    out.status = "undone" if out.remaining == 0 else "undo_failed"
    e["undo"] = {"at": time.strftime("%Y-%m-%dT%H:%M:%S"), "restored": len(back), "remaining": out.remaining}
    for x in journal.entries:
        u = x.get("undo") or {}
        if u.get("by") == f"clear:{pid}" and x.get("status") == "undone":
            created = _created(x)
            if created and all(c in after for c in created):
                x["status"] = u.get("prev_status") or "applied"
                x.pop("undo", None)
                out.reopened.append(str(x.get("proposal_id")))
    journal.mark(pid, out.status)
    return out
