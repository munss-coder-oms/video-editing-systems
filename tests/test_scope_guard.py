"""범위 지킴이와 출처 (설계 B4.3, B4.4, B11 test_scope_guard / test_proposal).

| 경우 | 하는 일 |
| 바뀌는 자리가 말한 범위 밖 (0.5초 여유) | 막음 |
| 말하지 않은 트랙 | 막음 |
| 말한 수보다 많음 | 경고 |
| 전체에 적용되는데 "전체"라고 말하지 않음 | 경고 |
| 타임라인의 절반보다 넓은 범위 | 경고 |
| 못 알아들은 부분 | 경고 (알아들은 줄만 넣는다) |
쉬는 곳·튀는 소리는 계산할 때 범위 안쪽으로 잘랐으므로 막히지 않고, 자르지 않는 일(표시)이 1초 밖이면 막힌다.
"""

from __future__ import annotations

import pytest

from engine.chat.brain import DEFAULT, SAID, RequestedScope
from engine.edits import scope as G
from engine.edits.marks import mark_proposal, moved_mark, remade_mark
from engine.edits.proposal import MarkerRow, Proposal
from engine.resolve_link.ops import TimelineInfo
from tests.fakes import timeline_info

TL0 = 216000
FPS = 60
END = TL0 + 600 * FPS  # 10분
MIN5, MIN6 = TL0 + 300 * FPS, TL0 + 360 * FPS


def _check(requested, effects, *, count=None, **kw):
    return G.check(requested, effects=effects, fps=FPS, tl_start=TL0, tl_end=END,
                   count=len(effects) if count is None else count, **kw)


def _said(lo_s, hi_s, src="said", **kw):
    return {"range": [TL0 + int(lo_s * FPS), TL0 + int(hi_s * FPS)], "range_src": src, "said_whole": False,
            "tracks": None, "count_hint": None, **kw}


def _s(sec: float) -> int:
    return TL0 + int(round(sec * FPS))


# ── 막기: 범위 밖 ─────────────────────────────────────────────────────


@pytest.mark.parametrize("effect,blocked", [
    ((300.0, 303.0), False),
    ((359.0, 360.0), False),
    ((299.6, 303.0), False),  # 0.5초 여유 안
    ((359.0, 360.5), False),
    ((299.4, 303.0), True),  # 0.5초 넘게 앞
    ((359.0, 361.0), True),  # 끝이 1초 밖: 자르지 않는 일은 막는다
    ((200.0, 201.0), True),
])
def test_effect_outside_the_said_range_blocks(effect, blocked):
    g = _check(_said(300, 360), [(_s(effect[0]), _s(effect[1]))])
    assert g.blocked is blocked
    if blocked:
        line = g.blocks[0]
        assert line.code == "outside_range" and line.data["lo_s"] == 300.0 and line.data["hi_s"] == 360.0


def test_clipped_finds_never_block_but_the_same_unclipped_effect_does():
    """쉼 4:58~5:03을 5:00~6:00에서 찾으면 계산이 5:00~5:03으로 자른다 → 막지 않음.
    자르지 않은 4:58~5:03(표시 넣기 같은 일)은 2초 밖이라 막는다."""
    clipped = [(_s(300.0), _s(303.0)), (_s(330.0), _s(333.0))]
    assert not _check(_said(300, 360), clipped).blocked
    assert _check(_said(300, 360), [(_s(298.0), _s(303.0))]).blocked


def test_around_window_and_whole_range():
    g = _check(_said(175, 185, "around", count_hint=1), [(_s(176.0), _s(178.0))])
    assert g.codes() == []
    # 전체라고 말했으면 범위 검사를 하지 않는다
    whole = {"range": [TL0, END], "range_src": "whole", "said_whole": True, "tracks": None, "count_hint": None}
    assert _check(whole, [(_s(1.0), _s(599.0))]).codes() == []


# ── 막기: 트랙 ────────────────────────────────────────────────────────


def test_tracks_outside_what_was_said_block():
    g = _check(_said(300, 360, tracks=[2]), [(_s(300), _s(301))], tracks=[1, 2])
    assert g.blocked and g.blocks[0].code == "outside_tracks" and g.blocks[0].data["tracks"] == [1]
    # 버튼에 정한 트랙은 괜찮다
    assert not _check(_said(300, 360, tracks=[2]), [(_s(300), _s(301))], tracks=[1, 2], slot_tracks=[1]).blocked
    # 트랙을 말하지 않았으면 보지 않는다
    assert not _check(_said(300, 360), [(_s(300), _s(301))], tracks=[1, 2, 3]).blocked


# ── 경고 ──────────────────────────────────────────────────────────────


def test_more_than_asked_warns_with_the_count():
    g = _check(_said(175, 185, "around", count_hint=1), [(_s(176), _s(177)), (_s(179), _s(180)), (_s(182), _s(183))])
    assert not g.blocked and g.codes() == ["more_than_asked"] and g.warnings[0].data == {"n": 3, "hint": 1}
    assert _check(_said(175, 185, "around", count_hint=3), [(_s(176), _s(177))] * 3).codes() == []


@pytest.mark.parametrize("said_whole,count,warn", [(False, 2, True), (True, 2, False), (False, 0, False)])
def test_whole_timeline_warns_unless_said(said_whole, count, warn):
    req = {"range": None, "range_src": "none", "said_whole": said_whole, "tracks": None, "count_hint": None}
    g = _check(req, [(_s(1), _s(2))] * count, whole=True)
    assert (g.codes() == ["whole"]) is warn
    if warn:
        assert g.warnings[0].data["length_s"] == 600.0 and not g.blocked


@pytest.mark.parametrize("lo,hi,src,warn", [
    (0, 400, "said", True), (0, 290, "said", False), (100, 500, "relative", True), (0, 400, "around", False),
])
def test_range_over_half_the_timeline_warns(lo, hi, src, warn):
    g = _check(_said(lo, hi, src), [(_s(lo + 1), _s(lo + 2))])
    assert ("half" in g.codes()) is warn
    if warn:
        assert g.warnings[0].data["percent"] == int(round((hi - lo) / 600 * 100))


def test_leftover_warns_but_does_not_block():
    g = _check(_said(200, 200.1, "point"), [(_s(200), _s(200) + 1)], leftovers=["5분에 파란 뭐시기", "초록도"])
    assert not g.blocked and g.codes() == ["leftover"]
    assert g.warnings[0].data == {"text": "5분에 파란 뭐시기 · 초록도", "parts": ["5분에 파란 뭐시기", "초록도"]}
    assert g.to_list()[0]["code"] == "leftover" and g.to_list()[0]["level"] == "warn"


def test_requested_dict_from_the_brain_scope():
    s = RequestedScope(range=(MIN5, MIN6), range_src="said", tracks=[2], count_hint=1)
    assert G.requested_dict(s) == {"range": [MIN5, MIN6], "range_src": "said", "said_whole": False, "tracks": [2],
                                   "count_hint": 1}
    assert G.requested_dict(None) is None


# ── 제안과 견주기 (check_proposal) ─────────────────────────────────────


def _info():
    return TimelineInfo.from_result(timeline_info(end_frame=END))


def _items(*secs, color="Red", src=None):
    return [{"at": _s(a), "end": _s(a) + 1, "color": color, "name": "표시", "note": "",
             "src": dict(src or {"at": SAID, "color": SAID, "name": DEFAULT})} for a in secs]


def test_mark_card_inside_its_request_is_clean_and_moving_it_out_is_blocked():
    items = _items(200.0)
    req = {"range": [_s(200.0), _s(200.0) + 1], "range_src": "point", "said_whole": False, "tracks": None,
           "count_hint": 1}
    p = mark_proposal(items, _info(), requested=req, request="3분 20초에 빨간 표시")
    assert p.kind == "mark" and p.fingerprint == "" and p.from_chat
    assert G.check_proposal(p).codes() == []
    # 카드에서 [+]로 옮기면 부탁 범위도 고친 자리를 따른다 (사용자가 정한 값)
    moved = moved_mark(p, 0, 6)
    assert moved.id == p.id and moved.params["items"][0]["src"]["at"] == SAID
    assert moved.requested["range_src"] == "edited" and G.check_proposal(moved).codes() == []
    # 부탁 범위를 그대로 두고 자리만 1초 밖으로 옮긴 제안은 막는다 (자르지 않는 일)
    far = mark_proposal(_items(201.0), _info(), requested=req)
    assert G.check_proposal(far).blocked


def test_find_proposal_without_a_range_warns_whole_and_with_a_range_does_not():
    from engine.edits.proposal import build_specs

    rows = [MarkerRow(_s(301.0), _s(303.0), 2.0, "쉼", "")]
    specs = build_specs("P1", rows, TL0, "Blue", point=False, note_for=lambda r: "")
    base = dict(id="P1", kind="mark_pauses", slot=None, origin="chat:rule", request="쉬는 곳", params={"min_s": 1.5},
                rows=rows, specs=specs, timeline={}, fps=float(FPS), tl_start=TL0, tl_end=END, fingerprint="f")
    p = Proposal(**base, requested={"range": None, "range_src": "none", "said_whole": False, "tracks": None,
                                    "count_hint": None})
    assert G.check_proposal(p).codes() == ["whole"]
    p = Proposal(**base, scope={"kind": "range", "lo": MIN5, "hi": MIN6, "src": "said"},
                 requested=_said(300, 360))
    assert G.check_proposal(p).codes() == []
    p = Proposal(**base, requested=_said(300, 360), leftovers=["5분에 파란 뭐시기"])
    assert G.check_proposal(p).codes() == ["leftover"]


# ── 출처 (카드의 줄마다) ──────────────────────────────────────────────


def test_every_mark_row_keeps_its_own_provenance():
    items = _items(60.0, color="Blue", src={"at": SAID, "color": SAID, "name": DEFAULT}) + \
        _items(120.0, color="Green", src={"at": SAID, "color": DEFAULT, "name": DEFAULT})
    p = mark_proposal(items, _info(), provenance={"at": SAID, "color": "mixed", "name": DEFAULT})
    assert [i["src"]["color"] for i in p.params["items"]] == [SAID, DEFAULT]
    assert p.provenance == {"at": SAID, "color": "mixed", "name": DEFAULT}
    again = remade_mark(p, p.params["items"])
    assert [i["src"] for i in again.params["items"]] == [i["src"] for i in p.params["items"]]
    assert again.provenance == p.provenance


def test_mark_proposal_tags_every_marker_and_keeps_it_inside_the_timeline():
    items = _items(60.0) + [{"at": END + 500, "end": END + 900, "color": "Chartreuse", "name": "x" * 90, "note": "메모",
                             "src": {}}]
    p = mark_proposal(items, _info(), pid="P20260101-000000-aaaa")
    assert [s.custom for s in p.specs] == ["aih:P20260101-000000-aaaa:1", "aih:P20260101-000000-aaaa:2"]
    last = p.specs[1]
    assert last.frame == END - 1 - TL0 and last.dur == 1 and last.color == "Green" and len(last.name) <= 40
    assert p.specs[0].frame == 60 * FPS and p.specs[0].dur == 1
    rng = mark_proposal([{"at": _s(10), "end": _s(20), "color": "Red", "name": "구간"}], _info())
    assert rng.specs[0].dur == 10 * FPS and rng.rows[0].end - rng.rows[0].start == 10 * FPS
    pt = mark_proposal([{"at": _s(10), "end": _s(20), "color": "Red", "name": "구간"}], _info(), point_only=True)
    assert pt.specs[0].dur == 1
