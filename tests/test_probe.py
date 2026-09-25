"""영상 정보 읽기: 세로 영상 회전, 카메라 타임코드, 오디오 시작 시각."""

from __future__ import annotations

import json
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


@requires_ffmpeg
def test_all_audio_tracks_are_listed(two_track_video):
    info = probe(two_track_video)
    assert [t.index for t in info.audio_tracks] == [0, 1]
    assert [t.title for t in info.audio_tracks] == ["Game", "Mic"]
    assert info.audio_tracks[1].label().startswith("2번 트랙")


@requires_ffmpeg
def test_unreadable_track_is_marked(spatial_audio_video):
    info = probe(spatial_audio_video)
    assert [t.decodable for t in info.audio_tracks] == [True, False]
    assert info.has_audio and info.audio_codec == "pcm_s16le"


@requires_ffmpeg
def test_cut_mkv_length_is_real_video_length(cut_mkv):
    """스트림 복사로 잘라 영상이 늦게 시작하는 MKV도 실제 영상 길이로 타임라인을 만든다."""
    import json

    from engine.fcpxml import timeline_frames
    from engine.ffmpeg import find_tool

    info = probe(cut_mkv)
    out = subprocess.run(
        [find_tool("ffprobe"), "-v", "error", "-select_streams", "v:0", "-count_frames",
         "-show_entries", "stream=nb_read_frames", "-of", "json", str(cut_mkv)],
        capture_output=True, text=True, check=True,
    ).stdout
    frames = int(json.loads(out)["streams"][0]["nb_read_frames"])
    assert info.video_start > 0.1
    assert timeline_frames(info) == frames


def test_truncated_ffprobe_json_is_read_again(monkeypatch, tmp_path):
    """ffprobe가 코드 0으로 끝났는데 JSON 끝이 잘려 온 경우 (윈도우 자동 검사, 2026-09-25) 한 번 더 읽는다."""
    from engine import ffmpeg as ffmpeg_mod
    from engine import probe as probe_mod

    good = json.dumps({"streams": [{"index": 0, "codec_type": "audio", "sample_rate": "48000",
                                     "channels": 2, "duration": "10.0"}],
                       "format": {"duration": "10.0"}}, indent=4)
    outputs = [good[: good.rstrip().rfind("}")], good]
    calls = []

    def fake_run(args, tool="ffmpeg", **kwargs):
        calls.append(tool)
        return subprocess.CompletedProcess(args, 0, outputs.pop(0), "")

    monkeypatch.setattr(ffmpeg_mod, "run", fake_run)
    media = tmp_path / "a.wav"
    media.write_bytes(b"")
    assert probe_mod._probe_json(media)["format"]["duration"] == "10.0"
    assert calls == ["ffprobe", "ffprobe"]

    outputs[:] = ["{", "{"]
    with pytest.raises(ffmpeg_mod.FFmpegError):
        probe_mod._probe_json(media)

