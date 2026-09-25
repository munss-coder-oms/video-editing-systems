"""음량 결과 자동 테스트 (PRD 7.4 원칙 5, 1단계 완료 기준)."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from engine.commands import Gain, NormalizeLoudness, default_balance_commands
from engine.job import process_video
from engine.balance import balance
from engine.probe import probe

from .conftest import ffmpeg, load_mono, requires_ffmpeg, segment_lufs, segment_rms

pytestmark = requires_ffmpeg


def _sha1(path) -> str:
    return hashlib.sha1(path.read_bytes()).hexdigest()


@pytest.mark.parametrize("strength", ["weak", "medium", "strong"])
def test_hits_youtube_loudness(uneven_video, tmp_path, strength):
    """모든 강도에서 -14 LUFS ±1, 최대치 -1 dBTP 이하."""
    result = process_video(uneven_video, output_dir=tmp_path, strength=strength)
    after = result.balance.after
    assert abs(after.integrated - (-14.0)) <= 1.0, after
    assert after.true_peak <= -1.0, after


def test_other_target(uneven_video, tmp_path):
    result = process_video(uneven_video, output_dir=tmp_path, target_lufs=-16.0)
    assert abs(result.balance.after.integrated - (-16.0)) <= 1.0


def test_spikes_tamed_and_quiet_lifted(uneven_video, tmp_path):
    result = process_video(uneven_video, output_dir=tmp_path, strength="medium")
    wav = result.balance.output_wav

    normal_before = segment_lufs(uneven_video, 2, 7)
    quiet_before = segment_lufs(uneven_video, 13, 6)
    spike_before = segment_lufs(uneven_video, 25, 1.5)
    normal_after = segment_lufs(wav, 2, 7)
    quiet_after = segment_lufs(wav, 13, 6)
    spike_after = segment_lufs(wav, 25, 1.5)

    # 튀는 소리: 보통 말소리와의 차이가 5dB 이상 줄어든다.
    assert (spike_before - normal_before) - (spike_after - normal_after) >= 5.0
    # 작은 목소리: 보통 말소리와의 차이가 5dB 이상 줄어든다.
    assert (normal_before - quiet_before) - (normal_after - quiet_after) >= 5.0
    # 분석 리포트도 튀던 구간을 찾아낸다.
    assert len(result.balance.before.spikes) >= 2
    assert len(result.balance.after.spikes) < len(result.balance.before.spikes)


def test_silence_noise_not_boosted(gap_audio, tmp_path):
    """말이 없는 구간의 바닥 소음을 말소리만큼 키우지 않는다."""
    result = process_video(gap_audio, output_dir=tmp_path)
    wav = result.balance.output_wav
    gap_before = segment_rms(gap_audio, 2, 7) - segment_rms(gap_audio, 12, 6)
    gap_after = segment_rms(wav, 2, 7) - segment_rms(wav, 12, 6)
    assert gap_after >= gap_before - 3.0


def test_keeps_sync_and_length(click_audio, tmp_path):
    """처리해도 소리 위치와 길이가 그대로여야 영상과 싱크가 맞는다."""
    import numpy as np

    result = process_video(click_audio, output_dir=tmp_path)
    src = load_mono(click_audio)
    out = load_mono(result.balance.output_wav)
    assert abs(len(src) - len(out)) <= 48  # 1ms 이내

    def onset(x):
        seg = x[int(4.9 * 48000):int(5.2 * 48000)]
        t = np.arange(len(seg)) / 48000
        i = np.convolve(seg * np.sin(2 * np.pi * 1000 * t), np.ones(96), "same")
        q = np.convolve(seg * np.cos(2 * np.pi * 1000 * t), np.ones(96), "same")
        env = np.hypot(i, q)
        return np.argmax(env > 0.5 * env.max()) / 48000

    assert abs(onset(src) - onset(out)) < 0.002  # 2ms 이내


def test_source_is_never_modified(uneven_video, tmp_path):
    before = _sha1(uneven_video)
    process_video(uneven_video, output_dir=tmp_path)
    assert _sha1(uneven_video) == before


def test_outputs_written(uneven_video, tmp_path):
    result = process_video(uneven_video, output_dir=tmp_path)
    names = {p.name for p in tmp_path.iterdir()}
    stem = uneven_video.stem
    assert f"{stem}_balanced.wav" in names
    assert f"{stem}_timeline.fcpxml" in names
    assert {"project.json", "음량_리포트.txt", "리졸브_불러오기_방법.txt", "작업로그.log"} <= names
    info = probe(result.balance.output_wav)
    assert info.audio_sample_rate == 48000
    assert info.audio_channels == 2
    assert abs(info.duration - result.media.duration) < 0.1


def test_section_gain_command(click_audio, tmp_path):
    """구간 음량 명령(gain)이 해당 구간만 바꾼다."""
    media = probe(click_audio)
    cmds = [Gain(start=1.0, end=3.0, db=-10.0)]
    result = balance(media, cmds, tmp_path / "out.wav")
    wav = result.output_wav
    # 모노 원본도 스테레오로 내보내므로(채널마다 -3dB) 바꾸지 않은 구간과 비교한 차이로 본다.
    same = segment_rms(wav, 7.0, 3.0) - segment_rms(click_audio, 7.0, 3.0)
    changed = segment_rms(wav, 1.2, 1.6) - segment_rms(click_audio, 1.2, 1.6) - same
    early = segment_rms(wav, 0.2, 0.6) - segment_rms(click_audio, 0.2, 0.6) - same
    assert changed == pytest.approx(-10.0, abs=0.5)
    assert early == pytest.approx(0.0, abs=0.5)


def test_default_commands_end_with_normalize():
    cmds = default_balance_commands("strong", -14.0)
    assert isinstance(cmds[-1], NormalizeLoudness)
    assert {c.op for c in cmds} == {"tame_peaks", "lift_quiet", "normalize_loudness"}


def _tone_onset(x, start=4.5, end=7.0):
    import numpy as np

    seg = x[int(start * 48000):int(end * 48000)]
    t = np.arange(len(seg)) / 48000
    i = np.convolve(seg * np.sin(2 * np.pi * 1000 * t), np.ones(96), "same")
    q = np.convolve(seg * np.cos(2 * np.pi * 1000 * t), np.ones(96), "same")
    env = np.hypot(i, q)
    return start + np.argmax(env > 0.5 * env.max()) / 48000


def test_late_audio_is_aligned_to_video(late_audio_video, click_audio, tmp_path):
    """오디오가 영상보다 늦게 시작하면 WAV 앞을 채워서 영상 첫 프레임에 맞춘다."""
    result = process_video(late_audio_video, output_dir=tmp_path)
    info = result.media
    expected = _tone_onset(load_mono(click_audio)) + (info.audio_start - info.video_start)
    onset = _tone_onset(load_mono(result.balance.output_wav))
    assert abs(onset - expected) < 0.003


def test_section_gain_uses_video_time(late_audio_video, click_audio, tmp_path):
    """오디오가 늦게 시작하는 영상에서도 구간 음량은 영상 시각 기준으로 적용된다.

    짧은 신호는 원본 오디오 5.0초, 영상 기준 약 5.4초에 있다. 5.3~5.6초를 낮추면
    영상 시각으로 계산할 때만 신호가 그 구간 안에 든다.
    """
    media = probe(late_audio_video)
    result = balance(media, [Gain(start=5.3, end=5.6, db=-20.0)], tmp_path / "out.wav")
    d = media.audio_start - media.video_start
    same = segment_rms(result.output_wav, 8.0 + d, 2.0) - segment_rms(click_audio, 8.0, 2.0)
    changed = segment_rms(result.output_wav, 5.0 + d, 0.05) - segment_rms(click_audio, 5.0, 0.05) - same
    assert changed == pytest.approx(-20.0, abs=1.0)


def test_early_audio_is_trimmed_to_video(early_audio_video, click_audio, tmp_path):
    """오디오가 영상보다 먼저 시작하면 영상 첫 프레임 전의 소리를 잘라서 맞춘다."""
    result = process_video(early_audio_video, output_dir=tmp_path)
    expected = _tone_onset(load_mono(click_audio)) - 0.3
    onset = _tone_onset(load_mono(result.balance.output_wav), 4.0, 6.5)
    assert abs(onset - expected) < 0.003


def test_joined_file_gap_keeps_sync(joined_video, click_audio, tmp_path):
    """중간에 소리가 끊긴 파일(이어 붙인 영상)도 끊긴 곳을 무음으로 채워 뒤쪽이 밀리지 않는다."""
    result = process_video(joined_video, output_dir=tmp_path)
    expected = _tone_onset(load_mono(click_audio)) + 2.0  # 원본 5초 → 뒤 파일 2초 → 영상 7초
    onset = _tone_onset(load_mono(result.balance.output_wav), 6.0, 8.5)
    assert abs(onset - expected) < 0.003


def _wav_samples(path) -> int:
    import subprocess

    from engine.ffmpeg import find_tool

    out = subprocess.run(
        [find_tool("ffprobe"), "-v", "error", "-select_streams", "a:0", "-count_packets",
         "-show_entries", "stream=duration_ts,channels,sample_rate", "-of", "json", str(path)],
        capture_output=True, text=True, check=True,
    ).stdout
    import json

    return json.loads(out)["streams"][0]


@pytest.mark.parametrize("fixture", ["uneven_video", "late_audio_video", "joined_video"])
def test_wav_matches_timeline_length(fixture, request, tmp_path):
    """WAV가 리졸브 타임라인의 영상 길이와 샘플 단위까지 같고, 항상 48kHz 스테레오다."""
    from engine.fcpxml import timeline_frames

    video = request.getfixturevalue(fixture)
    result = process_video(video, output_dir=tmp_path)
    info = _wav_samples(result.balance.output_wav)
    expected = round(timeline_frames(result.media) * 48000 / result.media.fps)
    assert int(info["duration_ts"]) == expected
    assert int(info["channels"]) == 2 and int(info["sample_rate"]) == 48000


@pytest.mark.parametrize("room", ["noisy_pause_audio", "loud_room_audio"])
@pytest.mark.parametrize("strength", ["medium", "strong"])
def test_room_noise_in_long_pause_not_boosted(request, room, tmp_path, strength):
    """말을 오래 쉬는 동안의 방 소음이 말소리에 비해 3dB 넘게 커지지 않는다 (점검 E2).
    에어컨처럼 방 소음이 말소리와 가까운 녹음도 작은 소리 올리기가 소음을 키우지 않는다.
    다만 '강하게'는 큰 말소리를 눌러 말소리와 소음의 차이가 조금 줄어들므로 4dB까지 본다."""
    source = request.getfixturevalue(room)
    result = process_video(source, output_dir=tmp_path, strength=strength)
    wav = result.balance.output_wav
    before = segment_rms(source, 12, 3) - segment_rms(source, 2, 7)
    after = segment_rms(wav, 12, 3) - segment_rms(wav, 2, 7)
    limit = 4.0 if (strength, room) == ("strong", "loud_room_audio") else 3.0
    assert after - before < limit


@pytest.mark.parametrize("strength", ["weak", "medium", "strong"])
def test_lone_word_in_pause_is_kept(noisy_pause_audio, tmp_path, strength):
    """오래 쉬다가 짧게 한마디 한 소리가 줄어들지 않는다."""
    result = process_video(noisy_pause_audio, output_dir=tmp_path, strength=strength)
    wav = result.balance.output_wav
    before = segment_rms(noisy_pause_audio, 16.05, 0.4) - segment_rms(noisy_pause_audio, 2, 7)
    after = segment_rms(wav, 16.05, 0.4) - segment_rms(wav, 2, 7)
    assert abs(after - before) < 3.0


def test_mono_and_stereo_get_same_processing(uneven_video, media_dir, tmp_path):
    """같은 소리면 모노든 스테레오든 같은 강도로 처리된다 (점검 E3)."""
    mono = media_dir / "uneven_mono.wav"
    ffmpeg("-i", str(uneven_video), "-vn", "-ac", "1", "-c:a", "pcm_s16le", str(mono))
    stereo = media_dir / "uneven_stereo.wav"
    ffmpeg("-i", str(mono), "-af", "pan=stereo|c0=c0|c1=c0", "-c:a", "pcm_s16le", str(stereo))
    diffs = []
    for i, src in enumerate((mono, stereo)):
        wav = process_video(src, output_dir=tmp_path / str(i)).balance.output_wav
        diffs.append(segment_lufs(wav, 2, 7) - segment_lufs(wav, 13, 6))
    assert abs(diffs[0] - diffs[1]) < 1.0


def test_all_audio_tracks_are_mixed_by_default(two_track_video, tmp_path):
    """게임 소리와 마이크가 따로 녹음돼 있으면 기본으로 둘 다 섞는다 (마이크가 빠지지 않게)."""
    import numpy as np

    result = process_video(two_track_video, output_dir=tmp_path / "all")
    assert result.balance.audio_tracks == [0, 1]
    assert any("오디오 트랙이 2개" in w for w in result.warnings)
    x = load_mono(result.balance.output_wav)
    mic = np.abs(x[int(5.0 * 48000):int(5.05 * 48000)]).max()
    around = np.abs(x[int(4.0 * 48000):int(4.9 * 48000)]).max()
    assert mic > around

    only_game = process_video(two_track_video, output_dir=tmp_path / "game", audio_tracks=[0])
    assert only_game.balance.audio_tracks == [0]
    data = json.loads((Path(only_game.output_dir) / "project.json").read_text(encoding="utf-8"))
    assert data["settings"]["audio_tracks"] == [0]


def test_bad_track_number_rejected(two_track_video, tmp_path):
    with pytest.raises(ValueError):
        process_video(two_track_video, output_dir=tmp_path, audio_tracks=[5])


def test_near_silent_track_is_not_blown_up(media_dir, tmp_path):
    """마이크가 빠져 거의 무음인 트랙은 목표까지 키우지 않고 알린다 (점검 L2)."""
    path = media_dir / "near_silent.wav"
    ffmpeg("-f", "lavfi", "-i", "anoisesrc=c=pink:a=0.005:d=10:r=48000", "-c:a", "pcm_s16le", str(path))
    result = process_video(path, output_dir=tmp_path)
    b, a = result.balance.before, result.balance.after
    assert -70 < b.integrated < -45
    assert a.integrated - b.integrated <= 26.0
    assert any("거의 없습니다" in w for w in result.balance.warnings)


def test_progress_never_goes_backwards(uneven_video, tmp_path):
    values = []
    process_video(uneven_video, output_dir=tmp_path, on_stage=lambda _s, v: values.append(v))
    assert values == sorted(values)
    assert values[-1] == pytest.approx(1.0)


def test_camcorder_m2ts_keeps_sync(late_audio_m2ts, click_audio, tmp_path):
    """캠코더 .m2ts처럼 오디오가 늦게 시작하는 MPEG-TS도 영상 첫 프레임에 맞춘다."""
    result = process_video(late_audio_m2ts, output_dir=tmp_path)
    info = result.media
    expected = _tone_onset(load_mono(click_audio)) + (info.audio_start - info.video_start)
    onset = _tone_onset(load_mono(result.balance.output_wav))
    assert info.audio_start - info.video_start == pytest.approx(0.08, abs=0.02)
    assert abs(onset - expected) < 0.003


def test_unreadable_spatial_audio_track_is_skipped(spatial_audio_video, tmp_path):
    """FFmpeg가 풀 수 없는 트랙(아이폰 공간 음향)은 빼고 처리한다. 예전처럼 실패하지 않는다."""
    result = process_video(spatial_audio_video, output_dir=tmp_path)
    assert result.balance.audio_tracks == [0]
    assert any("읽을 수 없는" in w for w in result.warnings)
    with pytest.raises(ValueError):
        process_video(spatial_audio_video, output_dir=tmp_path / "b", audio_tracks=[1])
