"""음량 분석 저장소 (설계 B2.4 1).

열쇠 = (절대 경로, 크기, 고친 시각, 스트림, ENGINE_ANALYSIS_VERSION). 파일이나 분석 방법이 바뀌면 다시 잰다.
FFmpeg는 낮은 우선순위로 돌린다.
"""

from __future__ import annotations

import json
import os

import pytest

from engine import analysis_cache as ac
from engine import ffmpeg
from engine.analysis_cache import AnalysisCache, StreamAnalysis, analyze_stream
from engine.probe import probe
from tests.conftest import requires_ffmpeg


def _fake(path: str, stream: int = 0, m=None) -> StreamAnalysis:
    m = m if m is not None else [-20.0, -20.0, -70.0, -20.0]
    t = [round(0.1 * (i + 1), 3) for i in range(len(m))]
    return StreamAnalysis(path=path, stream=stream, duration=len(m) / 10, integrated=-20.0, true_peak=-3.0,
                          lra=2.0, typical=-20.0, noise_floor=-60.0, t=t, m=list(m), s=list(m))


def test_json_round_trip_and_bad_shape(tmp_path):
    a = _fake(str(tmp_path / "x.wav"))
    b = StreamAnalysis.from_json(json.loads(json.dumps(a.to_json())))
    assert (b.t, b.m, b.s, b.typical, b.version) == (a.t, a.m, a.s, a.typical, ac.ENGINE_ANALYSIS_VERSION)
    bad = a.to_json()
    bad["m"] = bad["m"][:-1]
    with pytest.raises(ValueError):
        StreamAnalysis.from_json(bad)


def test_presence_floor_counts_digital_silence_and_active_ratio():
    # 게이트를 켠 마이크: 쉴 때 완전히 0 → 바닥은 −70, 말하는 0.1초는 모두 "소리 나는 중"
    a = _fake("x", m=[-30.0] * 8 + [-200.0] * 2)
    assert a.presence_floor() == -70.0
    assert a.active_ratio() == pytest.approx(0.8)
    p = a.profile()
    assert set(p) == {"typical", "noise_floor", "active_ratio", "integrated"} and p["active_ratio"] == 0.8
    # 늘 같은 크기의 소음: 바닥이 곧 그 크기라 "소리 나는 중"이 없다
    assert _fake("x", m=[-30.0] * 10).active_ratio() == 0.0


def test_put_get_and_key_changes_with_the_file(tmp_path):
    f = tmp_path / "a.wav"
    f.write_bytes(b"x" * 10)
    cache = AnalysisCache(tmp_path / "cache")
    assert cache.get(str(f), 0) is None
    target = cache.put(_fake(str(f)))
    assert target is not None and target.parent == tmp_path / "cache"
    assert cache.get(str(f), 0).m == _fake(str(f)).m
    assert cache.get(str(f), 1) is None  # 다른 스트림
    st = f.stat()
    os.utime(f, ns=(st.st_atime_ns, st.st_mtime_ns + 10 ** 9))
    assert cache.get(str(f), 0) is None  # 파일이 바뀌면 다시 잰다
    assert AnalysisCache.key(str(tmp_path / "없음.wav"), 0) is None


def test_version_change_and_broken_file_are_ignored(tmp_path, monkeypatch):
    f = tmp_path / "a.wav"
    f.write_bytes(b"x")
    cache = AnalysisCache(tmp_path / "cache")
    target = cache.put(_fake(str(f)))
    target.write_text("{깨진", encoding="utf-8")
    assert cache.get(str(f), 0) is None
    cache.put(_fake(str(f)))
    monkeypatch.setattr(ac, "ENGINE_ANALYSIS_VERSION", ac.ENGINE_ANALYSIS_VERSION + 1)
    assert cache.get(str(f), 0) is None


def test_old_files_are_pruned(tmp_path, monkeypatch):
    monkeypatch.setattr(ac, "MAX_FILES", 2)
    cache = AnalysisCache(tmp_path / "cache")
    for i in range(3):
        f = tmp_path / f"{i}.wav"
        f.write_bytes(b"x")
        target = cache.put(_fake(str(f)))
        os.utime(target, (1000 + i, 1000 + i))
    names = sorted(p.name for p in (tmp_path / "cache").glob("*.json"))
    assert len(names) == 2
    assert cache.get(str(tmp_path / "0.wav"), 0) is None  # 가장 오래된 것이 지워졌다


@requires_ffmpeg
def test_analyze_stream_hits_cache_and_runs_at_low_priority(gap_tone, tmp_path, monkeypatch):
    seen = []
    real = ffmpeg.run

    def spy(*args, **kw):
        seen.append(kw.get("priority"))
        return real(*args, **kw)

    monkeypatch.setattr(ffmpeg, "run", spy)
    cache = AnalysisCache(tmp_path / "cache")
    a, hit = cache.get_or_analyze(str(gap_tone), 0)
    assert hit is False and (cache.hits, cache.misses) == (0, 1)
    assert "below_normal" in seen
    assert len(a.t) == len(a.m) == len(a.s) and a.duration == pytest.approx(14.0, abs=0.05)
    assert a.elapsed_s > 0
    progress = []
    b, hit = cache.get_or_analyze(str(gap_tone), 0, progress=progress.append)
    assert hit is True and b.m == a.m and progress == [1.0] and cache.hits == 1


@requires_ffmpeg
def test_analyze_stream_reads_one_stream_of_a_multi_stream_file(obs_video):
    media = probe(str(obs_video))
    voice = analyze_stream(str(obs_video), 1, media)
    game = analyze_stream(str(obs_video), 2, media)
    assert voice.m != game.m
    # 목소리는 쉬는 곳에서 0이 된다 (게이트) → 소리 나는 0.1초가 대부분, 게임은 늘 같은 소음
    assert voice.active_ratio() > 0.6 and game.active_ratio() < 0.1
    assert voice.duration == pytest.approx(30.0, abs=0.1)


@requires_ffmpeg
def test_analyze_stream_can_be_cancelled(gap_tone, tmp_path):
    cache = AnalysisCache(tmp_path / "cache")
    with pytest.raises(ffmpeg.Cancelled):
        cache.get_or_analyze(str(gap_tone), 0, is_cancelled=lambda: True)
    assert list((tmp_path / "cache").glob("*.json")) == []  # 멈춘 분석은 저장하지 않는다
