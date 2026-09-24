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

from .probe import MediaInfo

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


def build_fcpxml(
    media: MediaInfo,
    audio_wav: str | PurePath,
    *,
    audio_channels: Optional[int] = None,
    audio_rate: int = 48000,
    project_name: Optional[str] = None,
) -> str:
    if not media.has_video:
        raise ValueError("영상 트랙이 없는 파일은 타임라인을 만들 수 없습니다.")
    clock = FrameClock(media.fps)
    total = clock.frames(media.duration)
    if total <= 0:
        raise ValueError("영상 길이를 알 수 없습니다.")
    dur = clock.time(total)
    channels = audio_channels or media.audio_channels or 2
    name = project_name or PurePath(media.path).stem
    wav_name = PurePath(str(audio_wav)).stem

    root = ET.Element("fcpxml", version=FCPXML_VERSION)
    res = ET.SubElement(root, "resources")
    ET.SubElement(
        res,
        "format",
        id="r1",
        frameDuration=clock.frame_duration,
        width=str(media.width),
        height=str(media.height),
    )
    video_attrs = dict(
        id="r2",
        name=PurePath(media.path).stem,
        src=file_uri(media.path),
        start="0s",
        duration=dur,
        hasVideo="1",
        format="r1",
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
        id="r3",
        name=wav_name,
        src=file_uri(audio_wav),
        start="0s",
        duration=dur,
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
        ref="r2",
        name=PurePath(media.path).stem,
        offset="0s",
        start="0s",
        duration=dur,
        format="r1",
        tcFormat="NDF",
        srcEnable="video",
    )
    ET.SubElement(
        video_clip,
        "asset-clip",
        ref="r3",
        name=wav_name,
        lane="-1",
        offset="0s",
        start="0s",
        duration=dur,
        audioRole="dialogue",
    )

    ET.indent(root, space="  ")
    body = ET.tostring(root, encoding="unicode")
    return '<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE fcpxml>\n' + body + "\n"


def write_fcpxml(path: str | Path, xml_text: str) -> None:
    Path(path).write_text(xml_text, encoding="utf-8")
