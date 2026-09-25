"""영상 파일 정보 읽기 (길이, 해상도, 프레임 속도, 오디오, 타임코드)."""

from __future__ import annotations

import functools
import json
import math
import re
from dataclasses import asdict, dataclass, field
from fractions import Fraction
from pathlib import Path
from typing import List, Optional

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
class AudioTrack:
    """파일 안의 오디오 트랙 하나 (게임 소리·마이크가 따로 녹음된 영상은 여러 개)."""

    index: int  # 오디오 트랙 순서 (0부터). FFmpeg의 0:a:<index>
    channels: int = 0
    sample_rate: int = 0
    codec: str = ""
    title: str = ""  # OBS 등이 붙인 트랙 이름 (없으면 빈 값)
    language: str = ""
    start: float = 0.0  # 트랙 시작 시각 (초, 파일에 적힌 값 그대로)
    # FFmpeg가 풀 수 있는 형식인지. 아이폰 공간 음향(APAC) 트랙처럼 못 푸는 트랙은 섞지 않는다.
    decodable: bool = True

    def label(self) -> str:
        name = f"{self.index + 1}번 트랙"
        extra = [f"{self.channels}ch" if self.channels else "", self.title, self.language]
        extra = [e for e in extra if e and e != "und"]
        return f"{name} ({', '.join(extra)})" if extra else name


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
    audio_start: float = 0.0  # 첫 오디오 트랙 시작 시각 (초). 영상과 다르면 싱크를 맞춰 준다
    format_start: float = 0.0  # 파일 전체 시작 시각. FFmpeg는 이 값을 0으로 당겨서 처리한다
    variable_frame_rate: bool = False  # 휴대폰 영상처럼 프레임 간격이 일정하지 않음
    timecode: str = ""  # 카메라가 기록한 시작 타임코드 ("01:00:00:00"). 없으면 빈 값
    has_audio: bool = False
    audio_channels: int = 0
    audio_sample_rate: int = 0
    audio_codec: str = ""
    video_codec: str = ""
    audio_tracks: List[AudioTrack] = field(default_factory=list)

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


@functools.lru_cache(maxsize=1)
def _audio_decoders() -> frozenset:
    """이 FFmpeg가 풀 수 있는 오디오 코덱 이름들. 목록을 못 읽으면 빈 집합."""
    try:
        out = ffmpeg.run(["-hide_banner", "-codecs"]).stdout
    except ffmpeg.FFmpegError:
        return frozenset()
    names = set()
    for line in out.splitlines():
        parts = line.split()
        if len(parts) >= 2 and len(parts[0]) == 6 and parts[0][0] == "D" and parts[0][2] == "A":
            names.add(parts[1])
    return frozenset(names)


def _decodable(codec: str) -> bool:
    if not codec or codec in ("none", "unknown"):
        return False
    decoders = _audio_decoders()
    return not decoders or codec in decoders


def _video_tag_length(data: dict, video: dict) -> float:
    """MKV/WebM의 DURATION 태그로 영상 길이를 구한다.

    FFmpeg가 쓴 파일(OBS, HandBrake, LosslessCut 포함)은 이 태그에 '끝나는 시각'을 적으므로,
    영상이 0초보다 늦게 시작하면 시작 시각만큼 빼야 실제 길이가 된다.
    """
    tag = _parse_duration_tag((video.get("tags") or {}).get("DURATION"))
    if not tag:
        return 0.0
    encoder = ((data.get("format") or {}).get("tags") or {}).get("ENCODER") or ""
    if encoder.startswith("Lavf"):
        return max(0.0, tag - _float(video.get("start_time")))
    return tag


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


def _probe_json(path: Path) -> dict:
    """ffprobe가 돌려준 JSON.

    윈도우 자동 검사에서 ffprobe가 정상 종료(코드 0)했는데도 JSON 맨 끝의 '}' 한 줄이 빠진 채로
    온 적이 있다 (2026-09-25, 같은 파일을 다시 읽으면 정상). 그래서 한 번 더 읽고, 그래도 깨져 있으면
    알아볼 수 있는 오류를 낸다.
    """
    error: Optional[json.JSONDecodeError] = None
    for _ in range(2):
        result = ffmpeg.run(
            ["-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)],
            tool="ffprobe",
        )
        try:
            return json.loads(result.stdout or "{}")
        except json.JSONDecodeError as exc:
            error = exc
    raise ffmpeg.FFmpegError(f"ffprobe가 영상 정보를 온전히 돌려주지 않았습니다 ({error}): {path}")


def probe(path: str | Path) -> MediaInfo:
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(f"파일이 없습니다: {path}")
    data = _probe_json(path)
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
    info.format_start = _float(data.get("format", {}).get("start_time"))
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
            info.variable_frame_rate = nominal > 0 and avg > 0
        if native > 0:
            native = _clean_rate(native)
            info.native_frame_rate = f"{native.numerator}/{native.denominator}"
        timeline = snap_frame_rate(native)
        info.frame_rate = f"{timeline.numerator}/{timeline.denominator}"
        if not info.native_frame_rate:
            info.native_frame_rate = info.frame_rate

        info.video_duration = _float(video.get("duration")) or _video_tag_length(data, video) or duration
        # 시작 시각이 적혀 있지 않으면 파일 전체 시작 시각을 쓴다.
        raw_start = video.get("start_time")
        info.video_start = _float(raw_start) if raw_start not in (None, "N/A") else info.format_start
        info.video_codec = video.get("codec_name", "")
        info.timecode = _find_timecode(data, streams, video)
    audios = [s for s in streams if s.get("codec_type") == "audio"]
    for i, a in enumerate(audios):
        tags = a.get("tags") or {}
        codec = a.get("codec_name") or ""
        info.audio_tracks.append(
            AudioTrack(
                index=i,
                channels=int(a.get("channels") or 0),
                sample_rate=int(a.get("sample_rate") or 0),
                codec=codec,
                title=_track_title(tags),
                language=(tags.get("language") or "").strip(),
                start=_float(a.get("start_time")),
                decodable=_decodable(codec),
            )
        )
    # 대표 오디오 정보는 풀 수 있는 첫 트랙에서 가져온다.
    first = next((audios[t.index] for t in info.audio_tracks if t.decodable), None)
    if first:
        info.has_audio = True
        info.audio_channels = int(first.get("channels") or 0)
        info.audio_sample_rate = int(first.get("sample_rate") or 0)
        info.audio_codec = first.get("codec_name", "")
        info.audio_start = _float(first.get("start_time"))
    return info


_GENERIC_HANDLERS = {"soundhandler", "sound media handler", "core media audio", "apple sound media handler", "audio"}


def _track_title(tags: dict) -> str:
    """OBS 등이 붙인 트랙 이름. mp4가 자동으로 붙이는 의미 없는 이름(SoundHandler 등)은 뺀다."""
    title = (tags.get("title") or "").strip()
    if title:
        return title
    handler = (tags.get("handler_name") or "").strip()
    return "" if handler.lower() in _GENERIC_HANDLERS else handler
