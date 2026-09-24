"""음량 결과 자동 테스트 (PRD 7.4 원칙 5, 1단계 완료 기준)."""

from __future__ import annotations

import hashlib

import pytest

from engine.commands import Gain, NormalizeLoudness, default_balance_commands
from engine.job import process_video
from engine.balance import balance
from engine.probe import probe

from .conftest import load_mono, requires_ffmpeg, segment_lufs, segment_rms

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
    changed = segment_rms(wav, 1.2, 1.6) - segment_rms(click_audio, 1.2, 1.6)
    same = segment_rms(wav, 7.0, 3.0) - segment_rms(click_audio, 7.0, 3.0)
    assert changed == pytest.approx(-10.0, abs=0.5)
    assert same == pytest.approx(0.0, abs=0.5)


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
    changed = segment_rms(result.output_wav, 5.0 + d, 0.05) - segment_rms(click_audio, 5.0, 0.05)
    assert changed == pytest.approx(-20.0, abs=1.0)
