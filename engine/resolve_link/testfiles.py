"""연결 시험에 쓰는 파일 (시험용 소리).

리졸브로 가져올 파일은 우체통의 files 폴더에만 둔다 (Lua가 그 밖의 경로는 거부한다).
"""

from __future__ import annotations

import math
import os
import sys
import wave
from array import array
from pathlib import Path
from typing import Optional

from ..ffmpeg import FFmpegError, find_tool, run
from .paths import files_dir

TEST_TONE_NAME = "aih_test_tone.wav"
TONE_RATE = 48000
TONE_CHANNELS = 2
TONE_FREQ = 1000
TONE_LEVEL_DBFS = -20.0


def _tone_ok(path: Path, seconds: float) -> bool:
    """이미 있는 시험용 소리를 그대로 써도 되는지 (리졸브가 파일을 쥐고 있을 수 있어 덮어쓰지 않는다)."""
    try:
        with wave.open(str(path), "rb") as w:
            return (
                w.getframerate() == TONE_RATE
                and w.getnchannels() == TONE_CHANNELS
                and w.getsampwidth() == 2
                and w.getnframes() == int(round(seconds * TONE_RATE))
            )
    except (OSError, EOFError, wave.Error):
        return False


def _write_with_ffmpeg(path: Path, seconds: float) -> None:
    amp = 10 ** (TONE_LEVEL_DBFS / 20)
    # 채널마다 식을 따로 준다 (-ac 2로 늘리면 FFmpeg가 3dB 줄여서 섞는다).
    expr = "|".join([f"{amp:.6f}*sin(2*PI*{TONE_FREQ}*t)"] * TONE_CHANNELS)
    run(
        [
            "-v", "error", "-y",
            "-f", "lavfi",
            "-i", f"aevalsrc={expr}:c=stereo:s={TONE_RATE}:d={seconds:.6f}",
            "-ar", str(TONE_RATE), "-c:a", "pcm_s16le",
            # 정확한 길이 (aevalsrc는 마지막 묶음을 조금 더 만들 수 있다)
            "-af", f"atrim=end_sample={int(round(seconds * TONE_RATE))}",
            "-f", "wav", str(path),
        ]
    )


def _write_with_wave(path: Path, seconds: float) -> None:
    amp = 10 ** (TONE_LEVEL_DBFS / 20) * 32767
    total = int(round(seconds * TONE_RATE))
    # 1kHz는 48kHz에서 48샘플마다 정확히 한 바퀴라 한 주기를 만들어 반복한다.
    period = TONE_RATE // math.gcd(TONE_RATE, TONE_FREQ)
    cycle = array("h")
    for n in range(period):
        v = int(round(amp * math.sin(2 * math.pi * TONE_FREQ * n / TONE_RATE)))
        cycle.extend([v] * TONE_CHANNELS)
    samples = array("h")
    whole, rest = divmod(total, period)
    for _ in range(whole):
        samples.extend(cycle)
    samples.extend(cycle[: rest * TONE_CHANNELS])
    if sys.byteorder == "big":
        samples.byteswap()
    with wave.open(str(path), "wb") as w:
        w.setnchannels(TONE_CHANNELS)
        w.setsampwidth(2)
        w.setframerate(TONE_RATE)
        w.writeframes(samples.tobytes())


def make_test_tone(seconds: float = 3.0, path: Optional[Path] = None, use_ffmpeg: bool = True) -> Path:
    """1kHz, -20dBFS, 48kHz 스테레오 16비트 시험용 소리를 files 폴더에 만든다.

    FFmpeg가 있으면 FFmpeg로, 없으면 파이썬 wave 모듈로 만든다.
    같은 파일이 이미 있으면 그대로 쓴다.
    """
    target = Path(path) if path is not None else files_dir() / TEST_TONE_NAME
    target.parent.mkdir(parents=True, exist_ok=True)
    if _tone_ok(target, seconds):
        return target
    tmp = target.with_name(target.stem + ".part.wav")
    try:
        written = False
        if use_ffmpeg:
            try:
                find_tool("ffmpeg")
                _write_with_ffmpeg(tmp, seconds)
                written = _tone_ok(tmp, seconds)
            except (FFmpegError, OSError):
                written = False
        if not written:
            _write_with_wave(tmp, seconds)
        os.replace(tmp, target)
    finally:
        try:
            tmp.unlink()
        except OSError:
            pass
    return target
