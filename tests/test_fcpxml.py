from __future__ import annotations

import xml.etree.ElementTree as ET
from fractions import Fraction

import pytest

from engine.fcpxml import FrameClock, build_fcpxml, file_uri, timeline_size
from engine.probe import MediaInfo, _clean_rate, snap_frame_rate, timecode_seconds


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
    assert video.get("format") == "r2"  # 원본 형식 (타임라인 형식 r1과 따로)
    assert video.get("hasVideo") == "1"
    assert video.get("hasAudio") is None  # 원본 오디오가 타임라인에 함께 올라오지 않게
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
        # 리졸브 무료판 타임라인은 60fps 이하의 정해진 값만 된다.
        (Fraction(285, 10), Fraction(30000, 1001)),
        (Fraction(589, 10), Fraction(60000, 1001)),
        (Fraction(120), Fraction(60)),
        (Fraction(240), Fraction(60)),
        (Fraction(15), Fraction(30)),
    ],
)
def test_snap_frame_rate(measured, expected):
    assert snap_frame_rate(measured) == expected


def test_camera_timecode_start():
    """카메라 타임코드가 01:00:00:00이면 원본 클립의 start도 그 시각이어야 한다."""
    media = _media(timecode="01:00:00:00")
    root = ET.fromstring(build_fcpxml(media, r"C:\out\a.wav").split("\n", 2)[2])
    tc = Fraction(108000 * 1001, 30000)  # 29.97fps에서 01:00:00:00
    video = root.find("resources/asset[@id='r3']")
    clip = root.find("library/event/project/sequence/spine/asset-clip")
    connected = clip.find("asset-clip")
    assert _seconds(video.get("start")) == tc
    assert _seconds(clip.get("start")) == tc
    # 붙인 오디오는 부모 클립의 원본 시간 기준이라 offset도 같은 값이어야 첫 프레임에 맞는다.
    assert _seconds(connected.get("offset")) == tc
    assert connected.get("start") == "0s"


@pytest.mark.parametrize(
    "tc, rate, expected",
    [
        ("00:00:01:00", Fraction(25), Fraction(1)),
        ("01:00:00:00", Fraction(24000, 1001), Fraction(86400 * 1001, 24000)),
        # 29.97 드롭 프레임: 00:01:00;02가 1800번째 프레임
        ("00:01:00;02", Fraction(30000, 1001), Fraction(1800 * 1001, 30000)),
        ("00:10:00;00", Fraction(30000, 1001), Fraction(17982 * 1001, 30000)),
        ("", Fraction(30), Fraction(0)),
        ("잘못된 값", Fraction(30), Fraction(0)),
    ],
)
def test_timecode_seconds(tc, rate, expected):
    assert timecode_seconds(tc, rate) == expected


def test_shorter_wav_limits_only_audio_clip():
    """WAV가 영상보다 짧아도 영상은 끝까지 두고, 붙인 오디오 클립만 WAV 길이로 자른다."""
    media = _media(duration=10.0, video_duration=10.0)
    root = ET.fromstring(build_fcpxml(media, "a.wav", wav_duration=8.0).split("\n", 2)[2])
    seq = root.find("library/event/project/sequence")
    clip = seq.find("spine/asset-clip")
    connected = clip.find("asset-clip")
    assert _seconds(seq.get("duration")) == _seconds(clip.get("duration"))
    assert _seconds(clip.get("duration")) > Fraction(9)
    assert _seconds(connected.get("duration")) <= Fraction(8)


@pytest.mark.parametrize(
    "tc, rate, expected",
    [
        # 파일에 29.97이 2997/100처럼 근사값으로 적혀 있어도 NTSC로 계산해야 한다.
        ("01:00:00:00", Fraction(2997, 100), Fraction(108000 * 1001, 30000)),
        # 119.88fps 드롭 프레임은 분마다 8프레임을 건너뛴다.
        ("00:01:00;08", Fraction(120000, 1001), Fraction(7200 * 1001, 120000)),
        # 59.94fps 드롭 프레임은 분마다 4프레임.
        ("00:01:00;04", Fraction(60000, 1001), Fraction(3600 * 1001, 60000)),
    ],
)
def test_timecode_seconds_ntsc_variants(tc, rate, expected):
    assert timecode_seconds(tc, rate) == expected


@pytest.mark.parametrize(
    "rate, expected",
    [
        (Fraction(2997, 100), Fraction(30000, 1001)),
        (Fraction(5994, 100), Fraction(60000, 1001)),
        (Fraction(2997003, 100000), Fraction(30000, 1001)),
        (Fraction(25), Fraction(25)),
        (Fraction(2500001, 100000), Fraction(25)),
        (Fraction(0), Fraction(0)),
    ],
)
def test_clean_rate(rate, expected):
    assert _clean_rate(rate) == expected


@pytest.mark.parametrize(
    "size, expected",
    [
        ((1920, 1080), (1920, 1080)),
        ((1080, 1920), (1080, 1920)),
        ((3840, 2160), (3840, 2160)),
        ((7680, 4320), (3840, 2160)),
        ((4320, 7680), (2160, 3840)),
        ((5120, 2880), (3840, 2160)),
    ],
)
def test_timeline_size_fits_resolve_free(size, expected):
    assert timeline_size(*size) == expected


def test_high_frame_rate_keeps_native_format():
    media = _media(frame_rate="60/1", native_frame_rate="120/1")
    root = ET.fromstring(build_fcpxml(media, "a.wav").split("\n", 2)[2])
    seq_format = root.find("resources/format[@id='r1']")
    native_format = root.find("resources/format[@id='r2']")
    assert seq_format.get("frameDuration") == "1/60s"
    assert native_format.get("frameDuration") == "1/120s"
