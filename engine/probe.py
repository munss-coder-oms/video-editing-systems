"""영상 파일 정보 읽기 (길이, 해상도, 프레임 속도, 오디오, 타임코드)."""

from __future__ import annotations

import json
import math
import re
from dataclasses import asdict, dataclass
from fractions import Fraction
from pathlib import Path
from typing import Optional

from . import ffmpeg

# 다빈치 리졸브 무료판에서 만들 수 있는 타임라인 프레임 속도 (60fps 이하).
# 휴대폰 영상처럼 가변 프레임(VFR)이거나 특이한 속도면 이 중 가장 가까운 값으로 맞춘다.
TIMELINE_RATES = [
    Fraction(24000, 1001),
    Fraction(24),
    Fraction(25),
    Fraction(30000, 1001),
    Fraction(30),
    Fraction(48000, 1001),
    Fraction(48),
    Fraction(50),
    Fraction(60000, 1001),
    Fraction(60),
]

_TC_RE = re.compile(r"^(\d{1,2})[:;](\d{2})[:;](\d{2})([:;.])(\d{2,3})$")


@dataclass
class MediaInfo:
    path: str
    duration: float
    has_video: bool
    width: int = 0  # 화면에 보이는 방향 기준 (세로 영상은 세로 크기)
    height: int = 0
    frame_rate: str = "30/1"  # 리졸브 타임라인에 쓸 프레임 속도 ("30000/1001" 같은 분수)
    native_frame_rate: str = ""  # 파일에 기록된 프레임 속도 (120fps 슬로모션 등 그대로)
    video_duration: float = 0.0  # 영상 트랙 길이 (전체 길이와 다를 수 있음)
    video_start: float = 0.0  # 영상 트랙 시작 시각 (초)
    audio_start: float = 0.0  # 오디오 트랙 시작 시각 (초). 영상과 다르면 싱크를 맞춰 준다
    timecode: str = ""  # 카메라가 기록한 시작 타임코드 ("01:00:00:00"). 없으면 빈 값
    has_audio: bool = False
    audio_channels: int = 0
    audio_sample_rate: int = 0
    audio_codec: str = ""
    video_codec: str = ""

    @property
    def fps(self) -> Fraction:
        return Fraction(self.frame_rate)

    @property
    def native_fps(self) -> Fraction:
        return Fraction(self.native_frame_rate or self.frame_rate)

    def to_dict(self) -> dict:
        return asdict(self)

    def summary(self) -> str:
        parts = [f"길이 {format_time(self.duration)}"]
        if self.has_video:
            parts.append(f"{self.width}x{self.height} {float(self.native_fps):.3f}fps")
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
    """측정된 프레임 속도를 리졸브 무료판 타임라인에 쓸 수 있는 가장 가까운 값으로 맞춘다.

    60fps를 넘으면(120fps 등) 반으로, 20fps보다 낮으면(15fps 등) 두 배로 접어서 고른다.
    """
    if rate <= 0:
        return Fraction(30)
    r = float(rate)
    while r > 61:
        r /= 2
    while r < 20:
        r *= 2
    return min(TIMELINE_RATES, key=lambda s: abs(math.log(r / float(s))))


def _parse_rate(text: Optional[str]) -> Fraction:
    try:
        return Fraction(text or "0")
    except (ValueError, ZeroDivisionError):
        return Fraction(0)


def _float(value) -> float:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return 0.0
    return v if math.isfinite(v) else 0.0


def _parse_duration_tag(text: Optional[str]) -> float:
    """MKV의 DURATION 태그("00:01:02.345000000")를 초로."""
    if not text:
        return 0.0
    try:
        h, m, s = text.split(":")
        return int(h) * 3600 + int(m) * 60 + float(s)
    except ValueError:
        return 0.0


def _rotation(video: dict) -> int:
    rot = None
    for sd in video.get("side_data_list", []) or []:
        if "rotation" in sd:
            rot = sd.get("rotation")
            break
    if rot is None:
        rot = (video.get("tags") or {}).get("rotate", 0)
    return int(round(abs(_float(rot)))) % 360


def _find_timecode(data: dict, streams: list, video: Optional[dict]) -> str:
    candidates = [(data.get("format", {}).get("tags") or {}).get("timecode")]
    if video:
        candidates.append((video.get("tags") or {}).get("timecode"))
    for s in streams:
        candidates.append((s.get("tags") or {}).get("timecode"))
    for tc in candidates:
        if tc and _TC_RE.match(tc.strip()):
            return tc.strip()
    return ""


def _is_ntsc(rate: Fraction, nominal: int) -> bool:
    """29.97처럼 정수 속도의 1000/1001배인지 (2997/100처럼 기록된 값도 포함)."""
    r = float(rate)
    return abs(r - nominal * 1000 / 1001) < abs(r - nominal)


def timecode_seconds(timecode: str, native_rate: Fraction) -> Fraction:
    """카메라 타임코드("hh:mm:ss:ff", 드롭 프레임은 ';')를 초(분수)로 바꾼다."""
    m = _TC_RE.match(timecode.strip()) if timecode else None
    if not m or native_rate <= 0:
        return Fraction(0)
    h, mi, s, sep, f = m.groups()
    h, mi, s, f = int(h), int(mi), int(s), int(f)
    nominal = round(float(native_rate))
    if nominal <= 0:
        return Fraction(0)
    ntsc = _is_ntsc(native_rate, nominal)
    frames = ((h * 60 + mi) * 60 + s) * nominal + f
    if sep == ";" and ntsc and nominal % 30 == 0:
        # 드롭 프레임: 10분 단위가 아닌 매 분마다 (30fps 기준 2, 60fps 4, 120fps 8) 프레임 번호를 건너뛴다.
        total_minutes = 60 * h + mi
        frames -= (nominal // 30 * 2) * (total_minutes - total_minutes // 10)
    if ntsc:
        return Fraction(frames * 1001, nominal * 1000)
    return Fraction(frames, nominal)


def _clean_rate(rate: Fraction) -> Fraction:
    """2997/100 같은 근사값을 정확한 30000/1001이나 정수로 바로잡는다."""
    if rate <= 0:
        return rate
    nominal = round(float(rate))
    if nominal <= 0:
        return rate.limit_denominator(1001)
    for exact in (Fraction(nominal), Fraction(nominal * 1000, 1001)):
        if abs(float(rate) / float(exact) - 1) < 2e-4:
            return exact
    return rate.limit_denominator(1001)


def _first_audio_pts(path: Path) -> Optional[float]:
    """처음 디코딩되는 오디오 프레임의 시각.

    스트림 start_time에는 디코더가 버리는 앞부분(Opus pre-skip 등)이 들어 있을 수 있어,
    실제로 소리가 나오는 첫 프레임 시각을 쓴다.
    """
    try:
        result = ffmpeg.run(
            [
                "-v", "error", "-select_streams", "a:0", "-show_frames",
                "-read_intervals", "%+#10", "-show_entries", "frame=pts_time",
                "-of", "csv=p=0", str(path),
            ],
            tool="ffprobe",
        )
    except ffmpeg.FFmpegError:
        return None
    for line in result.stdout.splitlines():
        line = line.strip().rstrip(",")
        try:
            v = float(line)
        except ValueError:
            continue
        if math.isfinite(v):
            return v
    return None


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
            and not (s.get("disposition") or {}).get("attached_pic")
        ),
        None,
    )
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)

    duration = _float(data.get("format", {}).get("duration"))
    if not duration:
        for s in (video, audio):
            if s and _float(s.get("duration")):
                duration = _float(s["duration"])
                break

    info = MediaInfo(path=str(path), duration=duration, has_video=video is not None)
    if video:
        info.width = int(video.get("width") or 0)
        info.height = int(video.get("height") or 0)
        if _rotation(video) % 180 == 90:  # 휴대폰 세로 영상
            info.width, info.height = info.height, info.width

        nominal = _parse_rate(video.get("r_frame_rate"))
        avg = _parse_rate(video.get("avg_frame_rate"))
        if nominal > 0 and (avg <= 0 or abs(float(avg) / float(nominal) - 1) < 0.02):
            native = nominal
        else:  # 가변 프레임: 평균값 사용
            native = avg if avg > 0 else nominal
        if native > 0:
            native = _clean_rate(native)
            info.native_frame_rate = f"{native.numerator}/{native.denominator}"
        timeline = snap_frame_rate(native)
        info.frame_rate = f"{timeline.numerator}/{timeline.denominator}"
        if not info.native_frame_rate:
            info.native_frame_rate = info.frame_rate

        info.video_duration = (
            _float(video.get("duration"))
            or _parse_duration_tag((video.get("tags") or {}).get("DURATION"))
            or duration
        )
        info.video_start = _float(video.get("start_time"))
        info.video_codec = video.get("codec_name", "")
        info.timecode = _find_timecode(data, streams, video)
    if audio:
        info.has_audio = True
        info.audio_channels = int(audio.get("channels") or 0)
        info.audio_sample_rate = int(audio.get("sample_rate") or 0)
        info.audio_codec = audio.get("codec_name", "")
        first = _first_audio_pts(path) if video else None
        info.audio_start = first if first is not None else _float(audio.get("start_time"))
    return info
