"""소리 스트림 ↔ 트랙, 전체 소리(섞은 스트림) 찾기, 소리 모양 확인, 3초 듣기 (설계 B2.3, B11 test_audio_map).

- 연결 정보 JSON(channel_idx는 스트림을 이어 센 채널 번호)이 있으면 그것, 없으면 순서 규칙.
- detect_mix: 합성 4스트림 mp4(소리 0 = 1~3을 섞은 것, 각각 AAC)는 소리 0을 찾고, 따로 녹음한 것은 찾지 않는다.
- 같은 녹화 모양에서 목소리와 게임 스트림이 바뀌면 소리 모양이 달라져 다시 묻는다.
"""

from __future__ import annotations

import json
import wave

import pytest

from engine.analysis_cache import StreamAnalysis, analyze_stream
from engine.probe import AudioTrack, MediaInfo, probe
from engine.resolve_link.ops import Item
from engine.timeline.audio_map import (assign_streams, best_speech_moment, detect_mix, doubled_voice, excerpt_wav,
                                       layout_signature, profile_change, stream_from_mapping,
                                       track_streams_from_probe)
from tests.conftest import requires_ffmpeg
from tests.fakes import audio_item, obs_items

TL0 = 216000


def _mapping(*channels, mute=False) -> str:
    return json.dumps({"track_mapping": {"1": {"channel_idx": list(channels), "mute": mute, "type": "Stereo"}}})


def _items(rows):
    return [Item.from_row(r) for r in rows]


def test_layout_signature_counts_streams_channels_and_titles():
    m = MediaInfo(path="x.mp4", duration=10.0, has_video=True, audio_tracks=[
        AudioTrack(0, channels=2, title="Mix"), AudioTrack(1, channels=1, title="Mic|1"), AudioTrack(2, channels=2)])
    assert layout_signature(m) == "a3:2,1,2:Mix|Mic/1|"


def test_stream_from_mapping_json():
    chans = [2, 2, 2, 2]
    assert stream_from_mapping(_mapping(1, 2), chans) == 0
    assert stream_from_mapping(_mapping(3, 4), chans) == 1
    assert stream_from_mapping(_mapping(7), chans) == 3
    assert stream_from_mapping(_mapping(2, 3), chans) is None  # 두 스트림에 걸침
    assert stream_from_mapping(_mapping(9), chans) is None
    assert stream_from_mapping(_mapping(3, 4, mute=True), chans) is None
    assert stream_from_mapping("{깨짐", chans) is None and stream_from_mapping(None, chans) is None
    assert stream_from_mapping(_mapping(2), [1, 2]) == 1  # 모노 + 스테레오


def test_ordinal_fallback_and_mapping_json():
    rows = obs_items("C:/rec.mp4", TL0, 600)
    items = _items(rows)
    got = assign_streams(items, {"C:/rec.mp4": 4})
    assert [got.stream_of[i.uid] for i in items] == [0, 1, 2, 3]
    assert got.counts() == {"ordinal": 4} and got.unknown == []
    # 연결 정보가 순서와 다르다고 하면 연결 정보를 따른다 (서로 다른 수를 센다)
    probe_read = {"source_audio_mapping": [{"track": 1, "mapping": _mapping(3, 4)},
                                           {"track": 2, "mapping": _mapping(1, 2)}]}
    tm = track_streams_from_probe(probe_read, items, {"C:/rec.mp4": [2, 2, 2, 2]})
    assert tm == {1: ("C:/rec.mp4", 1), 2: ("C:/rec.mp4", 0)}
    got = assign_streams(items, {"C:/rec.mp4": 4}, tm)
    assert [got.stream_of[i.uid] for i in items] == [1, 0, 2, 3]
    assert got.counts() == {"mapping": 2, "ordinal": 2} and got.conflicts == 2


def test_ordinal_needs_the_whole_group_and_later_items_follow_their_track():
    rows = obs_items("C:/rec.mp4", TL0, 600)
    # 뒤쪽에 A2에만 같은 파일 클립이 하나 더 (잘라 붙인 것)
    rows.append(audio_item("late", 2, TL0 + 1000, 300, "C:/rec.mp4", src=1000))
    # 스트림 수와 맞지 않는 묶음: 다른 파일의 A1 하나 (4스트림 파일)
    rows.append(audio_item("odd", 1, TL0 + 2000, 300, "C:/other.mp4"))
    items = _items(rows)
    got = assign_streams(items, {"C:/rec.mp4": 4, "C:/other.mp4": 4})
    assert got.stream_of["late"] == 1 and got.method["late"] == "track"
    assert [i.uid for i in got.unknown] == ["odd"]
    single = assign_streams(_items([audio_item("w", 3, TL0, 100, "C:/voice.wav")]), {"C:/voice.wav": 1})
    assert single.stream_of["w"] == 0 and single.method["w"] == "single"


def test_doubled_voice():
    assert doubled_voice(0, 1, [0, 1, 2]) is True
    assert doubled_voice(0, 1, [1, 2]) is False  # 섞은 스트림이 꺼져 있음
    assert doubled_voice(0, 1, [0, 2]) is False  # 목소리 스트림이 꺼져 있음
    assert doubled_voice(None, 1, [0, 1]) is False
    assert doubled_voice(0, None, [0, 3]) is True  # 목소리를 아직 모르면 다른 스트림이 켜져 있는지


def test_profile_change_reasons():
    old = {"typical": -30.0, "noise_floor": -60.0, "active_ratio": 0.7, "integrated": -31.0}
    assert profile_change(old, dict(old)) is None
    assert profile_change(old, dict(old, typical=-18.0)) == "typical"
    assert profile_change(old, dict(old, active_ratio=0.1)) == "active"
    assert profile_change(old, dict(old, integrated=-60.0)) == "silent"
    assert profile_change(None, dict(old, integrated=-60.0)) == "silent"
    assert profile_change(None, old) is None


def test_best_speech_moment_picks_loudest_three_seconds():
    t = [round(0.1 * (i + 1), 3) for i in range(200)]
    s = [-40.0 if not 120 <= i < 150 else -20.0 for i in range(200)]
    a = StreamAnalysis(path="x", stream=0, duration=20.0, integrated=-30.0, true_peak=-3.0, lra=3.0,
                       typical=-30.0, noise_floor=-60.0, t=t, m=list(s), s=s)
    start = best_speech_moment(a)
    assert 12.0 - 0.2 <= start + 3.0 <= 15.0 + 0.2


@requires_ffmpeg
def test_detect_mix_flags_the_summed_stream(obs_video):
    media = probe(str(obs_video))
    assert layout_signature(media) == "a4:2,2,2,2:|||"
    assert [t.start for t in media.audio_tracks] == [0.0, 0.0, 0.0, 0.0]  # 스트림마다 start_time을 읽는다
    analyses = {s: analyze_stream(str(obs_video), s, media) for s in range(4)}
    r = detect_mix(analyses)
    assert r.stream == 0 and r.shares[0] >= 0.8
    assert all(r.shares[k] < 0.5 for k in (1, 2, 3))
    assert detect_mix({k: analyses[k] for k in (0, 1)}).stream is None  # 둘이면 판단하지 않는다
    # 목소리(1)와 게임(2)을 바꿔 고르면 소리 모양이 크게 달라 다시 묻는다
    assert profile_change(analyses[1].profile(), analyses[2].profile()) in ("typical", "active")
    assert profile_change(analyses[1].profile(), analyses[1].profile()) is None


@requires_ffmpeg
def test_detect_mix_leaves_independent_streams_alone(obs_video_unmixed):
    media = probe(str(obs_video_unmixed))
    analyses = {s: analyze_stream(str(obs_video_unmixed), s, media) for s in range(4)}
    assert detect_mix(analyses).stream is None


@requires_ffmpeg
def test_per_stream_start_time_is_read(late_audio_video):
    media = probe(str(late_audio_video))
    assert media.audio_tracks[0].start == pytest.approx(0.4, abs=0.02)


@requires_ffmpeg
def test_excerpt_wav_is_three_seconds_of_one_stream(obs_video, tmp_path):
    media = probe(str(obs_video))
    out = excerpt_wav(str(obs_video), 1, 10.5, tmp_path / "listen.wav", media=media)
    with wave.open(str(out), "rb") as w:
        assert (w.getnchannels(), w.getframerate(), w.getsampwidth()) == (2, 48000, 2)
        assert w.getnframes() == pytest.approx(3 * 48000, abs=48000 * 0.05)
