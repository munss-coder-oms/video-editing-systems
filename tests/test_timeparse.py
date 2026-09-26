"""한국어 시간 말 → 타임라인 프레임 (설계 B4.2 "Time grammar", B11 test_timeparse).

표 하나에 말 → 뜻을 적어 두고 모두 돌린다.
- 60fps 타임라인: 시작 01:00:00:00 = 216000 프레임, 길이 10분, 재생 위치 0:10.
- 2시간짜리 60fps 타임라인: "1:03:20"이 지난 시간(1시간 3분 20초)으로도, 리졸브 시간(3분 20초)으로도 안쪽 → 되묻기.
- 29.97 드롭 프레임: 시작 01:00:00;00 = 107892 프레임. 3분 20초 = 5994 프레임 (00:03:20;00과 같은 자리).
"""

from __future__ import annotations

import pytest

from engine.chat.timeparse import (Around, Clock, Edge, Hms3, InOut, Playhead, RangeExpr, Relative, TC4, TimeContext,
                                   TimeError, Whole, context_from_info, find_times, resolve_point, resolve_range,
                                   scan)
from tests.fakes import timeline_info

TL0 = 216000
F2997 = 30000 / 1001
DF0 = 107892  # 01:00:00;00 (29.97 드롭 프레임)

C60 = TimeContext(fps=60.0, start=TL0, end=TL0 + 600 * 60, fps_text="60", start_tc="01:00:00:00",
                  playhead=TL0 + 600, name="Timeline 1")
C60_LONG = TimeContext(fps=60.0, start=TL0, end=TL0 + 2 * 3600 * 60, fps_text="60", start_tc="01:00:00:00",
                       playhead=None)
C2997 = TimeContext(fps=F2997, start=DF0, end=DF0 + int(round(1200 * F2997)), fps_text="29.97", drop=True,
                    start_tc="01:00:00;00", playhead=DF0 + 5994)
CONTEXTS = {"60": C60, "60long": C60_LONG, "2997": C2997}


def _one(text: str):
    toks = scan(text)
    assert len(toks) == 1, (text, toks)
    return toks[0]


def _read(text: str, ctx: TimeContext):
    """점이면 ("P", 지난 초, 어떻게), 구간이면 ("R", 시작 초, 끝 초, 출처), 못 풀면 ("E", 까닭)."""
    t = _one(text)
    try:
        if t.is_range or isinstance(t.expr, Around):
            r = resolve_range(t.expr, ctx, find=True)
            return ("R", round(ctx.seconds_of(r.lo), 3), round(ctx.seconds_of(r.hi), 3), r.src)
        p = resolve_point(t.expr, ctx)
        return ("P", round(ctx.seconds_of(p.frame), 3), p.how)
    except TimeError as exc:
        return ("E", exc.code)


# ── 말 → 뜻 (60fps, 10분, 재생 위치 0:10) ─────────────────────────────

CASES_60 = [
    # 지난 시간
    ("3분 20초", ("P", 200.0, "elapsed")),
    ("3분20초", ("P", 200.0, "elapsed")),
    ("3:20", ("P", 200.0, "elapsed")),
    ("200초", ("P", 200.0, "elapsed")),
    ("3분 반", ("P", 210.0, "elapsed")),
    ("3분 20", ("P", 200.0, "elapsed")),
    ("1분 30.5초", ("P", 90.5, "elapsed")),
    ("0.5초", ("P", 0.5, "elapsed")),
    ("2분", ("P", 120.0, "elapsed")),
    ("00:03:20", ("P", 200.0, "elapsed")),
    ("0:03:20", ("P", 200.0, "elapsed")),
    ("９분", ("P", 540.0, "elapsed")),  # 전각 숫자는 정리하고 찾는다 (normalize는 두뇌가 하지만 표에서도 확인)
    # 리졸브 타임코드
    ("01:03:20:00", ("P", 200.0, "tc")),
    ("01:00:00:30", ("P", 0.5, "tc")),
    ("01:09:59:59", ("P", 599.983, "tc")),
    # 세 칸: 지난 시간이면 밖, 리졸브 시간으로 보면 안
    ("1:03:20", ("P", 200.0, "tc")),
    # 재생 위치
    ("여기", ("P", 10.0, "playhead")),
    ("지금", ("P", 10.0, "playhead")),
    ("재생 위치", ("P", 10.0, "playhead")),
    ("현재 위치", ("P", 10.0, "playhead")),
    # 기준점에서 몇 초
    ("여기서부터 10초", ("R", 10.0, 20.0, "relative")),
    ("여기 앞뒤 2초", ("R", 8.0, 12.0, "relative")),
    ("처음부터 5분", ("R", 0.0, 300.0, "relative")),
    ("처음 5분", ("R", 0.0, 300.0, "relative")),
    ("끝에서 30초", ("R", 570.0, 600.0, "relative")),
    ("마지막 30초", ("R", 570.0, 600.0, "relative")),
    ("처음부터 3분 20초까지", ("R", 0.0, 200.0, "relative")),
    # 구간
    ("5분부터 6분까지", ("R", 300.0, 360.0, "said")),
    ("5분~6분", ("R", 300.0, 360.0, "said")),
    ("5~6분", ("R", 300.0, 360.0, "said")),
    ("5분-6분", ("R", 300.0, 360.0, "said")),
    ("5분 - 6분", ("R", 300.0, 360.0, "said")),
    ("5분에서 6분", ("R", 300.0, 360.0, "said")),
    ("5분과 6분 사이", ("R", 300.0, 360.0, "said")),
    ("5분하고 6분 사이", ("R", 300.0, 360.0, "said")),
    ("5분〜6분", ("R", 300.0, 360.0, "said")),
    ("3분 20초부터 3분 25초", ("R", 200.0, 205.0, "said")),
    ("3분 5초~10초", ("R", 185.0, 190.0, "said")),  # 뒤의 10초는 앞의 3분을 이어받는다
    # 앞의 단위 없는 수는 뒤의 맨 앞(가장 큰) 단위를 빌린다: 5분~6분 30초 (5초~6분 30초가 아니다)
    ("5~6분 30초", ("R", 300.0, 390.0, "said")),
    ("1~2분 30초", ("R", 60.0, 150.0, "said")),
    ("5~6:30", ("R", 300.0, 390.0, "said")),
    ("15~25초", ("R", 15.0, 25.0, "said")),
    ("1:00~1:30", ("R", 60.0, 90.0, "said")),
    ("3분 20초부터 끝까지", ("R", 200.0, 600.0, "said")),
    ("01:05:00:00~01:06:00:00", ("R", 300.0, 360.0, "said")),
    # 끝이 타임라인 밖이면 끝에서 자른다
    ("9분~11분", ("R", 540.0, 600.0, "said")),
    # 쯤·근처: 찾는 말에는 앞뒤 5초
    ("3분쯤", ("R", 175.0, 185.0, "around")),
    ("3분 근처", ("R", 175.0, 185.0, "around")),
    ("3분 20초 즈음", ("R", 195.0, 205.0, "around")),
    ("2초쯤", ("R", 0.0, 7.0, "around")),  # 시작보다 앞은 자른다
    # 전체
    ("전체", ("R", 0.0, 600.0, "whole")),
    ("처음부터 끝까지", ("R", 0.0, 600.0, "whole")),
    # 못 푸는 것
    ("11분", ("E", "outside")),
    ("1시간", ("E", "outside")),
    ("01:20:00:00", ("E", "outside")),
    ("00:59:00:00", ("E", "outside")),
    ("6분~5분", ("E", "reversed")),
    ("In~Out", ("E", "in_out")),
    ("인아웃", ("E", "in_out")),
    ("표시한 구간", ("E", "in_out")),
]


@pytest.mark.parametrize("text,want", CASES_60, ids=[c[0] for c in CASES_60])
def test_time_words_on_a_60fps_timeline(text, want):
    from engine.chat.intents_ko import normalize

    assert _read(normalize(text), C60) == want


# ── 리졸브 시간과 지난 시간이 둘 다 안쪽이면 되묻는다 (2시간 타임라인) ──────

CASES_LONG = [
    ("1:03:20", ("E", "ambiguous")),
    ("1:00:00", ("E", "ambiguous")),
    ("01:03:20:00", ("P", 200.0, "tc")),  # 네 칸은 늘 리졸브 시간
    ("0:03:20", ("P", 200.0, "elapsed")),  # 0시는 리졸브 시간이면 타임라인 앞이라 지난 시간
    ("1:03:20.5", ("P", 3800.5, "elapsed")),  # 소수 초는 타임코드가 아니다
    ("1시간 3분 20초", ("P", 3800.0, "elapsed")),
    ("여기", ("E", "no_playhead")),
]


@pytest.mark.parametrize("text,want", CASES_LONG, ids=[c[0] for c in CASES_LONG])
def test_resolve_time_or_elapsed_on_a_two_hour_timeline(text, want):
    assert _read(text, C60_LONG) == want


def test_ambiguous_time_carries_both_readings():
    t = _one("1:03:20")
    with pytest.raises(TimeError) as e:
        resolve_point(t.expr, C60_LONG)
    d = e.value.detail
    assert d["raw"] == "1:03:20" and d["elapsed_s"] == 3800.0 and d["tc"] == "01:03:20:00" and d["tc_s"] == 200.0


def test_three_part_time_read_as_timecode_leaves_a_card_note():
    p = resolve_point(_one("1:03:20").expr, C60)
    assert p.how == "tc" and p.note == ("tc_read", {"tc": "01:03:20:00", "seconds": 200.0})
    assert p.frame == TL0 + 200 * 60


# ── 29.97 드롭 프레임 ─────────────────────────────────────────────────

CASES_2997 = [
    ("3분 20초", ("P", 200.0, "elapsed"), 5994),
    ("01:03:20;00", ("P", 200.0, "tc"), 5994),
    ("01:03:20:00", ("P", 200.0, "tc"), 5994),  # ':'로 적어도 타임라인이 드롭 프레임이면 그렇게 센다
    ("01:01:00;02", ("P", 60.06, "tc"), 1800),  # 1분에서 ;00 ;01을 건너뛴다
    ("01:10:00;00", ("P", 599.999, "tc"), 17982),  # 10분마다는 건너뛰지 않는다
    ("10분", ("P", 599.999, "elapsed"), 17982),
    ("여기", ("P", 200.0, "playhead"), 5994),
    ("00:00:01;00", ("E", "outside"), None),
]


@pytest.mark.parametrize("text,want,frames", CASES_2997, ids=[c[0] for c in CASES_2997])
def test_drop_frame_2997(text, want, frames):
    assert _read(text, C2997) == want
    if frames is not None:
        assert resolve_point(_one(text).expr, C2997).frame - DF0 == frames


def test_drop_frame_range_counts_real_seconds():
    """지난 시간은 실제 초 × 29.97: 5분 = 8991 프레임. 드롭 프레임 타임코드는 10분마다만 실제 시간과 딱 맞으므로
    5분 자리는 리졸브 시간으로 01:04:59;29 (01:05:00;00은 없는 타임코드, 다음 프레임이 01:05:00;02)."""
    r = resolve_range(_one("5분~6분").expr, C2997)
    assert (r.lo - DF0, r.hi - DF0) == (8991, 10789)
    assert C2997.tc_of(r.lo) == "01:04:59;29" and C2997.tc_of(r.lo + 1) == "01:05:00;02"


# ── 찾기 (자리와 식) ──────────────────────────────────────────────────


def test_spans_point_at_the_words_in_the_text():
    text = "3분 20초에 빨간 표시하고 5분~6분 쉬는 곳"
    toks = scan(text)
    assert [t.text for t in toks] == ["3분 20초", "5분~6분"]
    assert [text[a:b] for a, b in (t.span for t in toks)] == ["3분 20초", "5분~6분"]
    assert isinstance(toks[0].expr, Clock) and isinstance(toks[1].expr, RangeExpr)


@pytest.mark.parametrize("text,kind", [
    ("1:03:20", Hms3), ("01:03:20:00", TC4), ("여기", Playhead), ("여기서부터 10초", Relative),
    ("3분쯤", Around), ("In~Out", InOut), ("전체", Whole), ("5분~6분", RangeExpr), ("200초", Clock),
])
def test_expression_kinds(text, kind):
    assert isinstance(_one(text).expr, kind)


def test_masked_spans_are_not_times():
    text = "2초 넘게 쉰 곳"
    assert scan(text) != []
    assert scan(text, masked=[(0, 6)]) == []


@pytest.mark.parametrize("text,want", [
    ("3분 20 표시해줘", ["3분 20"]),  # 초를 생략한 20 뒤에 빈칸과 다른 말
    ("3분 20 빨간 표시", ["3분 20"]),
    ("3분 20에 표시", ["3분 20"]),
    ("3분 3개 표시", ["3분"]),  # 개수·단위가 붙으면 초가 아니다
    ("3분 20 개", ["3분"]),
    ("3분 5 dB", ["3분"]),
    ("1시간 20 표시", ["1시간"]),  # 생략한 초는 분 뒤에서만
])
def test_bare_seconds_after_minutes(text, want):
    assert [t.text for t in scan(text)] == want


def test_numbers_without_units_are_not_times():
    assert find_times("쉬는 곳 3개만") == [] and find_times("6dB 줄여줘", [(0, 3)]) == []


def test_edges_resolve_to_start_and_end():
    assert resolve_point(Edge("start"), C60).frame == TL0
    assert resolve_point(Edge("end"), C60).frame == C60.end


def test_no_playhead_is_an_error_not_frame_zero():
    ctx = TimeContext(fps=60.0, start=TL0, end=TL0 + 3600, playhead=None)
    with pytest.raises(TimeError) as e:
        resolve_point(Playhead("여기"), ctx)
    assert e.value.code == "no_playhead"


def test_context_from_timeline_info_reads_the_playhead_from_current_tc():
    ctx = context_from_info(timeline_info(current_tc="01:03:20:00"))
    assert ctx.start == TL0 and ctx.fps == 60.0 and ctx.playhead == TL0 + 200 * 60 and ctx.name == "Timeline 1"
    assert ctx.tc_of(TL0 + 600) == "01:00:10:00"
    df = context_from_info(timeline_info(fps="29.97", drop_frame=True, start_frame=DF0, end_frame=DF0 + 36000,
                                         start_tc="01:00:00;00", current_tc="01:03:20;00"))
    assert df.drop is True and df.playhead == DF0 + 5994 and df.fps == pytest.approx(F2997)
    assert context_from_info(timeline_info(fps=None)) is None
    assert context_from_info({"start_frame": 0, "end_frame": 0, "fps": "30"}) is None


def test_table_is_big_enough():
    """설계 B11: 60개 이상."""
    count = len(CASES_60) + len(CASES_LONG) + len(CASES_2997)
    assert count >= 60, count
