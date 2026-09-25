"""[리졸브에 넣기]·되돌리기·모두 빼기 (설계 B2.5 5~7, B6.2, B6.3, B11 test_markers_apply).

가짜 리졸브(FakeResolve)는 Lua 스크립트와 같은 규칙으로 답한다: 꼬리표가 같으면 두 번 넣지 않고,
자리가 차 있으면 다섯 프레임까지 뒤로 밀고, 길이 있는 표시를 거절하는 판도 흉내 낸다.
사용자 표시(꼬리표가 우리 것이 아닌 것)는 어떤 경우에도 그대로 있어야 한다.
"""

from __future__ import annotations

import pytest

from engine.edits import apply as ap
from engine.edits.journal import Journal, key_for
from engine.edits.proposal import MarkerRow, Proposal, build_specs, new_proposal_id
from engine.resolve_link.bridge import BridgeTimeout
from engine.resolve_link.ops import Item, ResolveOps, TimelineInfo
from engine.timeline.snapshot import TimelineSnapshot
from tests.fakes import FakeResolve, audio_item, obs_items, timeline_info

TL0 = 216000
FPS = 60.0
USER = {100: "", 400: "내 꼬리표", 7000: "aih-not-ours"}  # 사용자 표시 (프레임 → 꼬리표)


def _fake(**info) -> FakeResolve:
    fake = FakeResolve(timeline_info(**info), obs_items("C:/rec.mp4", TL0, 60000))
    for f, custom in USER.items():
        fake.add_user_marker(f, custom)
    return fake


def _user_markers(fake: FakeResolve):
    return {f: m for f, m in fake.markers.items() if f in USER}


def _proposal(fake: FakeResolve, n: int = 3, *, gap: int = 600, dur: int = 120, point: bool = False,
              kind: str = "mark_pauses", pid=None) -> Proposal:
    snap = TimelineSnapshot(TimelineInfo.from_result(fake.info), [Item.from_row(r) for r in fake.items])
    pid = pid or new_proposal_id()
    rows = [MarkerRow(TL0 + 1000 + i * gap, TL0 + 1000 + i * gap + dur, dur / FPS, f"쉼 {dur / FPS:.1f}초", "")
            for i in range(n)]
    specs = build_specs(pid, rows, TL0, "Blue", point=point, note_for=lambda r: "쉰 길이")
    return Proposal(id=pid, kind=kind, slot=1, origin="button:1", request="쉬는 곳 표시", params={"min_s": 1.5},
                    rows=rows, specs=specs, timeline=snap.timeline_record(), fps=FPS, tl_start=TL0,
                    tl_end=snap.end, fingerprint=snap.fingerprint(), point_only=point)


def _ours(fake: FakeResolve, pid: str):
    return sorted(f for f, m in fake.markers.items() if str(m["custom"]).startswith(f"aih:{pid}:"))


def _journal(fake: FakeResolve, root) -> Journal:
    return Journal.for_timeline(TimelineInfo.from_result(fake.info), root)


def test_apply_places_markers_and_writes_the_journal(tmp_path):
    fake = _fake()
    p = _proposal(fake)
    before = {f: dict(m) for f, m in _user_markers(fake).items()}
    out = ap.apply_markers(ResolveOps(fake), p, root=tmp_path)
    assert out.status == "applied" and (out.expected, out.placed, out.failed) == (3, 3, 0)
    assert out.dur_ok is True and out.point_fallback is False and out.receipt["readback"] is True
    assert _ours(fake, p.id) == [1000, 1600, 2200]
    assert all(fake.markers[f]["duration"] == 120 for f in _ours(fake, p.id))
    assert [c["frame"] for c in out.created] == [TL0 + 1000, TL0 + 1600, TL0 + 2200]  # 일지는 절대 프레임
    assert _user_markers(fake) == before
    assert {m[0] for m in fake.mutations} == {"AddMarker"}
    e = _journal(fake, tmp_path).entry(p.id)
    assert e["status"] == "applied" and e["origin"] == "button:1" and e["commands"][0]["op"] == "mark_pauses"
    assert [m["custom"] for m in e["expected"]["markers"]] == [s.custom for s in p.specs]
    assert e["plan_digest"] == p.digest() and e["after_fingerprint"] == p.fingerprint
    assert ap.active_entries(_journal(fake, tmp_path), "mark_pauses")[0]["proposal_id"] == p.id
    assert ap.active_entries(_journal(fake, tmp_path), "mark_spikes") == []


def test_many_markers_go_in_chunks_of_a_hundred(tmp_path):
    fake = _fake()
    p = _proposal(fake, 250, gap=200, dur=60)
    seen = []
    out = ap.apply_markers(ResolveOps(fake), p, root=tmp_path, progress=lambda i, n: seen.append((i, n)))
    assert out.status == "applied" and out.placed == 250
    sent = [len(a["markers"]) for op, a in zip(fake.requests, fake.args) if op == "add_markers"]
    assert sent == [100, 100, 50] and seen == [(1, 3), (2, 3), (3, 3)]


def test_late_answer_is_read_back_and_nothing_goes_in_twice(tmp_path):
    fake = _fake()
    fake.timeout_once.add("add_markers")  # 넣은 뒤 답이 늦다
    p = _proposal(fake)
    out = ap.apply_markers(ResolveOps(fake), p, root=tmp_path)
    assert out.status == "applied" and out.placed == 3
    assert fake.requests.count("add_markers") == 1  # 다시 읽어 보니 다 있어서 다시 보내지 않았다
    customs = [m["custom"] for m in fake.markers.values()]
    assert all(customs.count(s.custom) == 1 for s in p.specs)


def test_late_answer_twice_leaves_a_partial_entry(tmp_path, monkeypatch):
    fake = _fake()
    p = _proposal(fake, 150, gap=300, dur=60)
    calls = {"n": 0}
    real = fake._op_add_markers

    def slow_second_chunk(a):
        calls["n"] += 1
        if calls["n"] >= 2:
            raise BridgeTimeout("add_markers")  # 둘째 묶음은 넣지 못하고 답도 없다
        return real(a)

    monkeypatch.setattr(fake, "_op_add_markers", slow_second_chunk)
    out = ap.apply_markers(ResolveOps(fake), p, root=tmp_path)
    assert out.status == "partial" and out.placed == 100 and out.error.startswith("timeout")
    assert _journal(fake, tmp_path).entry(p.id)["status"] == "partial"
    # [이어서 넣기]: 빠진 50개만 보낸다
    monkeypatch.setattr(fake, "_op_add_markers", real)
    fake.requests.clear()
    fake.args.clear()
    out = ap.resume_markers(ResolveOps(fake), p, root=tmp_path)
    assert out.status == "applied" and out.placed == 150 and out.skipped_existing == 100
    sent = [a["markers"] for op, a in zip(fake.requests, fake.args) if op == "add_markers"]
    assert [len(x) for x in sent] == [50] and sent[0][0]["custom"] == p.specs[100].custom
    assert _journal(fake, tmp_path).entry(p.id)["status"] == "applied"


def test_taken_frames_make_a_partial_then_resume_fills_the_gap(tmp_path):
    fake = _fake()
    p = _proposal(fake)
    for k in range(6):  # 둘째 표시 자리부터 다섯 프레임 뒤까지 모두 차 있다
        fake.add_user_marker(1600 + k, "", name="막힘")
    out = ap.apply_markers(ResolveOps(fake), p, root=tmp_path)
    assert out.status == "partial" and (out.placed, out.failed) == (2, 1)
    for k in range(6):
        del fake.markers[1600 + k]
    out = ap.resume_markers(ResolveOps(fake), p, root=tmp_path)
    assert out.status == "applied" and out.placed == 3 and _ours(fake, p.id) == [1000, 1600, 2200]
    assert ap.resume_markers(ResolveOps(fake), _proposal(fake), root=tmp_path).status == "missing"


def test_shifted_marker_keeps_its_end(tmp_path):
    fake = _fake()
    p = _proposal(fake, 1)
    fake.add_user_marker(1000, "", name="막힘")
    out = ap.apply_markers(ResolveOps(fake), p, root=tmp_path)
    assert out.status == "applied" and _ours(fake, p.id) == [1001]
    assert fake.markers[1001]["duration"] == 119 and out.dur_ok is False


def test_changed_timeline_asks_first_and_force_puts_them_in(tmp_path):
    fake = _fake()
    p = _proposal(fake)
    fake.items[0]["start"] += 30  # 계산한 뒤 클립을 옮겼다
    fake.items[0]["end"] += 30
    out = ap.apply_markers(ResolveOps(fake), p, root=tmp_path)
    assert out.status == "changed" and fake.mutations == [] and _journal(fake, tmp_path).entry(p.id) is None
    out = ap.apply_markers(ResolveOps(fake), p, root=tmp_path, force=True)
    assert out.status == "applied" and out.placed == 3


def test_other_timeline_is_refused(tmp_path):
    fake = _fake()
    p = _proposal(fake)
    fake.info.update(timeline="Timeline 2", timeline_uid="tl-2")
    out = ap.apply_markers(ResolveOps(fake), p, root=tmp_path)
    assert out.status == "other_timeline" and out.timeline_name == "Timeline 1"
    assert fake.requests == ["timeline_info"] and fake.mutations == []
    fake.info.update(timeline=None, timeline_uid=None)
    assert ap.apply_markers(ResolveOps(fake), p, root=tmp_path).status == "other_timeline"


def test_replace_removes_the_earlier_proposal_first(tmp_path):
    fake = _fake()
    ops = ResolveOps(fake)
    first = _proposal(fake)
    ap.apply_markers(ops, first, root=tmp_path)
    second = _proposal(fake, 2, gap=900)
    out = ap.apply_markers(ops, second, root=tmp_path, replace=[first.id])
    assert out.status == "applied" and out.replaced == {first.id: 3}
    assert _ours(fake, first.id) == [] and _ours(fake, second.id) == [1000, 1900]
    j = _journal(fake, tmp_path)
    assert j.entry(first.id)["status"] == "undone" and j.entry(second.id)["status"] == "applied"
    assert [e["proposal_id"] for e in ap.active_entries(j, "mark_pauses")] == [second.id]
    assert len(_user_markers(fake)) == len(USER)


def test_point_fallback_when_ranges_are_refused(tmp_path):
    fake = _fake()
    fake.no_range_markers = True
    p = _proposal(fake)
    out = ap.apply_markers(ResolveOps(fake), p, root=tmp_path)
    assert out.status == "applied" and out.point_fallback is True and out.dur_ok is None
    assert all(fake.markers[f]["duration"] == 1 for f in _ours(fake, p.id))
    # 점 표시로 계산한 제안은 처음부터 point_only
    fake2 = _fake()
    p2 = _proposal(fake2, point=True)
    out = ap.apply_markers(ResolveOps(fake2), p2, root=tmp_path)
    assert out.point_fallback is True and all(a.get("point_only") for op, a in zip(fake2.requests, fake2.args)
                                              if op == "add_markers")


def test_empty_proposal_changes_nothing(tmp_path):
    fake = _fake()
    p = _proposal(fake, 0)
    out = ap.apply_markers(ResolveOps(fake), p, root=tmp_path)
    assert out.status == "applied" and fake.mutations == [] and _journal(fake, tmp_path).entries == []


# ── 되돌리기 ───────────────────────────────────────────────────────────

def test_undo_removes_only_that_proposal(tmp_path):
    fake = _fake()
    ops = ResolveOps(fake)
    a, b = _proposal(fake), _proposal(fake, 2, gap=700, kind="mark_spikes")
    b.specs = build_specs(b.id, [MarkerRow(TL0 + 5000 + i * 700, TL0 + 5060 + i * 700, 9.0, "튀는 소리", "")
                                 for i in range(2)], TL0, "Red", point=False, note_for=lambda r: "")
    ap.apply_markers(ops, a, root=tmp_path)
    ap.apply_markers(ops, b, root=tmp_path)
    key = key_for(TimelineInfo.from_result(fake.info))
    out = ap.undo_proposal(ops, a.id, root=tmp_path, journal_key=key)
    assert out.status == "undone" and (out.expected, out.deleted, out.already_gone) == (3, 3, 0)
    assert _ours(fake, a.id) == [] and len(_ours(fake, b.id)) == 2 and len(_user_markers(fake)) == len(USER)
    j = _journal(fake, tmp_path)
    assert j.entry(a.id)["status"] == "undone" and j.entry(a.id)["undo"]["deleted"] == 3
    # 사용자가 하나를 먼저 지웠으면 "이미 없음"으로 센다
    first = _ours(fake, b.id)[0]
    del fake.markers[first]
    out = ap.undo_proposal(ops, b.id, root=tmp_path, journal_key=key)
    assert (out.deleted, out.already_gone) == (1, 1)
    deletes = [a for op, a in zip(fake.requests, fake.args) if op == "delete_markers"]
    assert all(d.get("prefix", "").startswith("aih:P") and "custom" not in d for d in deletes)


def test_undo_on_another_timeline_is_refused(tmp_path):
    fake = _fake()
    ops = ResolveOps(fake)
    p = _proposal(fake)
    ap.apply_markers(ops, p, root=tmp_path)
    key = key_for(TimelineInfo.from_result(fake.info))
    fake.info.update(timeline="Timeline 2", timeline_uid="tl-2")
    fake.requests.clear()
    out = ap.undo_proposal(ops, p.id, root=tmp_path, journal_key=key)
    assert out.status == "other_timeline" and "Timeline 1" in out.message
    assert "delete_markers" not in fake.requests and len(_ours(fake, p.id)) == 3
    assert ap.undo_proposal(ops, "Pnothing", root=tmp_path).status == "missing"


# ── 모두 빼기 ──────────────────────────────────────────────────────────

def _with_legacy(page: str = "edit") -> FakeResolve:
    fake = _fake(page=page)
    fake.add_user_marker(3000, "aih_test", color="Green", name="시험")
    fake.add_user_marker(3100, "aih_test", color="Green", name="시험")
    fake.info["tracks"]["audio"].append({"index": 5, "name": ap.LEGACY_TEST_TRACK, "enabled": True,
                                         "locked": False, "subtype": "stereo", "count": 1})
    fake.items.append(audio_item("t1", 5, TL0, 300, "C:/state/test_tone.wav"))
    return fake


def test_scan_counts_tags_and_legacy_traces(tmp_path):
    fake = _with_legacy()
    ops = ResolveOps(fake)
    ap.apply_markers(ops, _proposal(fake), root=tmp_path)
    fake.requests.clear()
    scan = ap.scan_ours(ops)
    assert (scan.markers, scan.legacy_markers, scan.legacy_tracks) == (3, 2, [5]) and scan.total == 6
    assert scan.needs_edit_page is False and fake.mutations and set(fake.requests) <= {"timeline_info", "get_markers"}
    assert ap.scan_ours(ResolveOps(_with_legacy("color"))).needs_edit_page is True
    assert ap.scan_ours(ResolveOps(_fake(page="color"))).needs_edit_page is False  # 트랙이 없으면 화면은 상관없다


def test_remove_all_on_the_edit_page(tmp_path):
    fake = _with_legacy()
    ops = ResolveOps(fake)
    p = _proposal(fake)
    ap.apply_markers(ops, p, root=tmp_path)
    before = {f: dict(m) for f, m in _user_markers(fake).items()}
    out = ap.remove_all_ours(ops, ap.scan_ours(ops), root=tmp_path)
    assert (out.markers_deleted, out.legacy_deleted, out.markers_left) == (3, 2, 0)
    assert out.track["removed_tracks"] == 1 and out.track_skipped is None and out.journal_undone == [p.id]
    assert _user_markers(fake) == before and 3000 not in fake.markers
    assert all(t["name"] != ap.LEGACY_TEST_TRACK for t in fake.info["tracks"]["audio"])
    assert _journal(fake, tmp_path).entry(p.id)["status"] == "undone"
    assert ap.scan_ours(ops).total == 0


@pytest.mark.parametrize("choice", ["need_edit_page", "switch", "markers_only"])
def test_remove_all_off_the_edit_page(tmp_path, choice):
    fake = _with_legacy("color")
    ops = ResolveOps(fake)
    scan = ap.scan_ours(ops)
    assert scan.needs_edit_page
    out = ap.remove_all_ours(ops, scan, root=tmp_path, remove_track=choice != "markers_only",
                             switch_page=choice == "switch")
    assert out.legacy_deleted == 2 and len(_user_markers(fake)) == len(USER)
    names = [t["name"] for t in fake.info["tracks"]["audio"]]
    if choice == "switch":
        assert out.track["switched_page"] is True and fake.opened_pages == ["edit", "color"]
        assert ap.LEGACY_TEST_TRACK not in names
    else:
        assert out.track_skipped == choice and ap.LEGACY_TEST_TRACK in names and fake.opened_pages == []
    if choice == "markers_only":
        assert "remove_audio" not in fake.requests


def test_remove_all_after_the_timeline_changed_does_nothing(tmp_path):
    fake = _with_legacy()
    ops = ResolveOps(fake)
    scan = ap.scan_ours(ops)
    fake.info.update(timeline="Timeline 2", timeline_uid="tl-2")
    out = ap.remove_all_ours(ops, scan, root=tmp_path)
    assert out.other_timeline is True and fake.mutations == []

