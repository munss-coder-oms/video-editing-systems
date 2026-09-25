"""대화의 표시 넣기(mark)와 도우미 표시 지우기(clear_marks) (설계 B3, B6).

- 표시 넣기: 지문(클립 목록)을 보지 않는 제안. 넣기·되돌리기는 자동화 버튼과 같다.
- 지우기: 계산(읽기만) → [리졸브에 넣기] → 꼬리표로만 지움 → 영수증 → 되돌리기(같은 꼬리표로 다시 넣음).
- 사용자가 찍은 표시와 다른 프로그램의 표시는 어떤 경우에도 그대로다.
"""

from __future__ import annotations

import pytest

from engine.edits import apply as ap
from engine.edits.journal import Journal
from engine.edits.marks import apply_clear, mark_proposal, plan_clear
from engine.resolve_link.ops import ResolveOps, TimelineInfo
from tests.fakes import FakeResolve, timeline_info

TL0 = 216000
FPS = 60
USER = {  # 사용자·다른 프로그램의 표시 (타임라인 시작부터 센 프레임 → 꼬리표, 색)
    700: ("", "Blue"),
    60 * FPS + 30: ("내 꼬리표", "Blue"),
    120 * FPS: ("autosubs_1", "Blue"),
    200 * FPS: ("aih-not-ours", "Red"),
    300 * FPS: ("aih_test2", "Red"),
}


def _fake() -> FakeResolve:
    fake = FakeResolve(timeline_info(end_frame=TL0 + 600 * FPS))
    for f, (custom, color) in USER.items():
        fake.add_user_marker(f, custom, color=color)
    return fake


def _info(fake):
    return TimelineInfo.from_result(fake.info)


def _user(fake):
    return {f: dict(m) for f, m in fake.markers.items() if f in USER}


def _ours(fake):
    return {f: m for f, m in fake.markers.items() if str(m.get("custom", "")).startswith("aih:")}


def _item(sec, color, end_sec=None, name="표시"):
    at = TL0 + int(sec * FPS)
    end = TL0 + int(end_sec * FPS) if end_sec else at + 1
    return {"at": at, "end": end, "color": color, "name": name, "note": "메모", "src": {"at": "said"}}


def _put(fake, root, *items, kind="mark"):
    p = mark_proposal(list(items), _info(fake), request="표시")
    if kind != "mark":
        p.kind = kind
    out = ap.apply_markers(ResolveOps(fake), p, root=root)
    assert out.status == "applied", out
    return p


def test_mark_proposal_goes_in_without_reading_the_clip_list(tmp_path):
    fake = _fake()
    before = _user(fake)
    p = _put(fake, tmp_path, _item(60, "Red"), _item(90, "Blue", 95))
    assert "timeline_items" not in fake.requests  # 지문이 없으니 클립 목록을 다시 읽지 않는다
    assert sorted(_ours(fake)) == [60 * FPS, 90 * FPS]
    assert fake.markers[90 * FPS]["duration"] == 5 * FPS and fake.markers[60 * FPS]["duration"] == 1
    e = Journal.for_timeline(_info(fake), tmp_path).entry(p.id)
    assert e["commands"][0]["op"] == "mark" and e["origin"] == "chat:rule" and e["status"] == "applied"
    out = ap.undo_proposal(ResolveOps(fake), p.id, root=tmp_path)
    assert out.status == "undone" and out.deleted == 2 and _ours(fake) == {}
    assert _user(fake) == before


# ── 고르기 (plan_clear: 읽기만) ───────────────────────────────────────


def test_plan_filters_by_colour_kind_and_range_and_never_touches_resolve(tmp_path):
    fake = _fake()
    mark = _put(fake, tmp_path, _item(10, "Blue"), _item(20, "Red"), _item(299, "Blue", 302))
    pauses = _put(fake, tmp_path, _item(330, "Blue", 333), _item(359, "Blue", 361), kind="mark_pauses")
    ops = ResolveOps(fake)
    n = len(fake.mutations)

    everything = plan_clear(ops, root=tmp_path)
    assert everything.count == 5 and everything.total_ours == 5 and everything.which() == "all_ours"
    blue = plan_clear(ops, colors=["Blue"], root=tmp_path)
    assert blue.count == 4 and blue.colors == ["Blue"] and blue.which() == "color:Blue"
    kinds = plan_clear(ops, kinds=["mark_pauses"], root=tmp_path)
    assert sorted(t["pid"] for t in kinds.targets) == [pauses.id, pauses.id] and kinds.which() == "kind:mark_pauses"
    assert {t["kind"] for t in kinds.targets} == {"mark_pauses"}
    # 5:00~6:00: 다 들어가는 것만 (5:30~5:33). 4:59~5:02와 5:59~6:01은 걸쳐 있어서 세기만 한다
    rng = plan_clear(ops, lo=TL0 + 300 * FPS, hi=TL0 + 360 * FPS, root=tmp_path)
    assert [t["frame"] - TL0 for t in rng.targets] == [330 * FPS] and rng.straddling == 2
    assert rng.scope == {"kind": "range", "lo": TL0 + 300 * FPS, "hi": TL0 + 360 * FPS}
    # 한 점 (0:20 앞뒤 0.5초): 그 안에서 시작하는 표시
    point = plan_clear(ops, lo=TL0 + 20 * FPS - 30, hi=TL0 + 20 * FPS + 31, point=True, root=tmp_path)
    assert [t["color"] for t in point.targets] == ["Red"] and point.effects() == [(TL0 + 20 * FPS, TL0 + 20 * FPS + 1)]
    assert all(c.startswith("aih:") for p in (everything, blue, kinds, rng, point) for c in p.customs)
    assert len(fake.mutations) == n and not ({"add_markers", "delete_markers"} & set(fake.requests[-10:]))
    assert mark.id != pauses.id


# ── 지우기와 되돌리기 ─────────────────────────────────────────────────


def test_clear_deletes_only_the_shown_tags_and_undo_puts_them_back(tmp_path):
    fake = _fake()
    before = _user(fake)
    a = _put(fake, tmp_path, _item(10, "Blue"), _item(20, "Blue"))
    b = _put(fake, tmp_path, _item(30, "Blue"), _item(40, "Red"))
    ops = ResolveOps(fake)
    plan = plan_clear(ops, colors=["Blue"], root=tmp_path, request="도우미가 넣은 파란 표시 지워줘")
    assert plan.count == 3
    out = apply_clear(ops, plan, root=tmp_path)
    assert out.status == "applied" and (out.expected, out.deleted, out.left) == (3, 3, 0)
    assert [m["color"] for m in _ours(fake).values()] == ["Red"]
    assert _user(fake) == before  # 사용자·다른 프로그램의 파란 표시는 그대로
    calls = [x for op, x in zip(fake.requests, fake.args) if op == "delete_markers"]
    assert len(calls) == 1 and calls[0]["prefix"] == "aih:" and sorted(calls[0]["customs"]) == sorted(plan.customs)
    assert calls[0]["snapshot"] is True
    j = Journal.for_timeline(_info(fake), tmp_path)
    e = j.entry(plan.id)
    assert e["commands"][0]["op"] == "clear_marks" and e["status"] == "applied"
    assert sorted(e["deleted"]) == sorted(plan.customs) and len(e["deleted_snapshot"]) == 3
    # a는 표시가 모두 지워졌으니 "뺌", b는 빨간 표시가 남았으니 그대로
    assert out.closed == [a.id]
    assert j.entry(a.id)["status"] == "undone" and j.entry(a.id)["undo"]["by"] == f"clear:{plan.id}"
    assert j.entry(b.id)["status"] == "applied"

    u = ap.undo_proposal(ops, plan.id, root=tmp_path)
    assert u.status == "undone" and u.restored and u.deleted == 3 and u.remaining == 0 and u.reopened == [a.id]
    ours = _ours(fake)
    assert sorted(ours) == [10 * FPS, 20 * FPS, 30 * FPS, 40 * FPS]
    assert all(m["name"] == "표시" and m["note"] for m in ours.values())
    assert {m["custom"] for m in ours.values()} >= set(plan.customs)  # 같은 꼬리표로 다시
    j = Journal.for_timeline(_info(fake), tmp_path)
    assert j.entry(a.id)["status"] == "applied" and "undo" not in j.entry(a.id)
    assert j.entry(plan.id)["status"] == "undone"
    assert _user(fake) == before
    # 다시 넣은 a는 보통 되돌리기로 뺄 수 있다
    assert ap.undo_proposal(ops, a.id, root=tmp_path).deleted == 2


def test_undo_clear_skips_markers_whose_card_was_undone_since(tmp_path):
    fake = _fake()
    a = _put(fake, tmp_path, _item(10, "Blue"))
    b = _put(fake, tmp_path, _item(30, "Blue"), _item(40, "Red"))
    ops = ResolveOps(fake)
    plan = plan_clear(ops, colors=["Blue"], root=tmp_path)
    apply_clear(ops, plan, root=tmp_path)
    # 그사이 b를 되돌리기로 뺐다 (빨간 표시가 빠짐)
    assert ap.undo_proposal(ops, b.id, root=tmp_path).status == "undone"
    u = ap.undo_proposal(ops, plan.id, root=tmp_path)
    assert u.restored and u.deleted == 1 and u.reopened == [a.id]
    assert sorted(_ours(fake)) == [10 * FPS]  # b의 파란 표시는 다시 넣지 않는다


def test_nothing_to_clear_changes_nothing(tmp_path):
    fake = _fake()
    ops = ResolveOps(fake)
    plan = plan_clear(ops, colors=["Pink"], root=tmp_path)
    assert plan.count == 0
    out = apply_clear(ops, plan, root=tmp_path)
    assert out.status == "applied" and out.deleted == 0 and "delete_markers" not in fake.requests
    assert Journal.for_timeline(_info(fake), tmp_path).entry(plan.id) is None


def test_clear_on_another_timeline_is_refused(tmp_path):
    fake = _fake()
    _put(fake, tmp_path, _item(10, "Blue"))
    ops = ResolveOps(fake)
    plan = plan_clear(ops, root=tmp_path)
    fake.info.update(timeline="Timeline 2", timeline_uid="tl-2")
    out = apply_clear(ops, plan, root=tmp_path)
    assert out.status == "other_timeline" and "delete_markers" not in fake.requests and len(_ours(fake)) == 1


def test_many_tags_go_in_chunks_of_two_hundred(tmp_path):
    fake = _fake()
    ops = ResolveOps(fake)
    for n in range(3):
        _put(fake, tmp_path, *[_item(1 + n * 150 + k * 0.5, "Green") for k in range(150)])
    plan = plan_clear(ops, root=tmp_path)
    assert plan.count == 450
    out = apply_clear(ops, plan, root=tmp_path)
    sizes = [len(x["customs"]) for op, x in zip(fake.requests, fake.args) if op == "delete_markers"]
    assert sizes == [200, 200, 50] and out.deleted == 450 and _ours(fake) == {} and len(out.closed) == 3


def test_journal_refuses_to_plan_deleting_foreign_tags(tmp_path):
    j = Journal(tmp_path, "tl")
    with pytest.raises(ValueError):
        j.begin_clear("P1", origin="chat:rule", request="", commands=[], expected_deleted=["aih:P0:1", "autosubs_1"])


def test_interrupted_clear_is_reconciled_on_connect(tmp_path):
    """지우다 앱이 꺼짐: 다음 연결에서 없어진 꼬리표로 맞추고, 되돌리기는 계산 때 적어 둔 모양으로 한다."""
    fake = _fake()
    a = _put(fake, tmp_path, _item(10, "Blue"), _item(20, "Blue"))
    ops = ResolveOps(fake)
    plan = plan_clear(ops, root=tmp_path)
    j = Journal.for_timeline(_info(fake), tmp_path)
    j.begin_clear(plan.id, origin="chat:rule", request="", commands=plan.commands(), expected_deleted=plan.customs,
                  snapshot=plan.snapshot_rows())
    # 하나만 지워지고 꺼졌다
    ops.delete_markers(prefix="aih:", customs=plan.customs[:1])
    j = Journal.for_timeline(_info(fake), tmp_path)
    rec = j.reconcile(ops.get_markers("aih:"))
    assert [(r.proposal_id, r.status) for r in rec] == [(plan.id, "partial")]
    e = j.entry(plan.id)
    assert e["deleted"] == plan.customs[:1]
    u = ap.undo_proposal(ops, plan.id, root=tmp_path)
    assert u.restored and u.deleted == 1 and sorted(_ours(fake)) == [10 * FPS, 20 * FPS]
    assert a.id in {m["custom"].split(":")[1] for m in _ours(fake).values()}
