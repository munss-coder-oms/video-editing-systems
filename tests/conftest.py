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
# 실제 말처럼 4초마다 1초씩 쉬고, 쉬는 동안에는 말소리보다 약 35dB 작은 방 소음만 남는다.
SPEECH = "anoisesrc=c=pink:a=0.1:d=40:r=48000"
ROOM = "anoisesrc=c=pink:a=0.0018:d=40:r=48000:seed=11"
SHAPE = (
    "volume='if(between(t,10,20),0.18,1)*if(between(t,25,26.5)+between(t,32,33),5,1)"
    "*if(lt(mod(t,4),3),1,0)'"
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
        "-f", "lavfi", "-i", ROOM,
        "-filter_complex",
        f"[1:a]{SHAPE}[s];[s][2:a]amix=inputs=2:normalize=0,aformat=channel_layouts=stereo[a]",
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


@pytest.fixture(scope="session")
def late_audio_m2ts(media_dir, click_audio) -> Path:
    """캠코더(AVCHD) 같은 .m2ts: 오디오가 영상보다 0.08초 늦게 시작한다. 5초 신호가 영상 5.08초에 들린다."""
    if not _have_ffmpeg():
        pytest.skip("FFmpeg가 설치되어 있지 않음")
    path = media_dir / "camcorder.m2ts"
    ffmpeg(
        "-f", "lavfi", "-i", "testsrc2=s=320x240:r=30000/1001:d=12",
        "-itsoffset", "0.08", "-i", str(click_audio),
        "-map", "0:v", "-map", "1:a", "-c:v", "mpeg2video", "-c:a", "pcm_bluray",
        "-mpegts_m2ts_mode", "1", str(path),
    )
    return path


@pytest.fixture(scope="session")
def spatial_audio_video(media_dir) -> Path:
    """아이폰 공간 음향처럼 FFmpeg가 풀 수 없는 오디오 트랙(apac)이 두 번째로 들어 있는 영상.

    PCM 트랙 두 개로 만든 뒤 두 번째 트랙의 형식 표시를 'apac'으로 바꿔 흉내 낸다.
    """
    if not _have_ffmpeg():
        pytest.skip("FFmpeg가 설치되어 있지 않음")
    src = media_dir / "two_pcm.mov"
    ffmpeg(
        "-f", "lavfi", "-i", "testsrc2=s=320x240:r=30:d=6",
        "-f", "lavfi", "-i", "anoisesrc=c=pink:a=0.1:d=6:r=48000",
        "-map", "0:v", "-map", "1:a", "-map", "1:a",
        "-c:v", "mpeg4", "-c:a", "pcm_s16le", str(src),
    )
    data = bytearray(src.read_bytes())
    # 세 번째 stsd(영상, 오디오1, 오디오2 순서) 안의 PCM 표시 'sowt'를 'apac'으로 바꾼다.
    pos = -1
    for _ in range(3):
        pos = data.find(b"stsd", pos + 1)
    tag = data.find(b"sowt", pos)
    assert pos > 0 and tag > 0
    data[tag:tag + 4] = b"apac"
    path = media_dir / "spatial_audio.mov"
    path.write_bytes(bytes(data))
    return path


@pytest.fixture(scope="session")
def cut_mkv(media_dir) -> Path:
    """앞부분을 스트림 복사로 잘라낸 MKV (영상이 다음 키프레임부터 늦게 시작함)."""
    if not _have_ffmpeg():
        pytest.skip("FFmpeg가 설치되어 있지 않음")
    src = media_dir / "gop.mkv"
    ffmpeg(
        "-f", "lavfi", "-i", "testsrc2=s=320x240:r=30:d=20",
        "-f", "lavfi", "-i", "anoisesrc=c=pink:a=0.1:d=20:r=48000",
        "-c:v", "mpeg4", "-g", "30", "-c:a", "pcm_s16le", str(src),
    )
    path = media_dir / "cut.mkv"
    ffmpeg("-i", str(src), "-ss", "5.5", "-c", "copy", str(path))
    return path


def _pause_audio(path: Path, room: float) -> Path:
    ffmpeg(
        "-f", "lavfi", "-i", "anoisesrc=c=pink:a=0.1:d=32:r=48000",
        "-f", "lavfi", "-i", f"anoisesrc=c=pink:a={room}:d=32:r=48000:seed=3",
        "-filter_complex",
        "[0:a]volume='if(between(t,10,22)*not(between(t,16,16.5)),0,1)':eval=frame,"
        "apulsator=hz=3:amount=0.6[s];[s][1:a]amix=inputs=2:normalize=0,aformat=channel_layouts=mono[a]",
        "-map", "[a]", "-c:a", "pcm_s16le", str(path),
    )
    return path


@pytest.fixture(scope="session")
def noisy_pause_audio(media_dir) -> Path:
    """0~10초 말, 10~22초 쉼(16초에 0.5초짜리 한마디), 22~32초 말. 방 소음은 말소리보다 약 28dB 작다."""
    if not _have_ffmpeg():
        pytest.skip("FFmpeg가 설치되어 있지 않음")
    return _pause_audio(media_dir / "noisy_pause.wav", 0.0028)


@pytest.fixture(scope="session")
def loud_room_audio(media_dir) -> Path:
    """noisy_pause_audio와 같지만 방 소음(에어컨, 선풍기)이 말소리보다 약 13dB만 작다."""
    if not _have_ffmpeg():
        pytest.skip("FFmpeg가 설치되어 있지 않음")
    return _pause_audio(media_dir / "loud_room.wav", 0.0157)


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
