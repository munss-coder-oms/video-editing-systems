"""자동화 버튼 계산 (설계 B2.4, B2.5 1~3, B11 test_automation_plan).

가짜 리졸브(FakeResolve)에 OBS 녹화 흉내(obs_video) 한 개를 A1..A4로 올려 두고 plan_slot을 돌린다.
- 처음 보는 녹화 모양이면 목소리를 묻고(전체 소리 = 소리 0, 목소리와 함께 켜져 있으면 두 번 들림),
  고르면 목소리의 쉬는 곳에 표시 제안을 만든다. 기억한 목소리는 묻지 않고, 소리 모양이 바뀌면 다시 묻는다.
- 요청 범위(In~Out)로 자르고, 범위에 걸쳐 잘린 조각이 min_s보다 짧으면 뺀다 (B11의 5:00~6:00 예).
- 계산 중에는 리졸브에 바꾸는 요청을 하나도 보내지 않는다.
"""

from __future__ import annotations

import threading

import pytest

from engine import ffmpeg
from engine.analysis_cache import AnalysisCache, StreamAnalysis
from engine.automation import kinds
from engine.automation.plan import (PlanEnv, PlanRefused, SlotRequest, VoiceMemory, VoiceOverride, VoiceQuestion,
                                    _pause_rows, plan_slot)
from engine.edits.proposal import Proposal
from engine.probe import probe
from engine.resolve_link.ops import Item, ResolveOps
from engine.timeline.audio_map import layout_signature
from engine.timeline.map import item_windows
from tests.conftest import OBS_PAUSES, OBS_SPIKE, requires_ffmpeg
from tests.fakes import FakeResolve, audio_item, obs_items, timeline_info

TL0 = 108000  # 30fps 타임라인의 01:00:00:00
FPS = 30.0
MUTATING = {"add_markers", "delete_markers", "remove_audio", "probe_copy", "switch_timeline"}


class Caps:
    def __init__(self, **values) -> None:
        self.values = values

    def has(self, cap):
        return self.values.get(cap)


@pytest.fixture(scope="module")
def cache_dir(tmp_path_factory):
    """한 번 잰 스트림은 다시 재지 않도록 이 파일의 시험들이 나눠 쓴다."""
    return tmp_path_factory.mktemp("analysis")


def _resolve(path: str, **info) -> FakeResolve:
    base = dict(start_frame=TL0, end_frame=TL0 + 900, fps="30", start_tc="01:00:00:00")
    base.update(info)
    return FakeResolve(timeline_info(**base), obs_items(path, TL0, 900, clip_fps="30"))


def _env(fake: FakeResolve, cache_dir, **kw) -> PlanEnv:
    return PlanEnv(ops=ResolveOps(fake), cache=AnalysisCache(cache_dir), **kw)


def _req(kind: str = "mark_pauses", **params) -> SlotRequest:
    return SlotRequest(slot=1 if kind == "mark_pauses" else 2, kind=kind, name=kind, params=params)


def _sig(path) -> str:
    return layout_signature(probe(str(path)))


def _secs(rows):
    return [((r.start - TL0) / FPS, (r.end - TL0) / FPS) for r in rows]


def _no_mutations(fake: FakeResolve) -> None:
    assert not (set(fake.requests) & MUTATING) and fake.mutations == []


# ── 종류 표 ────────────────────────────────────────────────────────────

def test_kind_params_normalize_to_their_ranges():
    k = kinds.kind("mark_pauses")
    got = k.normalize({"min_s": 9.0, "pad_s": -1, "below_lu": "x", "as_range": 1, "color": "Gold",
                       "name": "  아주 긴 이름을 넣어 보면 스무 글자에서 잘려야 한다  ", "max": 501, "scope": "all"})
    assert got["min_s"] == 5.0 and got["pad_s"] == 0.0 and got["below_lu"] == 25.0
    assert got["as_range"] is True and got["color"] == "Blue" and got["scope"] == "whole"
    assert len(got["name"]) == 20 and got["max"] == kinds.MAX_MARKERS_PER_PROPOSAL
    assert k.normalize({"min_s": 1.26})["min_s"] == 1.3  # 0.1 걸음
    assert kinds.kind("mark_spikes").normalize({"max": 13})["max"] == 10
    assert kinds.normalize_params("mark_pauses", None) == k.defaults()
    assert kinds.changed_params("mark_pauses", k.defaults(), dict(k.defaults(), min_s=2.0)) == ["min_s"]
    assert kinds.is_ready("mark_pauses") and kinds.is_ready("mark_spikes") and not kinds.is_ready("balance_voice")
    assert kinds.READY_KINDS == ("mark_pauses", "mark_spikes")
    assert kinds.kind("balance_voice").param("scope").requires_cap == "in_out"


def test_not_ready_kind_is_refused_without_asking_resolve(tmp_path):
    fake = FakeResolve()
    with pytest.raises(PlanRefused) as e:
        plan_slot(_req("balance_voice"), _env(fake, tmp_path), VoiceMemory())
    assert e.value.code == "not_ready" and fake.requests == []


# ── 목소리 고르기 → 제안 ────────────────────────────────────────────────

@requires_ffmpeg
def test_first_run_asks_for_the_voice_then_marks_its_pauses(obs_video, cache_dir):
    fake = _resolve(str(obs_video))
    stages = []
    env = _env(fake, cache_dir, progress=lambda s, f, info: stages.append(s))
    q = plan_slot(_req(), env, VoiceMemory())
    assert isinstance(q, VoiceQuestion) and q.reason == "first" and q.previous is None
    assert [s.index for s in q.streams] == [0, 1, 2, 3]
    assert q.mix == 0 and q.doubled is True  # 소리 0(전체)과 소리 2가 모두 켜져 있다
    assert all(s.enabled_on_timeline for s in q.streams)
    assert 0 <= q.streams[1].best_start <= 27.0
    assert q.signature == _sig(obs_video) and q.path == str(obs_video)
    _no_mutations(fake)

    p = plan_slot(_req(), env, VoiceMemory(), override=VoiceOverride(q.signature, 1))
    assert isinstance(p, Proposal) and p.kind == "mark_pauses" and p.slot == 1 and p.origin == "button:1"
    # 1.5초가 넘는 쉼 둘 (0.8초 쉼은 빠진다), 앞뒤로 0.2초 여유
    assert len(p.rows) == 2 and p.count == 2
    for (a, b), (qa, qb) in zip(_secs(p.rows), OBS_PAUSES[1:]):
        assert a == pytest.approx(qa + 0.2, abs=0.2) and b == pytest.approx(qb - 0.2, abs=0.2)
    assert p.rows[0].value == pytest.approx(2.0, abs=0.25)  # 쉰 길이는 여유를 두기 전
    assert p.total_s == pytest.approx(sum(r.value for r in p.rows), abs=1e-3)
    assert [s.frame for s in p.specs] == [r.start - TL0 for r in p.rows]  # AddMarker는 타임라인 시작 기준
    assert [s.custom for s in p.specs] == [f"aih:{p.id}:1", f"aih:{p.id}:2"]
    assert all(s.color == "Blue" and s.dur == r.end - r.start for s, r in zip(p.specs, p.rows))
    assert p.specs[0].name.startswith("쉼 2.") and "AI 도우미" in p.specs[0].note
    assert p.voice.stream == 1 and p.voice.remembered is False and p.voice.stream_count == 4
    assert p.voice.doubled is False  # 아직 전체 소리가 어느 것인지 기억하지 않았다
    assert p.tracks == [2] and p.scope["kind"] == "whole" and p.point_only is False
    assert p.timeline["key"] and p.fingerprint and p.tl_start == TL0 and p.tl_end == TL0 + 900
    assert p.debug["mapping_methods"] == {"ordinal": 4}
    assert stages[0] == 1 and 2 in stages and stages[-1] == 3
    _no_mutations(fake)


@requires_ffmpeg
def test_remembered_voice_is_used_without_asking(obs_video, cache_dir):
    fake = _resolve(str(obs_video))
    env = _env(fake, cache_dir)
    sig = _sig(obs_video)
    first = plan_slot(_req(), env, VoiceMemory(), override=VoiceOverride(sig, 1))
    memory = VoiceMemory(by_layout={sig: {"stream": 1, "mix": 0}}, profiles={sig: first.voice.profile})
    p = plan_slot(_req(), env, memory)
    assert isinstance(p, Proposal) and p.voice.remembered is True
    assert p.voice.mix == 0 and p.voice.doubled is True  # 소리 0과 소리 2가 함께 켜져 있다
    assert _secs(p.rows) == _secs(first.rows)
    # [바꾸기]: 기억이 있어도 다시 묻는다
    q = plan_slot(_req(), env, memory, ask_voice=True)
    assert isinstance(q, VoiceQuestion) and q.reason == "change" and q.previous == 1
    # 전체 소리 트랙(A1)을 끄면 두 번 들리지 않는다
    fake.info["tracks"]["audio"][0]["enabled"] = False
    p = plan_slot(_req(), env, memory)
    assert p.voice.doubled is False
    _no_mutations(fake)


@requires_ffmpeg
def test_changed_sound_shape_asks_again(obs_video, cache_dir):
    """목소리로 게임 소리의 모양을 기억해 두었다면 (다른 판에서 스트림 순서가 바뀐 것처럼) 다시 묻는다."""
    fake = _resolve(str(obs_video))
    env = _env(fake, cache_dir)
    sig = _sig(obs_video)
    game = plan_slot(_req(), env, VoiceMemory(), override=VoiceOverride(sig, 2))
    memory = VoiceMemory(by_layout={sig: {"stream": 1, "mix": 0}}, profiles={sig: game.voice.profile})
    q = plan_slot(_req(), env, memory)
    assert isinstance(q, VoiceQuestion) and q.reason == "changed" and q.previous == 1
    assert q.change_reason in ("typical", "active")
    _no_mutations(fake)


@requires_ffmpeg
def test_mark_spikes_finds_the_loud_moment(obs_video, cache_dir):
    fake = _resolve(str(obs_video))
    sig = _sig(obs_video)
    p = plan_slot(_req("mark_spikes"), _env(fake, cache_dir), VoiceMemory(), override=VoiceOverride(sig, 1))
    assert isinstance(p, Proposal) and p.kind == "mark_spikes" and len(p.rows) >= 1
    a, b = _secs(p.rows)[0]
    assert OBS_SPIKE[0] - 0.6 <= a <= OBS_SPIKE[0] + 0.3 and b <= OBS_SPIKE[1] + 0.6
    assert p.rows[0].value >= 8.0 and p.specs[0].color == "Red"
    assert p.specs[0].name.startswith("튀는 소리 +")
    assert p.total_s == pytest.approx(sum((r.end - r.start) / FPS for r in p.rows), abs=1e-3)
    _no_mutations(fake)


# ── 범위 ───────────────────────────────────────────────────────────────

@requires_ffmpeg
def test_in_out_scope_clips_to_the_requested_range(obs_video, cache_dir):
    fake = _resolve(str(obs_video))
    fake.in_out = {"video": {"in": 330, "out": 644}}  # 11.0초 ~ 21.5초 (Out은 들어가는 프레임)
    sig = _sig(obs_video)
    env = _env(fake, cache_dir, caps=Caps(in_out=True))
    p = plan_slot(_req(min_s=0.5, scope="in_out"), env, VoiceMemory(), override=VoiceOverride(sig, 1))
    assert p.scope == {"kind": "in_out", "lo": TL0 + 330, "hi": TL0 + 645}
    got = _secs(p.rows)
    assert len(got) == 2  # 5초 쉼은 범위 밖
    assert got[0][0] == pytest.approx(11.0, abs=1 / FPS) and got[0][1] == pytest.approx(11.8, abs=0.2)
    assert got[1][0] == pytest.approx(20.2, abs=0.2) and got[1][1] == pytest.approx(21.5, abs=1 / FPS)
    assert "scope" in fake.requests
    # 같은 범위에서 1.5초 넘는 쉼만 찾으면 잘린 두 조각 모두 빠진다
    p = plan_slot(_req(scope="in_out"), env, VoiceMemory(), override=VoiceOverride(sig, 1))
    assert p.rows == []
    # In~Out이 없으면 계산하지 않는다
    fake.in_out = None
    with pytest.raises(PlanRefused) as e:
        plan_slot(_req(scope="in_out"), env, VoiceMemory(), override=VoiceOverride(sig, 1))
    assert e.value.code == "no_in_out"
    _no_mutations(fake)


@requires_ffmpeg
def test_in_out_before_the_check_falls_back_to_whole(obs_video, cache_dir):
    fake = _resolve(str(obs_video))
    fake.in_out = {"video": {"in": 330, "out": 644}}
    sig = _sig(obs_video)
    p = plan_slot(_req(scope="in_out"), _env(fake, cache_dir, caps=Caps()), VoiceMemory(),
                  override=VoiceOverride(sig, 1))
    assert p.scope["kind"] == "whole" and p.params["scope"] == "whole" and p.warnings["in_out_off"] == 1
    assert "scope" not in fake.requests and len(p.rows) == 2


def _minute_analysis(path: str, quiet, end: float = 420.0) -> StreamAnalysis:
    """0.1초마다 M: 말소리 −20, 조용한 [a, b]는 M이 [a+0.4, b]에서 −70."""
    t, m = [], []
    for k in range(1, int(end * 10) + 1):
        x = round(k * 0.1, 3)
        t.append(x)
        m.append(-70.0 if any(a + 0.4 - 1e-9 <= x <= b + 1e-9 for a, b in quiet) else -20.0)
    return StreamAnalysis(path=path, stream=0, duration=end, integrated=-20.0, true_peak=-3.0, lra=2.0,
                          typical=-20.0, noise_floor=-70.0, t=t, m=m, s=list(m))


def test_b11_range_clip_keeps_long_enough_pieces():
    """쉼 4:58~5:03, 5:30~5:33, 5:59~6:04에 범위 5:00~6:00 → 5:00~5:03, 5:30~5:33 두 개, 셋째(1초)는 뺀다."""
    path = "C:/long.wav"
    a = _minute_analysis(path, [(298.0, 303.0), (330.0, 333.0), (359.0, 364.0)])
    it = Item.from_row(audio_item("w", 1, 0, int(420 * FPS), path, clip_fps="30"))
    by_path = {path: item_windows([it], FPS).windows}
    params = kinds.kind("mark_pauses").normalize({"pad_s": 0.0})
    rows, found = _pause_rows(params, {path: a}, by_path, FPS, int(300 * FPS), int(360 * FPS))
    got = [(r.start / FPS, r.end / FPS) for r in rows]
    assert found == 2 and len(got) == 2
    assert got[0][0] == 300.0 and got[0][1] == pytest.approx(303.0, abs=0.1)
    assert got[1][0] == pytest.approx(330.0, abs=0.1) and got[1][1] == pytest.approx(333.0, abs=0.1)
    # 범위 없이는 셋 다
    rows, _ = _pause_rows(params, {path: a}, by_path, FPS, 0, None)
    assert len(rows) == 3
    # 잘린 조각이 min_s 이상이면 남는다: 5:59~6:04에서 범위 끝을 6:01로
    rows, _ = _pause_rows(params, {path: a}, by_path, FPS, int(300 * FPS), int(361 * FPS))
    assert len(rows) == 3 and rows[2].end == int(361 * FPS)


# ── 거절 ───────────────────────────────────────────────────────────────

@pytest.mark.parametrize("change,code", [
    ({"project": None, "timeline": None}, "no_project"),
    ({"timeline": None}, "no_timeline"),
    ({"timeline": "AI 도우미 점검용 C1"}, "probe_copy"),
    ({"fps": None}, "timeline_unreadable"),
])
def test_refused_before_reading_items(tmp_path, change, code):
    fake = FakeResolve(timeline_info(**change), [])
    with pytest.raises(PlanRefused) as e:
        plan_slot(_req(), _env(fake, tmp_path), VoiceMemory())
    assert e.value.code == code and fake.requests == ["timeline_info"]


def test_no_audio_items_on_enabled_tracks(tmp_path):
    info = timeline_info()
    for t in info["tracks"]["audio"]:
        t["enabled"] = t["index"] != 1
    fake = FakeResolve(info, [audio_item("a", 1, 216000, 600, "C:/a.wav"),
                              audio_item("b", 2, 216000, 600, "C:/b.wav", enabled=False)])
    with pytest.raises(PlanRefused) as e:
        plan_slot(_req(), _env(fake, tmp_path), VoiceMemory())
    assert e.value.code == "no_audio_items"


def test_missing_files_are_refused(tmp_path):
    fake = FakeResolve(timeline_info(), [audio_item("a", 1, 216000, 600, "C:/없는/녹화.mp4")])
    with pytest.raises(PlanRefused) as e:
        plan_slot(_req(), _env(fake, tmp_path, exists=lambda p: False), VoiceMemory())
    assert e.value.code == "files_missing" and e.value.detail["paths"] == ["C:/없는/녹화.mp4"]


@requires_ffmpeg
def test_only_items_on_enabled_tracks_and_enabled_clips_count(obs_video, cache_dir):
    fake = _resolve(str(obs_video))
    fake.items[1]["enabled"] = False  # 목소리 클립(A2)을 끔
    sig = _sig(obs_video)
    with pytest.raises(PlanRefused) as e:
        plan_slot(_req(), _env(fake, cache_dir), VoiceMemory(), override=VoiceOverride(sig, 1))
    assert e.value.code == "no_voice_items" and e.value.detail["stream"] == 1


@requires_ffmpeg
def test_unmapped_tracks_are_refused_without_a_voice_picker_loop(obs_video, cache_dir):
    """녹화의 A1(전체 소리) 클립을 지워 순서 규칙이 안 맞고 연결 정보도 없음: 목소리를 골라도 넣을 곳이 없다.
    고르기 카드를 되풀이하지 않고 까닭을 알린다 (검토: no_voice_items 되풀이)."""
    fake = _resolve(str(obs_video))
    fake.items = [it for it in fake.items if it["track"] != 1]
    with pytest.raises(PlanRefused) as e:
        plan_slot(_req(), _env(fake, cache_dir), VoiceMemory())
    assert e.value.code == "unmapped_tracks" and e.value.detail["n"] == 3
    sig = _sig(obs_video)
    for stream in (1, 2, 3):  # 예전에 고른 것이 있어도 같은 까닭
        with pytest.raises(PlanRefused) as e:
            plan_slot(_req(), _env(fake, cache_dir), VoiceMemory(), override=VoiceOverride(sig, stream))
        assert e.value.code == "unmapped_tracks"
    _no_mutations(fake)


@requires_ffmpeg
def test_missing_second_file_is_a_warning(obs_video, cache_dir):
    fake = _resolve(str(obs_video))
    fake.items.append(audio_item("x", 4, TL0 + 600, 300, "C:/없음.wav", clip_fps="30"))
    sig = _sig(obs_video)
    real = str(obs_video)
    p = plan_slot(_req(), _env(fake, cache_dir, exists=lambda p: p == real), VoiceMemory(),
                  override=VoiceOverride(sig, 1))
    assert p.warnings["missing_files"] == 1 and p.debug["missing_files"] == ["C:/없음.wav"]
    assert len(p.rows) == 2


@requires_ffmpeg
def test_helper_test_sounds_and_single_stream_extras_are_not_the_voice(obs_video, gap_audio, cache_dir, tmp_path):
    """옛 시험 트랙("AI 도우미 시험")의 삐 소리, 도우미의 시험·점검 파일은 목소리가 아니다 (트랙 줄에 A5가 끼지 않게).
    목소리를 고른 녹화 옆의 소리 하나짜리 파일(음악 등)도 목소리로 보지 않고 알린다."""
    import shutil

    from engine.automation.plan import helper_own, read_snapshot
    from engine.edits.apply import LEGACY_TEST_TRACK

    tone = tmp_path / "files" / "aih_test_tone.wav"
    probe_wav = tmp_path / "files" / "probe" / "probe_101500.wav"
    music = tmp_path / "음악.wav"
    for target in (tone, probe_wav, music):
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(gap_audio, target)
    fake = _resolve(str(obs_video))
    audio = fake.info["tracks"]["audio"]
    audio.append({"index": 5, "name": LEGACY_TEST_TRACK, "enabled": True, "locked": False, "subtype": "mono",
                  "count": 1})
    audio.append({"index": 6, "name": "Audio 6", "enabled": True, "locked": False, "subtype": "mono", "count": 2})
    audio.append({"index": 7, "name": "Audio 7", "enabled": True, "locked": False, "subtype": "mono", "count": 1})
    fake.items.append(audio_item("t", 5, TL0 + 30, 105, str(music), clip_fps="30"))  # 옛 시험 트랙 위: 무엇이든
    fake.items.append(audio_item("u", 6, TL0 + 30, 105, str(tone), clip_fps="30"))
    fake.items.append(audio_item("v", 6, TL0 + 300, 105, str(probe_wav), clip_fps="30"))
    fake.items.append(audio_item("m", 7, TL0, 900, str(music), clip_fps="30"))  # 배경 음악
    sig = _sig(obs_video)
    p = plan_slot(_req(), _env(fake, cache_dir), VoiceMemory(), override=VoiceOverride(sig, 1))
    assert p.tracks == [2] and p.debug["own_items"] == 3
    assert p.warnings.get("single_file") == 1 and len(p.rows) == 2
    snap = read_snapshot(_env(fake, cache_dir))
    assert [it.track for it in snap.items if helper_own(snap, it)] == [5, 6, 6]
    _no_mutations(fake)


@requires_ffmpeg
def test_single_stream_recording_with_a_second_mono_file_still_counts(gap_audio, cache_dir, tmp_path):
    """소리가 하나뿐인 녹화끼리는 (따로 녹음한 마이크 등) 예전처럼 둘 다 목소리로 본다."""
    import shutil

    second = tmp_path / "마이크 2.wav"
    shutil.copy(gap_audio, second)
    fake = FakeResolve(timeline_info(start_frame=TL0, end_frame=TL0 + 900, fps="30", start_tc="01:00:00:00"),
                       [audio_item("a", 1, TL0, 900, str(gap_audio), clip_fps="30"),
                        audio_item("b", 2, TL0, 450, str(second), clip_fps="30")])
    p = plan_slot(_req(), _env(fake, cache_dir), VoiceMemory())
    assert isinstance(p, Proposal) and p.tracks == [1, 2] and "single_file" not in p.warnings


# ── 개수 제한, 점 표시, 멈추기 ───────────────────────────────────────────

@requires_ffmpeg
def test_too_many_keeps_the_longest_in_time_order(obs_video, cache_dir):
    fake = _resolve(str(obs_video))
    sig = _sig(obs_video)
    env = _env(fake, cache_dir)
    every = plan_slot(_req(min_s=0.5), env, VoiceMemory(), override=VoiceOverride(sig, 1))
    assert len(every.rows) == 3
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("engine.automation.plan.MAX_MARKERS_PER_PROPOSAL", 2)
        p = plan_slot(_req(min_s=0.5), env, VoiceMemory(), override=VoiceOverride(sig, 1))
    assert p.warnings["too_many"] == 3 and p.found == 3 and len(p.rows) == 2
    assert [r.start for r in p.rows] == sorted(r.start for r in p.rows)
    assert min(r.value for r in p.rows) > min(r.value for r in every.rows)  # 짧은 0.8초 쉼이 빠졌다
    assert [s.custom for s in p.specs] == [f"aih:{p.id}:1", f"aih:{p.id}:2"]


@requires_ffmpeg
def test_point_markers_when_ranges_are_off_or_unsupported(obs_video, cache_dir):
    fake = _resolve(str(obs_video))
    sig = _sig(obs_video)
    p = plan_slot(_req(as_range=False), _env(fake, cache_dir), VoiceMemory(), override=VoiceOverride(sig, 1))
    assert p.point_only is True and all(s.dur == 1 for s in p.specs)
    p = plan_slot(_req(), _env(fake, cache_dir, caps=Caps(range_markers=False)), VoiceMemory(),
                  override=VoiceOverride(sig, 1))
    assert p.point_only is True and all(s.dur == 1 for s in p.specs)
    p = plan_slot(_req(), _env(fake, cache_dir, caps=Caps(range_markers=True)), VoiceMemory(),
                  override=VoiceOverride(sig, 1))
    assert p.point_only is False and all(s.dur > 1 for s in p.specs)


@requires_ffmpeg
def test_cancel_stops_the_plan(obs_video, tmp_path):
    fake = _resolve(str(obs_video))
    stop = threading.Event()
    stop.set()
    with pytest.raises(ffmpeg.Cancelled):
        plan_slot(_req(), _env(fake, tmp_path, cancel=stop), VoiceMemory())
    assert list(tmp_path.glob("*.json")) == []

    # 크기 재는 중에 멈춤: 처음 재는 스트림에서 멈추고 아무것도 저장하지 않는다
    stop = threading.Event()

    def progress(stage, frac, info):
        if stage == 2:
            stop.set()

    with pytest.raises(ffmpeg.Cancelled):
        plan_slot(_req(), _env(fake, tmp_path, cancel=stop, progress=progress), VoiceMemory(),
                  override=VoiceOverride(_sig(obs_video), 1))
    assert list(tmp_path.glob("*.json")) == []
    _no_mutations(fake)


def test_add_more_drops_places_already_marked():
    """[더하기]: 이미 표시가 있는 곳(앞뒤 5프레임)은 빼고 새로 찾은 곳만. 꼬리표는 1부터 다시."""
    from engine.edits.proposal import MARKER_SHIFT, MarkerRow, build_specs, without_near

    rows = [MarkerRow(TL0 + f, TL0 + f + 30, 1.5, f"쉼 {f}", "") for f in (100, 400, 700, 1000)]
    specs = build_specs("Pnew", rows, TL0, "Blue", point=False, note_for=lambda r: "쉼")
    p = Proposal(id="Pnew", kind="mark_pauses", slot=1, origin="button:1", request="쉬는 곳", params={"min_s": 1.5},
                 rows=rows, specs=specs, timeline={}, fps=FPS, tl_start=TL0, tl_end=TL0 + 1800, fingerprint="f",
                 total_s=6.0, found=4)
    new, dropped = without_near(p, [TL0 + 100 + MARKER_SHIFT, TL0 + 703, TL0 + 1000 + MARKER_SHIFT + 1])
    assert dropped == 2 and [r.start - TL0 for r in new.rows] == [400, 1000]
    assert [s.custom for s in new.specs] == ["aih:Pnew:1", "aih:Pnew:2"] and new.count == 2
    assert new.total_s == 3.0 and new.debug["dropped_near"] == 2
    assert p.count == 4  # 원래 제안은 그대로
    same, none = without_near(p, [])
    assert same is p and none == 0
    gone, n = without_near(p, [TL0 + r for r in (100, 400, 700, 1000)])
    assert n == 4 and gone.count == 0
