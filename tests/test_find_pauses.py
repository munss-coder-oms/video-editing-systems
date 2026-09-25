"""쉬는 곳 찾기 (설계 B2.4 2, B11 test_find_pauses).

- 0.4초 음량 M(t)는 [t−0.4, t]의 소리다. 조용한 M이 t_a..t_b로 이어지면 쉰 곳은 [t_a−0.45, t_b+0.05].
- min_s는 여유를 두기 전 길이로 재고, 돌려주는 구간은 앞뒤를 pad_s씩 줄인 것.
- 쉰 것으로 보는 크기: 평소 말소리보다 below_lu 작거나 −50 LUFS보다 작은 곳.
"""

from __future__ import annotations

import pytest

from engine.loudness import (PAUSE_LEAD, PAUSE_TAIL, Region, analyze, find_pauses, find_spikes, pad_region,
                             pause_threshold, quiet_runs)
from tests.conftest import requires_ffmpeg


def _series(quiet, end: float = 12.0, loud: float = -20.0, low: float = -60.0):
    """0.1초마다 M. quiet = [(a, b)] 실제로 조용한 구간 → M은 [a+0.4, b]에서 작다."""
    out = []
    for k in range(1, int(round(end * 10)) + 1):
        t = round(k * 0.1, 3)
        q = any(a + 0.4 - 1e-9 <= t <= b + 1e-9 for a, b in quiet)
        out.append((t, low if q else loud))
    return out


def test_pause_threshold_uses_typical_or_absolute_floor():
    assert pause_threshold(-20.0, 25.0) == -45.0
    assert pause_threshold(-30.0, 25.0) == -50.0  # 말소리가 작아도 −50보다 작으면 늘 쉼
    assert pause_threshold(-10.0, 15.0, -60.0) == -25.0


def test_quiet_run_bounds_follow_the_momentary_window():
    frames = _series([(3.0, 5.0)])
    (r,) = quiet_runs(frames, -20.0, 25.0)
    assert r.start == pytest.approx(3.0 - (PAUSE_LEAD - 0.4), abs=1e-6)
    assert r.end == pytest.approx(5.0 + PAUSE_TAIL, abs=1e-6)
    assert r.level == -60.0


def test_find_pauses_min_length_is_measured_before_padding():
    frames = _series([(1.0, 2.2), (4.0, 5.6), (8.0, 11.0)])
    got = find_pauses(frames, -20.0, min_s=1.5, pad_s=0.2)
    # 1.2초 쉼은 빠지고, 1.6초 쉼은 (여유를 두면 1.2초가 되어도) 남는다
    assert [(round(r.start, 2), round(r.end, 2)) for r in got] == [(4.15, 5.45), (8.15, 10.85)]
    assert find_pauses(frames, -20.0, min_s=1.0, pad_s=0.0)[0].start == pytest.approx(0.95)


def test_quiet_frames_before_the_first_full_window_are_ignored():
    frames = [(0.1, -70.0), (0.2, -70.0), (0.3, -70.0)] + _series([], end=3.0)[3:]
    assert quiet_runs(frames, -20.0) == []


def test_loud_room_is_not_a_pause_but_digital_silence_always_is():
    # 말소리 −20, 방 소음 −42: 25dB 아래(−45)에 들지 않으니 쉼이 아니다
    frames = _series([(2.0, 5.0)], low=-42.0)
    assert find_pauses(frames, -20.0, below_lu=25.0) == []
    assert len(find_pauses(frames, -20.0, below_lu=15.0)) == 1
    # 말소리가 아주 작아도(−40) −50보다 작은 곳은 쉼
    frames = _series([(2.0, 5.0)], loud=-40.0, low=-55.0)
    assert len(find_pauses(frames, -40.0, below_lu=25.0)) == 1


def test_pad_region_drops_what_is_left_empty():
    assert pad_region(Region(1.0, 1.3, -60.0), 0.2) is None
    r = pad_region(Region(1.0, 3.0, -60.0), 0.2, pad_end=False)
    assert (r.start, r.end) == (1.2, 3.0)


def test_find_spikes_merges_within_merge_s():
    frames = [(round(k * 0.1, 3), -10.0 if k in (50, 51, 57, 90) else -20.0) for k in range(1, 120)]
    got = find_spikes(frames, -20.0, above_lu=8.0, merge_s=1.0)
    assert len(got) == 2
    got = find_spikes(frames, -20.0, above_lu=8.0, merge_s=0.3)
    assert len(got) == 3


@requires_ffmpeg
def test_tone_with_gaps_gives_exact_intervals(gap_tone):
    """1kHz 소리 사이의 0.8/1.6/3.0초 조용한 곳 (2~2.8, 5~6.6, 9~12초): 여유 0.2초를 둔 뒤 ±0.1초."""
    report = analyze(str(gap_tone), keep_frames=True)
    frames = report.momentary
    assert frames and abs(frames[1][0] - frames[0][0] - 0.1) < 1e-6
    got = find_pauses(frames, report.typical, min_s=0.5, pad_s=0.2)
    want = [(2.2, 2.6), (5.2, 6.4), (9.2, 11.8)]
    assert len(got) == 3
    for r, (a, b) in zip(got, want):
        assert r.start == pytest.approx(a, abs=0.1) and r.end == pytest.approx(b, abs=0.1)
    # 1.5초가 넘는 쉼만: 0.8초는 빠진다
    got = find_pauses(frames, report.typical, min_s=1.5, pad_s=0.2)
    assert [round(r.start) for r in got] == [5, 9]
    assert report.to_dict().get("momentary") is None  # 결과 파일에는 긴 목록을 넣지 않는다
