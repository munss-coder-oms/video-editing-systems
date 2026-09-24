"""영상 파일 정보 읽기 (길이, 해상도, 프레임 속도, 오디오)."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from fractions import Fraction
from pathlib import Path
from typing import Optional

from . import ffmpeg

# 흔히 쓰는 프레임 속도. 휴대폰 영상처럼 가변 프레임(VFR)이면 가장 가까운 값으로 맞춘다.
_STANDARD_RATES = [
    Fraction(24000, 1001),
    Fraction(24),
    Fraction(25),
    Fraction(30000, 1001),
    Fraction(30),
    Fraction(50),
    Fraction(60000, 1001),
    Fraction(60),
    Fraction(100),
    Fraction(120000, 1001),
    Fraction(120),
]


@dataclass
class MediaInfo:
    path: str
    duration: float
    has_video: bool
    width: int = 0
    height: int = 0
    frame_rate: str = "30/1"  # "30000/1001" 같은 분수 문자열
    has_audio: bool = False
    audio_channels: int = 0
    audio_sample_rate: int = 0
    audio_codec: str = ""
    video_codec: str = ""

    @property
    def fps(self) -> Fraction:
        return Fraction(self.frame_rate)

    def to_dict(self) -> dict:
        return asdict(self)

    def summary(self) -> str:
        parts = [f"길이 {format_time(self.duration)}"]
        if self.has_video:
            parts.append(f"{self.width}x{self.height} {float(self.fps):.3f}fps")
        if self.has_audio:
            parts.append(
                f"오디오 {self.audio_channels}ch {self.audio_sample_rate}Hz ({self.audio_codec})"
            )
        else:
            parts.append("오디오 없음")
        return ", ".join(parts)


def format_time(seconds: float) -> str:
    seconds = max(0.0, seconds)
    h = int(seconds // 3600)
    m = int(seconds % 3600 // 60)
    s = seconds % 60
    return f"{h}:{m:02d}:{s:05.2f}" if h else f"{m}:{s:05.2f}"


def snap_frame_rate(rate: Fraction) -> Fraction:
    """측정된 프레임 속도를 가장 가까운 표준 프레임 속도로 맞춘다."""
    if rate <= 0:
        return Fraction(30)
    best = min(_STANDARD_RATES, key=lambda r: abs(float(r) - float(rate)))
    if abs(float(best) - float(rate)) / float(best) < 0.01:
        return best
    return rate.limit_denominator(1001)


def _parse_rate(text: Optional[str]) -> Fraction:
    try:
        r = Fraction(text or "0")
    except (ValueError, ZeroDivisionError):
        return Fraction(0)
    return r


def probe(path: str | Path) -> MediaInfo:
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(f"파일이 없습니다: {path}")
    result = ffmpeg.run(
        ["-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)],
        tool="ffprobe",
    )
    data = json.loads(result.stdout or "{}")
    streams = data.get("streams", [])
    video = next(
        (
            s
            for s in streams
            if s.get("codec_type") == "video"
            and not s.get("disposition", {}).get("attached_pic")
        ),
        None,
    )
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)

    duration = float(data.get("format", {}).get("duration") or 0.0)
    if not duration:
        for s in (video, audio):
            if s and s.get("duration"):
                duration = float(s["duration"])
                break

    info = MediaInfo(path=str(path), duration=duration, has_video=video is not None)
    if video:
        info.width = int(video.get("width") or 0)
        info.height = int(video.get("height") or 0)
        rate = _parse_rate(video.get("avg_frame_rate"))
        if rate <= 0:
            rate = _parse_rate(video.get("r_frame_rate"))
        fps = snap_frame_rate(rate)
        info.frame_rate = f"{fps.numerator}/{fps.denominator}"
        info.video_codec = video.get("codec_name", "")
    if audio:
        info.has_audio = True
        info.audio_channels = int(audio.get("channels") or 0)
        info.audio_sample_rate = int(audio.get("sample_rate") or 0)
        info.audio_codec = audio.get("codec_name", "")
    return info
