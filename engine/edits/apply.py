"""[리졸브에 넣기]·되돌리기·"도우미가 넣은 것 모두 빼기" (설계 B2.5 5~7, B6.2, B6.3).

넣기 (apply_markers)
1. 타임라인이 계산할 때와 같은지: 다른 타임라인이면 거절, 같은 타임라인인데 클립 지문이 다르면
   "changed"로 돌려준다 (화면: [다시 계산] [그래도 넣기]; 표시만 넣는 일이라 그래도 넣기를 허락한다).
2. 바꾸기(replace): 같은 종류의 이전 제안 표시를 먼저 뺀다 (꼬리표 aih:<P>:로).
3. 일지에 "넣는 중"을 먼저 적고 (앞서 쓰기), add_markers를 100개씩 보낸다.
   답이 늦으면 다시 읽어서(get_markers prefix) 빠진 것만 한 번 더 보낸다 (꼬리표가 같으면 두 번 들어가지 않는다).
4. 다시 읽은 표시로 영수증을 만들고 일지를 applied / partial / undone으로 닫는다.

되돌리기 (undo_proposal): 그 일지의 타임라인일 때만. delete_markers{prefix: "aih:<P>:"}.
모두 빼기 (scan_ours + remove_all_ours): 지금 타임라인에서 꼬리표를 직접 찾는다 (일지가 없어도 된다).
옛 시험 흔적(aih_test 표시, "AI 도우미 시험" 트랙)도 뺀다. 트랙은 편집 화면 확인을 거친 뒤에만.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence

from ..resolve_link.bridge import BridgeError, BridgeTimeout
from ..resolve_link.ops import MAX_ADD_MARKERS, MarkerResult, MarkerSpec, ResolveOps, TimelineInfo
from ..timeline.snapshot import fingerprint
from .journal import Journal, key_for, marker_prefix
from .proposal import Proposal

LEGACY_TEST_TRACK = "AI 도우미 시험"  # 1차 시험판 [소리 넣기 시험]이 만든 트랙 (app/companion/steps.TEST_NAME)
LEGACY_TEST_TAG = "aih_test"  # 1차 시험판 표시의 꼬리표
OUR_PREFIX = "aih:"


@dataclass
class ApplyOutcome:
    status: str  # applied / partial / undone / changed / other_timeline / failed
    proposal_id: str
    expected: int = 0
    placed: int = 0
    failed: int = 0
    skipped_existing: int = 0
    point_fallback: bool = False
    dur_ok: Optional[bool] = None  # 다시 읽은 길이가 보낸 길이와 같은지 (길이 있는 표시)
    at: str = ""
    replaced: Dict[str, int] = field(default_factory=dict)  # 바꾸기로 뺀 이전 제안 → 뺀 수
    timeline_name: Optional[str] = None
    error: Optional[str] = None
    calls: Dict[str, Any] = field(default_factory=dict)
    receipt: Dict[str, Any] = field(default_factory=dict)
    created: List[Dict[str, Any]] = field(default_factory=list)


def _same_timeline(info: TimelineInfo, timeline: Dict[str, Any]) -> bool:
    uid = timeline.get("timeline_uid")
    if uid and info.timeline_uid:
        return uid == info.timeline_uid
    return key_for(info) == timeline.get("key")


def _now_hm() -> str:
    return time.strftime("%H:%M")


def _send_chunk(ops: ResolveOps, specs: Sequence[MarkerSpec], point_only: bool, prefix: str,
                cancel: Optional[threading.Event]) -> MarkerResult:
    """add_markers 한 번. 답이 늦으면 다시 읽어 빠진 것만 한 번 더 보낸다 (그래도 늦으면 BridgeTimeout)."""
    try:
        return ops.add_markers(specs, point_only=point_only, cancel=cancel)
    except BridgeTimeout:
        present = {m.get("custom") for m in ops.get_markers(prefix, cancel=cancel)}
        missing = [s for s in specs if s.custom not in present]
        result = MarkerResult(requested=0)
        result.skipped_existing = [s.custom for s in specs if s.custom in present]
        if missing:
            result.merge(ops.add_markers(missing, point_only=point_only, cancel=cancel))
        return result


Progress = Callable[[int, int], None]  # (보낸 묶음 수, 모든 묶음 수)


def _send_all(ops: ResolveOps, proposal: Proposal, specs: Sequence[MarkerSpec], total: MarkerResult,
              cancel: Optional[threading.Event], progress: Optional[Progress]) -> Optional[str]:
    """100개씩 보낸다. 멈춘 까닭(오류)이 있으면 그 글, 다 보냈으면 None."""
    chunks = [specs[i:i + MAX_ADD_MARKERS] for i in range(0, len(specs), MAX_ADD_MARKERS)]
    for n, chunk in enumerate(chunks, start=1):
        if progress is not None:
            progress(n, len(chunks))
        try:
            r = _send_chunk(ops, chunk, proposal.point_only or total.point_fallback, proposal.prefix, cancel)
        except BridgeTimeout as exc:
            return f"timeout:{exc}"
        except BridgeError as exc:
            return f"{exc.error}:{exc.func}"
        total.merge(r)
    return None


def apply_markers(ops: ResolveOps, proposal: Proposal, *, root: Optional[Path] = None,
                  replace: Sequence[str] = (), force: bool = False,
                  cancel: Optional[threading.Event] = None, progress: Optional[Progress] = None) -> ApplyOutcome:
    """제안의 표시를 넣는다. 영수증은 계획이 아니라 다시 읽은 표시로 만든다."""
    out = ApplyOutcome(status="failed", proposal_id=proposal.id, expected=proposal.count, at=_now_hm())
    info = ops.timeline_info(cancel=cancel)
    out.timeline_name = proposal.timeline.get("timeline")
    if not info.has_timeline or not _same_timeline(info, proposal.timeline):
        out.status = "other_timeline"
        return out
    items = ops.timeline_items("audio", cancel=cancel)
    fp = fingerprint(items)
    if fp != proposal.fingerprint and not force:
        out.status = "changed"
        return out
    journal = Journal.for_timeline(info, root)
    for old in replace:
        r = _undo_one(ops, journal, info, old, cancel)
        out.replaced[old] = r.deleted
    if not proposal.specs:
        out.status = "applied"
        return out
    journal.begin(proposal.id, origin=proposal.origin, request=proposal.request, commands=proposal.commands(),
                  expected_markers=proposal.expected_markers(), card_rows=[], plan_digest=proposal.digest())
    total = MarkerResult()
    error = _send_all(ops, proposal, proposal.specs, total, cancel, progress)
    return _finish(ops, proposal, journal, out, total, error, fp, cancel)


def resume_markers(ops: ResolveOps, proposal: Proposal, *, root: Optional[Path] = None,
                   cancel: Optional[threading.Event] = None, progress: Optional[Progress] = None) -> ApplyOutcome:
    """[이어서 넣기] (일부만 들어갔을 때): 다시 읽어서 빠진 표시만 보낸다. 같은 제안 번호와 꼬리표를 쓴다."""
    out = ApplyOutcome(status="failed", proposal_id=proposal.id, expected=proposal.count, at=_now_hm())
    info = ops.timeline_info(cancel=cancel)
    out.timeline_name = proposal.timeline.get("timeline")
    if not info.has_timeline or not _same_timeline(info, proposal.timeline):
        out.status = "other_timeline"
        return out
    journal = Journal.for_timeline(info, root)
    if journal.entry(proposal.id) is None:
        out.status = "missing"
        return out
    fp = fingerprint(ops.timeline_items("audio", cancel=cancel))
    present = {m.get("custom") for m in ops.get_markers(proposal.prefix, cancel=cancel)}
    missing = [s for s in proposal.specs if s.custom not in present]
    total = MarkerResult()
    total.skipped_existing = [s.custom for s in proposal.specs if s.custom in present]
    journal.mark(proposal.id, "applying")
    error = _send_all(ops, proposal, missing, total, cancel, progress) if missing else None
    return _finish(ops, proposal, journal, out, total, error, fp, cancel)


def _finish(ops: ResolveOps, proposal: Proposal, journal: Journal, out: ApplyOutcome, total: MarkerResult,
            error: Optional[str], fp: str, cancel: Optional[threading.Event]) -> ApplyOutcome:
    prefix = proposal.prefix
    point = proposal.point_only
    # 다시 읽기: 이 제안의 꼬리표가 붙은 표시
    try:
        markers = ops.get_markers(prefix, cancel=cancel)
    except (BridgeError, BridgeTimeout):
        markers = None
    expected = {s.custom: s for s in proposal.specs}
    if markers is None:
        # 다시 읽지 못함: 리졸브가 넣었다고 답한 것만 믿는다 (영수증에 그렇게 적는다)
        created = [{"frame": proposal.tl_start + p.frame, "custom": p.custom, "duration": p.dur_readback}
                   for p in total.placed if p.custom in expected]
        out.receipt["readback"] = False
    else:
        created = [{"frame": proposal.tl_start + int(m.get("frame") or 0), "custom": m.get("custom"),
                    "duration": m.get("duration")} for m in markers if m.get("custom") in expected]
        out.receipt["readback"] = True
    out.created = created
    out.placed = len(created)
    out.failed = len(total.failed)
    out.skipped_existing = len(total.skipped_existing)
    out.point_fallback = total.point_fallback or point
    if not out.point_fallback and created:
        out.dur_ok = all(c.get("duration") == expected[c["custom"]].dur for c in created)
    out.calls = total.calls
    out.error = error
    out.receipt.update({
        "placed": out.placed, "expected": out.expected, "failed": out.failed, "skipped_existing": out.skipped_existing,
        "point_fallback": out.point_fallback, "dur_ok": out.dur_ok, "at": out.at, "calls": total.calls,
        "error": error, "replaced": out.replaced,
    })
    entry = journal.finish(proposal.id, created_markers=created, receipt=out.receipt, after_fingerprint=fp)
    out.status = entry["status"]
    return out


@dataclass
class UndoOutcome:
    status: str  # undone / undo_failed / other_timeline / missing
    proposal_id: str
    expected: int = 0
    deleted: int = 0
    already_gone: int = 0
    remaining: Optional[int] = None
    message: Optional[str] = None
    calls: Dict[str, Any] = field(default_factory=dict)


def _undo_one(ops: ResolveOps, journal: Journal, info: TimelineInfo, pid: str,
              cancel: Optional[threading.Event]) -> UndoOutcome:
    e = journal.entry(pid)
    out = UndoOutcome(status="missing", proposal_id=pid)
    if e is None:
        return out
    blocker = journal.undo_blocker(info)
    if blocker is not None:
        out.status, out.message = "other_timeline", blocker
        return out
    out.expected = len(e.get("created", {}).get("markers") or []) or len(e.get("expected", {}).get("markers") or [])
    r = ops.delete_markers(prefix=marker_prefix(pid), cancel=cancel)
    out.deleted = int(r.get("deleted_count") or 0)
    rem = r.get("remaining")
    out.remaining = rem if isinstance(rem, int) else None
    out.already_gone = max(0, out.expected - out.deleted)
    out.calls = r.get("calls") if isinstance(r.get("calls"), dict) else {}
    out.status = "undone" if out.remaining in (0, None) else "undo_failed"
    e["undo"] = {"at": time.strftime("%Y-%m-%dT%H:%M:%S"), "deleted": out.deleted, "already_gone": out.already_gone,
                 "remaining": out.remaining}
    journal.mark(pid, out.status)
    return out


def undo_proposal(ops: ResolveOps, pid: str, *, root: Optional[Path] = None,
                  journal_key: Optional[str] = None, cancel: Optional[threading.Event] = None) -> UndoOutcome:
    """되돌리기 한 번 (카드 하나). 지금 타임라인이 그 일지의 타임라인이 아니면 거절한다."""
    info = ops.timeline_info(cancel=cancel)
    if journal_key is not None and journal_key != key_for(info):
        journal = Journal(root, journal_key)
        e = journal.entry(pid)
        if e is not None:
            out = UndoOutcome(status="other_timeline", proposal_id=pid)
            out.message = journal.undo_blocker(info)
            return out
    journal = Journal.for_timeline(info, root)
    return _undo_one(ops, journal, info, pid, cancel)


@dataclass
class OursScan:
    """지금 타임라인에서 도우미가 넣은 것 (꼬리표로 찾음)."""

    info: TimelineInfo
    markers: int  # aih: 표시
    legacy_markers: int  # 1차 시험판 aih_test 표시
    legacy_tracks: List[int]  # "AI 도우미 시험" 트랙 번호
    page: Optional[str]

    @property
    def total(self) -> int:
        return self.markers + self.legacy_markers + len(self.legacy_tracks)

    @property
    def needs_edit_page(self) -> bool:
        return bool(self.legacy_tracks) and self.page is not None and self.page != "edit"


def scan_ours(ops: ResolveOps, *, cancel: Optional[threading.Event] = None) -> OursScan:
    info = ops.timeline_info(cancel=cancel)
    ours = ops.marker_total(OUR_PREFIX, cancel=cancel) or 0
    legacy_rows, _ = ops.read_markers(LEGACY_TEST_TAG, limit=500, cancel=cancel)
    legacy = sum(1 for m in legacy_rows if m.get("custom") == LEGACY_TEST_TAG)
    tracks = [t.index for t in info.tracks.get("audio", []) if t.name == LEGACY_TEST_TRACK]
    return OursScan(info=info, markers=int(ours), legacy_markers=legacy, legacy_tracks=tracks, page=info.page)


@dataclass
class RemoveAllOutcome:
    markers_deleted: int = 0
    markers_left: Optional[int] = None
    legacy_deleted: int = 0
    track: Optional[Dict[str, Any]] = None  # remove_audio 답 (page, delete_clips, delete_track ...)
    track_skipped: Optional[str] = None  # markers_only / need_edit_page / error
    journal_undone: List[str] = field(default_factory=list)
    other_timeline: bool = False


def remove_all_ours(ops: ResolveOps, scan: OursScan, *, root: Optional[Path] = None, remove_track: bool = True,
                    switch_page: bool = False, cancel: Optional[threading.Event] = None) -> RemoveAllOutcome:
    """확인을 받은 뒤: 지금 타임라인의 aih: 표시, aih_test 표시, (편집 화면이면) 옛 시험 트랙을 뺀다.

    타임라인은 지우지 않는다. 확인할 때 본 타임라인이 아니면 아무것도 하지 않는다.
    """
    out = RemoveAllOutcome()
    info = ops.timeline_info(cancel=cancel)
    if key_for(info) != key_for(scan.info):
        out.other_timeline = True
        return out
    if scan.markers:
        r = ops.delete_markers(prefix=OUR_PREFIX, cancel=cancel)
        out.markers_deleted = int(r.get("deleted_count") or 0)
        rem = r.get("remaining")
        out.markers_left = rem if isinstance(rem, int) else None
    if scan.legacy_markers:
        r = ops.delete_markers(LEGACY_TEST_TAG, cancel=cancel)
        out.legacy_deleted = int(r.get("deleted_count") or 0)
    if scan.legacy_tracks:
        if not remove_track:
            out.track_skipped = "markers_only"
        else:
            try:
                out.track = ops.remove_audio(LEGACY_TEST_TRACK, switch_page=switch_page, cancel=cancel)
            except BridgeError as exc:
                out.track_skipped = "need_edit_page" if exc.error == "need_edit_page" else f"error:{exc.error}"
    journal = Journal.for_timeline(info, root)
    for e in journal.entries:
        if e.get("status") in ("applied", "partial", "applying"):
            e["status"] = "undone"
            e["undo"] = {"at": time.strftime("%Y-%m-%dT%H:%M:%S"), "by": "remove_all"}
            out.journal_undone.append(e.get("proposal_id"))
    if out.journal_undone:
        journal.save()
    return out


def active_entries(journal: Journal, kind: str) -> List[Dict[str, Any]]:
    """같은 종류로 넣어 둔 것 (다시 누르면 [바꾸기]/[더하기]를 묻는다)."""
    out = []
    for e in journal.entries:
        cmds = e.get("commands") or []
        op = cmds[0].get("op") if cmds and isinstance(cmds[0], dict) else None
        if op == kind and e.get("status") in ("applied", "partial"):
            out.append(e)
    return out
