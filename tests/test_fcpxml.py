from __future__ import annotations

import xml.etree.ElementTree as ET
from fractions import Fraction

import pytest

from engine.fcpxml import FrameClock, build_fcpxml, file_uri
from engine.probe import MediaInfo, snap_frame_rate


def _media(**kw) -> MediaInfo:
    base = dict(
        path=r"C:\Users\문성\Videos\첫 영상.mp4",
        duration=600.5,
        has_video=True,
        width=1920,
        height=1080,
        frame_rate="30000/1001",
        has_audio=True,
        audio_channels=2,
        audio_sample_rate=48000,
    )
    base.update(kw)
    return MediaInfo(**base)


def _seconds(value: str) -> Fraction:
    assert value.endswith("s")
    return Fraction(value[:-1])


def test_windows_path_uri():
    uri = file_uri(r"C:\Users\문성\Videos\첫 영상.mp4")
    assert uri.startswith("file:///C:/Users/")
    assert " " not in uri and "문" not in uri  # 한글·공백은 %인코딩


@pytest.mark.parametrize("rate", ["24000/1001", "25/1", "30000/1001", "60/1"])
def test_times_are_frame_aligned(rate):
    media = _media(frame_rate=rate)
    root = ET.fromstring(build_fcpxml(media, r"C:\out\첫 영상_balanced.wav").split("\n", 2)[2])
    frame = _seconds(root.find("resources/format").get("frameDuration"))
    for el in root.iter():
        for attr in ("duration", "offset", "start"):
            if attr in el.attrib:
                assert (_seconds(el.get(attr)) / frame).denominator == 1, (el.tag, attr)


def test_structure_for_resolve():
    xml = build_fcpxml(_media(), r"C:\out\첫 영상_balanced.wav")
    assert xml.startswith('<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE fcpxml>')
    root = ET.fromstring(xml.split("\n", 2)[2])
    assert root.get("version") == "1.8"
    video, audio = root.findall("resources/asset")
    assert video.get("hasVideo") == "1"
    assert audio.get("hasAudio") == "1" and audio.get("audioRate") == "48000"
    clip = root.find("library/event/project/sequence/spine/asset-clip")
    assert clip.get("ref") == video.get("id")
    assert clip.get("srcEnable") == "video"
    connected = clip.find("asset-clip")
    assert connected.get("ref") == audio.get("id")
    assert connected.get("lane") == "-1"
    assert connected.get("duration") == clip.get("duration")


def test_no_video_is_rejected():
    with pytest.raises(ValueError):
        build_fcpxml(_media(has_video=False), "a.wav")


def test_frame_clock():
    clock = FrameClock(Fraction(30000, 1001))
    assert clock.frame_duration == "1001/30000s"
    assert clock.time(0) == "0s"
    assert clock.frames(10.0) == 299


@pytest.mark.parametrize(
    "measured, expected",
    [
        (Fraction(2997, 100), Fraction(30000, 1001)),
        (Fraction(30), Fraction(30)),
        (Fraction(5994, 100), Fraction(60000, 1001)),
        (Fraction(0), Fraction(30)),
    ],
)
def test_snap_frame_rate(measured, expected):
    assert snap_frame_rate(measured) == expected
