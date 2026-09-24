"""테스트용 음원/영상은 FFmpeg로 그때그때 만든다 (저장소에 큰 파일을 넣지 않기 위해)."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from engine.ffmpeg import FFmpegError, find_tool


def _have_ffmpeg() -> bool:
    try:
        find_tool("ffmpeg")
        find_tool("ffprobe")
        return True
    except FFmpegError:
        return False


requires_ffmpeg = pytest.mark.skipif(not _have_ffmpeg(), reason="FFmpeg가 설치되어 있지 않음")


def ffmpeg(*args: str) -> None:
    subprocess.run(
        [find_tool("ffmpeg"), "-hide_banner", "-nostdin", "-v", "error", "-y", *args],
        check=True,
    )


# 말소리 대신 쓰는 신호: 3Hz로 크기가 출렁이는 핑크 노이즈.
# 10~20초는 15dB 작게(작은 목소리), 25~26.5초와 32~33초는 14dB 크게(튀는 소리).
SPEECH = "anoisesrc=c=pink:a=0.1:d=40:r=48000"
SHAPE = (
    "volume='if(between(t,10,20),0.18,1)*if(between(t,25,26.5)+between(t,32,33),5,1)'"
    ":eval=frame,apulsator=hz=3:amount=0.6"
)


@pytest.fixture(scope="session")
def media_dir(tmp_path_factory) -> Path:
    return tmp_path_factory.mktemp("media")


@pytest.fixture(scope="session")
def uneven_video(media_dir) -> Path:
    """소리가 들쭉날쭉한 40초 테스트 영상 (29.97fps, 스테레오, 한글 파일 이름)."""
    if not _have_ffmpeg():
        pytest.skip("FFmpeg가 설치되어 있지 않음")
    path = media_dir / "들쭉날쭉 영상.mp4"
    ffmpeg(
        "-f", "lavfi", "-i", "testsrc2=s=640x360:r=30000/1001:d=40",
        "-f", "lavfi", "-i", SPEECH,
        "-filter_complex", f"[1:a]{SHAPE},aformat=channel_layouts=stereo[a]",
        "-map", "0:v", "-map", "[a]",
        "-c:v", "mpeg4", "-q:v", "5",
        "-c:a", "aac", "-b:a", "192k", str(path),
    )
    return path


@pytest.fixture(scope="session")
def gap_audio(media_dir) -> Path:
    """10~20초는 말이 없고 -60dB 정도의 바닥 소음만 있는 30초 모노 음원."""
    if not _have_ffmpeg():
        pytest.skip("FFmpeg가 설치되어 있지 않음")
    path = media_dir / "gap.wav"
    ffmpeg(
        "-f", "lavfi", "-i", "anoisesrc=c=pink:a=0.1:d=30:r=48000",
        "-f", "lavfi", "-i", "anoisesrc=c=white:a=0.001:d=30:r=48000:seed=5",
        "-filter_complex",
        "[0:a]volume='if(between(t,10,20),0,1)':eval=frame,apulsator=hz=3:amount=0.6[s];"
        "[s][1:a]amix=inputs=2:normalize=0,aformat=channel_layouts=mono[a]",
        "-map", "[a]", "-c:a", "pcm_s16le", str(path),
    )
    return path


@pytest.fixture(scope="session")
def click_audio(media_dir) -> Path:
    """5초 지점에 1kHz 짧은 신호가 들어간 12초 음원 (싱크 확인용)."""
    if not _have_ffmpeg():
        pytest.skip("FFmpeg가 설치되어 있지 않음")
    path = media_dir / "click.wav"
    ffmpeg(
        "-f", "lavfi", "-i", "anoisesrc=c=pink:a=0.05:d=12:r=48000",
        "-f", "lavfi", "-i", "sine=f=1000:d=12:r=48000",
        "-filter_complex",
        "[1:a]volume='if(between(t,5,5.05),8,0)':eval=frame[b];[0:a][b]amix=inputs=2:normalize=0[a]",
        "-map", "[a]", "-c:a", "pcm_s16le", str(path),
    )
    return path


@pytest.fixture(scope="session")
def late_audio_video(media_dir, click_audio) -> Path:
    """오디오가 영상보다 0.4초 늦게 시작하는 영상 (카메라·휴대폰 파일에서 흔함).

    소리는 click_audio와 같아서 5초 지점의 짧은 신호가 실제로는 영상 5.4초에 들린다.
    """
    if not _have_ffmpeg():
        pytest.skip("FFmpeg가 설치되어 있지 않음")
    video = media_dir / "video_only.mp4"
    ffmpeg("-f", "lavfi", "-i", "testsrc2=s=320x240:r=30:d=12", "-c:v", "mpeg4", str(video))
    path = media_dir / "late_audio.mov"
    ffmpeg(
        "-i", str(video), "-itsoffset", "0.4", "-i", str(click_audio),
        "-map", "0:v", "-map", "1:a", "-c:v", "copy", "-c:a", "pcm_s16le", str(path),
    )
    return path


@pytest.fixture(scope="session")
def early_audio_video(media_dir, click_audio) -> Path:
    """오디오가 영상보다 0.3초 먼저 시작하는 영상. 5초 지점의 신호가 영상 4.7초에 들린다."""
    if not _have_ffmpeg():
        pytest.skip("FFmpeg가 설치되어 있지 않음")
    video = media_dir / "video_only_early.mp4"
    ffmpeg("-f", "lavfi", "-i", "testsrc2=s=320x240:r=30:d=12", "-c:v", "mpeg4", str(video))
    path = media_dir / "early_audio.mov"
    ffmpeg(
        "-itsoffset", "0.3", "-i", str(video), "-i", str(click_audio),
        "-map", "0:v", "-map", "1:a", "-c:v", "copy", "-c:a", "pcm_s16le", str(path),
    )
    return path


@pytest.fixture(scope="session")
def joined_video(media_dir, click_audio) -> Path:
    """두 파일을 이어 붙인 영상. 앞 파일의 소리가 영상보다 0.5초 짧아 중간에 소리가 끊긴다.

    뒤 파일은 원본 3초부터라서, 원본 5초의 신호가 영상 7초에 들려야 한다.
    """
    if not _have_ffmpeg():
        pytest.skip("FFmpeg가 설치되어 있지 않음")
    video = media_dir / "v5.mp4"
    ffmpeg("-f", "lavfi", "-i", "testsrc2=s=320x240:r=30:d=5", "-c:v", "mpeg4", str(video))
    p1, p2 = media_dir / "part1.mov", media_dir / "part2.mov"
    ffmpeg("-i", str(video), "-t", "4.5", "-i", str(click_audio), "-map", "0:v", "-map", "1:a",
           "-c:v", "copy", "-c:a", "pcm_s16le", str(p1))
    ffmpeg("-i", str(video), "-ss", "3", "-t", "5", "-i", str(click_audio), "-map", "0:v", "-map", "1:a",
           "-c:v", "copy", "-c:a", "pcm_s16le", str(p2))
    listing = media_dir / "parts.txt"
    listing.write_text(f"file '{p1.as_posix()}'\nfile '{p2.as_posix()}'\n", encoding="utf-8")
    path = media_dir / "joined.mkv"
    ffmpeg("-f", "concat", "-safe", "0", "-i", str(listing), "-c", "copy", str(path))
    return path


# 말하다 쉬다 하는 소리: 3.5초마다 0.5초 쉬고, 쉬는 동안에는 말소리보다 30dB 작은 방 소음이 남는다.
PAUSES = "volume='if(lt(mod(t,3.5),3.0),1,0)':eval=frame"


@pytest.fixture(scope="session")
def noisy_pause_audio(media_dir) -> Path:
    """0~10초 말, 10~22초 쉼(16초에 0.5초짜리 한마디), 22~32초 말. 방 소음은 말소리보다 약 28dB 작다."""
    if not _have_ffmpeg():
        pytest.skip("FFmpeg가 설치되어 있지 않음")
    path = media_dir / "noisy_pause.wav"
    ffmpeg(
        "-f", "lavfi", "-i", "anoisesrc=c=pink:a=0.1:d=32:r=48000",
        "-f", "lavfi", "-i", "anoisesrc=c=pink:a=0.0028:d=32:r=48000:seed=3",
        "-filter_complex",
        "[0:a]volume='if(between(t,10,22)*not(between(t,16,16.5)),0,1)':eval=frame,"
        "apulsator=hz=3:amount=0.6[s];[s][1:a]amix=inputs=2:normalize=0,aformat=channel_layouts=mono[a]",
        "-map", "[a]", "-c:a", "pcm_s16le", str(path),
    )
    return path


@pytest.fixture(scope="session")
def two_track_video(media_dir) -> Path:
    """게임 소리(1번 트랙)와 마이크(2번 트랙)가 따로 녹음된 영상 (OBS·엔비디아 녹화처럼).

    마이크 트랙에만 5초 지점에 1kHz 신호가 있다.
    """
    if not _have_ffmpeg():
        pytest.skip("FFmpeg가 설치되어 있지 않음")
    path = media_dir / "two_tracks.mkv"
    ffmpeg(
        "-f", "lavfi", "-i", "testsrc2=s=320x240:r=30:d=10",
        "-f", "lavfi", "-i", "anoisesrc=c=brown:a=0.1:d=10:r=48000",
        "-f", "lavfi", "-i", "sine=f=1000:d=10:r=48000",
        "-filter_complex", "[2:a]volume='if(between(t,5,5.05),0.8,0)':eval=frame[mic]",
        "-map", "0:v", "-map", "1:a", "-map", "[mic]",
        "-metadata:s:a:0", "title=Game", "-metadata:s:a:1", "title=Mic",
        "-c:v", "mpeg4", "-c:a", "pcm_s16le", str(path),
    )
    return path


def segment_lufs(path: Path, start: float, length: float) -> float:
    """파일 일부 구간의 평균 음량(LUFS)."""
    import re

    out = subprocess.run(
        [find_tool("ffmpeg"), "-hide_banner", "-nostdin", "-ss", str(start), "-t", str(length),
         "-i", str(path), "-af", "ebur128", "-f", "null", "-"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    ).stderr
    return float(re.findall(r"I:\s*(-?[\d.]+)\s*LUFS", out)[-1])


def segment_rms(path: Path, start: float, length: float) -> float:
    import re

    out = subprocess.run(
        [find_tool("ffmpeg"), "-hide_banner", "-nostdin", "-ss", str(start), "-t", str(length),
         "-i", str(path), "-af", "astats=metadata=0", "-f", "null", "-"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    ).stderr
    return float(re.findall(r"RMS level dB:\s*(-?[\d.]+)", out)[-1])


def load_mono(path: Path):
    import numpy as np

    raw = subprocess.run(
        [find_tool("ffmpeg"), "-v", "error", "-i", str(path), "-f", "f32le", "-ac", "1", "-ar", "48000", "-"],
        capture_output=True, check=True,
    ).stdout
    return np.frombuffer(raw, dtype=np.float32)
