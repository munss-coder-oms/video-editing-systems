"""원본 시각 → 타임라인 프레임 (설계 B2.4 3~6, B11 test_timeline_map).

자르지 않은 클립, 컷으로 나뉜 클립, 사이가 빈 클립, 한 파일에서 나온 두 클립, 클립 fps ≠ 타임라인 fps,
속도를 바꾼 클립 거절, left_offset 교차 확인, 조각 잇기·여유·요청 범위 자르기.
"""

from __future__ import annotations

from fractions import Fraction

import pytest

from engine.loudness import Region
from engine.resolve_link.ops import Item
from engine.timeline.map import (Piece, clip_pieces, exact_fps, item_windows, map_regions, merge_pieces,
                                 pad_pieces)
from engine.timeline.snapshot import TimelineSnapshot, fingerprint
from engine.resolve_link.ops import TimelineInfo
from tests.fakes import audio_item, timeline_info

TL0 = 216000  # 60fps 타임라인의 01:00:00:00


def item(uid="a", track=1, start=TL0, length=600, src=0, clip_fps="60", speed=1.0, left=None, path="C:/rec.mp4",
         enabled=True):
    return Item.from_row(audio_item(uid, track, start, length, path, src=src, clip_fps=clip_fps, speed=speed,
                                    left=left, enabled=enabled))


def test_exact_fps_uses_ntsc_fractions():
    assert exact_fps("29.97") == float(Fraction(30000, 1001))
    assert exact_fps("59.94 DF") == float(Fraction(60000, 1001))
    assert exact_fps("23.976 NDF") == float(Fraction(24000, 1001))
    assert exact_fps("60") == 60.0 and exact_fps(25) == 25.0
    assert exact_fps(None) is None and exact_fps("x") is None and exact_fps("0") is None and exact_fps(True) is None


def test_uncut_item_maps_seconds_to_frames():
    check = item_windows([item(length=6000)], 60.0)
    (w,) = check.windows
    assert (w.src_in, w.src_out) == (0.0, 100.0)
    (p,) = map_regions([Region(10.0, 12.5, -60.0)], check.windows)
    assert (p.start, p.end) == (TL0 + 600, TL0 + 750)
    assert p.start_edge and p.end_edge


def test_cut_splits_a_region_into_pieces_and_merge_joins_touching_ones():
    # 원본 0~10초가 A, 원본 10~20초가 B로 이어 붙어 있음 (자리 그대로), 그다음 원본 30~40초
    a = item("a", start=TL0, length=600, src=0)
    b = item("b", start=TL0 + 600, length=600, src=600)
    c = item("c", start=TL0 + 1200, length=600, src=1800)
    check = item_windows([a, b, c], 60.0)
    pieces = map_regions([Region(9.0, 11.0, -60.0), Region(19.5, 31.0, -55.0)], check.windows)
    assert [(p.start, p.end, p.start_edge, p.end_edge) for p in pieces] == [
        (TL0 + 540, TL0 + 600, True, False), (TL0 + 600, TL0 + 660, False, True),
        (TL0 + 1170, TL0 + 1200, True, False), (TL0 + 1200, TL0 + 1260, False, True),
    ]
    merged = merge_pieces(pieces, 1)
    # 첫 쉼은 컷을 넘어 한 조각, 두 번째는 원본에서 떨어진 곳이 붙은 것이라 한 조각이지만 레벨은 큰 값
    assert [(p.start, p.end) for p in merged] == [(TL0 + 540, TL0 + 660), (TL0 + 1170, TL0 + 1260)]
    assert merged[1].level == -55.0 and merged[1].start_edge and merged[1].end_edge


def test_gap_between_items_is_not_covered():
    a = item("a", start=TL0, length=600, src=0)
    b = item("b", start=TL0 + 900, length=600, src=900)  # 5초 비고 원본도 5초 건너뜀
    pieces = map_regions([Region(8.0, 17.0, -60.0)], item_windows([a, b], 60.0).windows)
    assert [(p.start, p.end) for p in pieces] == [(TL0 + 480, TL0 + 600), (TL0 + 900, TL0 + 1020)]
    assert len(merge_pieces(pieces, 1)) == 2


def test_two_items_from_one_source_both_map():
    a = item("a", start=TL0, length=600, src=0)
    b = item("b", start=TL0 + 3000, length=600, src=0)  # 같은 원본 구간을 한 번 더
    pieces = map_regions([Region(5.0, 6.0, -60.0)], item_windows([a, b], 60.0).windows)
    assert [p.start for p in pieces] == [TL0 + 300, TL0 + 3300]


def test_clip_fps_differs_from_timeline_fps():
    # 30fps 원본을 60fps 타임라인에: source_start는 원본 프레임(30fps)으로 온다
    it = item(start=TL0, length=1200, src=300, clip_fps="30", left=600)
    it.source_end = 300 + 600  # 20초 = 원본 600프레임
    check = item_windows([it], 60.0)
    (w,) = check.windows
    assert w.src_in == pytest.approx(10.0) and check.offset_mismatch == 0
    assert w.src_out == pytest.approx(30.0)
    (p,) = map_regions([Region(12.0, 13.0, -60.0)], check.windows)
    assert (p.start, p.end) == (TL0 + 120, TL0 + 180)


def test_ntsc_timeline_rounds_to_frames():
    fps = exact_fps("29.97")
    it = item(start=0, length=30000, src=0, clip_fps="29.97")
    (p,) = map_regions([Region(100.1, 101.1, -60.0)], item_windows([it], fps).windows)
    assert p.start == round(100.1 * fps) and p.end == round(101.1 * fps)


def test_speed_changed_items_are_refused_and_unreadable_ones_listed():
    fast = item("f", speed=2.0)
    ok = item("o", start=TL0 + 600)
    broken = Item.from_row({"uid": "x", "track": 1, "kind": "audio", "start": None, "end": None})
    check = item_windows([fast, ok, broken], 60.0)
    assert [w.item.uid for w in check.windows] == ["o"]
    assert [i.uid for i in check.refused_speed] == ["f"] and [i.uid for i in check.unmappable] == ["x"]
    # 두 프레임까지는 같은 속도로 본다
    near = item("n", length=600)
    near.source_end = near.source_start + 602
    assert item_windows([near], 60.0).windows


def test_left_offset_cross_check_warns_and_uses_source_start():
    it = item(src=600, left=660)  # 1초 차이
    check = item_windows([it], 60.0)
    assert check.offset_mismatch == 1 and check.windows[0].src_in == 10.0
    only_left = item(src=0, left=120)
    only_left.source_start = None
    only_left.source_end = None
    check = item_windows([only_left], 60.0)
    assert check.windows[0].src_in == 2.0 and check.speed_unchecked == 1


def test_pad_shrinks_inward_and_keeps_raw_bounds():
    (p,) = pad_pieces([Piece(100, 200, -60.0)], 12)
    assert (p.start, p.end, p.raw_start, p.raw_end, p.raw_frames) == (112, 188, 100, 200, 100)
    assert pad_pieces([Piece(100, 120, -60.0)], 10) == []


def test_clip_to_requested_range_trims_edges():
    pieces = [Piece(90, 130, -60.0), Piece(150, 170, -60.0), Piece(190, 260, -60.0)]
    got = clip_pieces(pieces, 100, 200)
    assert [(p.start, p.end, p.start_edge, p.end_edge) for p in got] == [
        (100, 130, False, True), (150, 170, True, True), (190, 200, True, False)]
    assert got[0].raw_start == 100 and got[2].raw_end == 200
    assert clip_pieces([Piece(10, 20, -60.0)], 30, 40) == []
    assert len(clip_pieces(pieces, None, None)) == 3


def test_snapshot_fingerprint_and_enabled_tracks():
    info = timeline_info(tracks={"video": [], "subtitle": [], "audio": [
        {"index": 1, "name": "A1", "enabled": True}, {"index": 2, "name": "A2", "enabled": False}]})
    rows = [audio_item("a", 1, TL0, 600, "C:/r.mp4"), audio_item("b", 2, TL0, 600, "C:/r.mp4")]
    items = [Item.from_row(r) for r in rows]
    snap = TimelineSnapshot(TimelineInfo.from_result(info), items)
    assert snap.enabled_tracks("audio") == {1}
    assert snap.fps == 60.0 and snap.start == TL0
    rec = snap.timeline_record()
    assert rec["key"] == snap.key and rec["drop_frame"] is False and rec["start_tc"] == "01:00:00:00"
    assert fingerprint(items) == fingerprint(list(reversed(items)))  # 순서와 무관
    moved = [Item.from_row(dict(rows[0], start=TL0 + 1)), items[1]]
    assert fingerprint(moved) != fingerprint(items)
