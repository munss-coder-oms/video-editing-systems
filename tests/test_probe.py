"""영상 정보 읽기: 세로 영상 회전, 카메라 타임코드, 오디오 시작 시각."""

from __future__ import annotations

import subprocess

import pytest

from engine.probe import _rotation, probe

from .conftest import ffmpeg, requires_ffmpeg


@pytest.mark.parametrize(
    "stream, expected",
    [
        ({"side_data_list": [{"side_data_type": "Display Matrix", "rotation": -90}]}, 90),
        ({"side_data_list": [{"rotation": 180}]}, 180),
        ({"tags": {"rotate": "90"}}, 90),
        ({}, 0),
    ],
)
def test_rotation(stream, expected):
    assert _rotation(stream) == expected


@requires_ffmpeg
def test_camera_timecode_is_read(media_dir):
    path = media_dir / "tc.mov"
    ffmpeg(
        "-f", "lavfi", "-i", "testsrc2=s=320x240:r=30000/1001:d=2",
        "-f", "lavfi", "-i", "anoisesrc=d=2:r=48000",
        "-c:v", "mpeg4", "-c:a", "pcm_s16le", "-timecode", "01:00:00:00", str(path),
    )
    info = probe(path)
    assert info.timecode.replace(";", ":") == "01:00:00:00"
    assert info.frame_rate == "30000/1001"


@requires_ffmpeg
def test_portrait_video_size(media_dir):
    path = media_dir / "portrait.mp4"
    try:
        ffmpeg(
            "-display_rotation", "90", "-f", "lavfi", "-i", "testsrc2=s=640x360:r=30:d=1",
            "-c:v", "mpeg4", str(path),
        )
    except subprocess.CalledProcessError:
        pytest.skip("이 FFmpeg는 -display_rotation을 지원하지 않음")
    info = probe(path)
    assert (info.width, info.height) == (360, 640)


@requires_ffmpeg
def test_audio_start_offset_is_read(late_audio_video):
    info = probe(late_audio_video)
    assert info.audio_start - info.video_start == pytest.approx(0.4, abs=0.05)
