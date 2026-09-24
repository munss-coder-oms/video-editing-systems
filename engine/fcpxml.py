"""다빈치 리졸브(무료판)용 타임라인 파일 만들기 (R-03).

리졸브 무료판은 외부에서 조종할 수 없으므로 FCPXML 파일로 넘긴다.
리졸브에서 `파일 → 가져오기 → 타임라인...`으로 이 파일을 열면
V1에 원본 영상, A1에 음량을 정리한 오디오가 올라온 타임라인이 만들어진다.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from fractions import Fraction
from pathlib import Path, PurePath, PureWindowsPath
from typing import Optional

from .probe import MediaInfo, timecode_seconds

FCPXML_VERSION = "1.8"  # 리졸브 17 이상이 안정적으로 불러오는 버전


def file_uri(path: str | PurePath) -> str:
    """파일 경로를 FCPXML이 쓰는 file:// 주소로 바꾼다 (한글·공백은 %인코딩)."""
    text = str(path)
    if len(text) > 1 and text[1] == ":" or text.startswith("\\\\"):
        return PureWindowsPath(text).as_uri()
    return Path(text).resolve().as_uri()


class FrameClock:
    """FCPXML의 시간 값은 프레임 길이의 정수배여야 한다."""

    def __init__(self, fps: Fraction):
        self.fps = fps
        self.frame = 1 / fps  # 한 프레임 길이(초)

    def frames(self, seconds: float) -> int:
        return int(Fraction(seconds).limit_denominator(1_000_000) * self.fps)

    def time(self, frames: int) -> str:
        if frames == 0:
            return "0s"
        value = self.frame * frames
        if value.denominator == 1:
            return f"{value.numerator}s"
        return f"{value.numerator}/{value.denominator}s"

    @property
    def frame_duration(self) -> str:
        f = self.frame
        return f"{f.numerator}/{f.denominator}s"


def _audio_layout(channels: int) -> str:
    return "mono" if channels == 1 else "stereo" if channels == 2 else "surround"


def _rational(value: Fraction) -> str:
    if value == 0:
        return "0s"
    if value.denominator == 1:
        return f"{value.numerator}s"
    return f"{value.numerator}/{value.denominator}s"


def timeline_size(width: int, height: int) -> tuple[int, int]:
    """리졸브 무료판 타임라인 한도(UHD 3840x2160)에 맞춘 크기. 방향과 비율은 유지."""
    long_side, short_side = max(width, height), min(width, height)
    scale = min(1.0, 3840 / long_side if long_side else 1.0, 2160 / short_side if short_side else 1.0)
    if scale >= 1.0:
        return width, height

    def even(v: float) -> int:
        return max(2, int(round(v / 2)) * 2)

    return even(width * scale), even(height * scale)


def build_fcpxml(
    media: MediaInfo,
    audio_wav: str | PurePath,
    *,
    audio_channels: Optional[int] = None,
    audio_rate: int = 48000,
    wav_duration: Optional[float] = None,
    project_name: Optional[str] = None,
) -> str:
    if not media.has_video:
        raise ValueError("영상 트랙이 없는 파일은 타임라인을 만들 수 없습니다.")
    clock = FrameClock(media.fps)
    video_frames = clock.frames(media.video_duration or media.duration)
    if video_frames <= 0:
        raise ValueError("영상 길이를 알 수 없습니다.")
    # 타임라인과 영상 클립은 영상 전체 길이. 정리된 오디오 클립만 WAV 길이를 넘지 않게 한다.
    dur = clock.time(video_frames)
    audio_frames = min(video_frames, clock.frames(wav_duration)) if wav_duration else video_frames
    audio_dur = clock.time(audio_frames)
    channels = audio_channels or media.audio_channels or 2
    name = project_name or PurePath(media.path).stem
    wav_name = PurePath(str(audio_wav)).stem
    # 카메라 영상은 타임코드가 01:00:00:00처럼 0이 아닌 값에서 시작할 수 있다.
    # FCPXML의 start는 원본 타임코드 기준이라, 여기에 맞춰야 리졸브가 원본을 제대로 찾는다.
    tc_start = timecode_seconds(media.timecode, media.native_fps)

    root = ET.Element("fcpxml", version=FCPXML_VERSION)
    res = ET.SubElement(root, "resources")
    seq_w, seq_h = timeline_size(media.width, media.height)
    ET.SubElement(
        res,
        "format",
        id="r1",
        frameDuration=clock.frame_duration,
        width=str(seq_w),
        height=str(seq_h),
    )
    # 원본 영상 자체의 형식 (실제 크기와 프레임 속도. 120fps, 8K 등도 그대로)
    native = media.native_fps
    ET.SubElement(
        res,
        "format",
        id="r2",
        frameDuration=_rational(1 / native) if native > 0 else clock.frame_duration,
        width=str(media.width),
        height=str(media.height),
    )
    video_attrs = dict(
        id="r3",
        name=PurePath(media.path).stem,
        src=file_uri(media.path),
        start=_rational(tc_start),
        duration=clock.time(video_frames),
        hasVideo="1",
        format="r2",
    )
    if media.has_audio:
        video_attrs.update(
            hasAudio="1",
            audioSources="1",
            audioChannels=str(media.audio_channels or 2),
            audioRate=str(media.audio_sample_rate or 48000),
        )
    ET.SubElement(res, "asset", **video_attrs)
    ET.SubElement(
        res,
        "asset",
        id="r4",
        name=wav_name,
        src=file_uri(audio_wav),
        start="0s",
        duration=clock.time(clock.frames(wav_duration)) if wav_duration else dur,
        hasAudio="1",
        audioSources="1",
        audioChannels=str(channels),
        audioRate=str(audio_rate),
    )

    library = ET.SubElement(root, "library")
    event = ET.SubElement(library, "event", name=name)
    project = ET.SubElement(event, "project", name=f"{name} (음량 정리)")
    sequence = ET.SubElement(
        project,
        "sequence",
        format="r1",
        duration=dur,
        tcStart="0s",
        tcFormat="NDF",
        audioLayout=_audio_layout(channels),
        audioRate="48k" if audio_rate == 48000 else "44.1k",
    )
    spine = ET.SubElement(sequence, "spine")
    # 원본 영상은 영상만 쓰고(srcEnable=video), 정리된 오디오를 아래 레인에 붙인다.
    video_clip = ET.SubElement(
        spine,
        "asset-clip",
        ref="r3",
        name=PurePath(media.path).stem,
        offset="0s",
        start=_rational(tc_start),
        duration=dur,
        tcFormat="NDF",
        srcEnable="video",
    )
    # 붙인 클립의 offset은 부모 클립의 원본 시간 기준이라, 부모의 start(타임코드)와 같아야
    # 영상 첫 프레임과 같은 위치에 놓인다.
    ET.SubElement(
        video_clip,
        "asset-clip",
        ref="r4",
        name=wav_name,
        lane="-1",
        offset=_rational(tc_start),
        start="0s",
        duration=audio_dur,
        audioRole="dialogue",
    )

    ET.indent(root, space="  ")
    body = ET.tostring(root, encoding="unicode")
    return '<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE fcpxml>\n' + body + "\n"


def write_fcpxml(path: str | Path, xml_text: str) -> None:
    Path(path).write_text(xml_text, encoding="utf-8")
