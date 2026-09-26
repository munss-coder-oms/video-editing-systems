"""미리보기용 OBS 녹화 흉내 (7분, 60fps): 소리 0 = 1~3을 섞은 것, 1 = 목소리, 2 = 게임, 3 = 음악.

tests/conftest.py의 _obs(OBS_VOICE)와 같은 만듦새(핑크 노이즈 + 3Hz 출렁임, 쉬는 동안 완전 무음, 튀는 곳 5배)를
길게 늘린 것이라 자동화 버튼의 계산이 시험과 같게 움직인다. 한 번 만들면 출력 폴더의 cache에 두고 다시 쓴다
(만드는 방법이 바뀌면 이름의 버전이 바뀌어 새로 만든다).

쉬는 곳은 길이를 여러 가지로 둔다.
- 1.5초가 안 되는 것: 버튼 1 기본값(1.5초)에 안 잡힌다.
- 1.7초: 기본값에만 잡힌다 (0.4초 음량으로 재면 0.1초쯤 길게 나와서, 2초에 가까운 것은 2초 설정에도 잡힌다).
- 2초가 넘는 것: 둘 다 잡힌다. 5분~6분 안에도 여러 개 둔다.
- 6분에 걸친 것: 대화의 구간 표시(5분~6분)에서 빠진다.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Dict, List, Sequence, Tuple

from engine.ffmpeg import FFmpegError, find_tool

MEDIA_VERSION = 1
SECONDS = 420  # 7분: 대화 시험의 3분 20초와 5분~6분이 들어간다
VIDEO_FPS = 60

# 목소리가 쉬는 곳 (원본 초). 괄호 안은 쉰 길이
PAUSES: Tuple[Tuple[float, float], ...] = (
    (12.0, 13.0),     # 1.0
    (31.0, 32.7),     # 1.7
    (55.0, 57.6),     # 2.6
    (78.0, 79.2),     # 1.2
    (101.0, 104.0),   # 3.0
    (140.0, 141.7),   # 1.7
    (175.0, 177.4),   # 2.4
    (215.0, 216.7),   # 1.7
    (248.0, 251.5),   # 3.5
    (283.0, 284.0),   # 1.0
    (305.0, 307.5),   # 2.5  (5분~6분 안)
    (322.0, 323.7),   # 1.7  (5분~6분 안, 2초 안 됨)
    (338.0, 341.0),   # 3.0  (5분~6분 안)
    (352.0, 354.6),   # 2.6  (5분~6분 안)
    (358.8, 361.6),   # 2.8  (6분에 걸침)
    (390.0, 392.6),   # 2.6
    (410.0, 411.7),   # 1.7
)
# 목소리가 확 커지는 곳 (5배 = 약 14dB)
SPIKES: Tuple[Tuple[float, float], ...] = ((47.0, 47.6), (133.0, 133.6), (262.0, 262.5), (318.0, 318.6))


def _between(spans: Sequence[Tuple[float, float]]) -> str:
    return "+".join(f"between(t,{a:g},{b:g})" for a, b in spans)


def voice_filter(pauses: Sequence[Tuple[float, float]] = PAUSES,
                 spikes: Sequence[Tuple[float, float]] = SPIKES) -> str:
    """tests/conftest.py의 OBS_VOICE와 같은 모양 (쉬는 곳 0, 튀는 곳 5배, 3Hz로 출렁임)."""
    return (f"volume='if({_between(pauses)},0,1)*if({_between(spikes)},5,1)':eval=frame,"
            "apulsator=hz=3:amount=0.6")


def media_path(cache_dir: Path) -> Path:
    return Path(cache_dir) / f"obs_preview_v{MEDIA_VERSION}.mp4"


def have_ffmpeg() -> bool:
    try:
        find_tool("ffmpeg")
        find_tool("ffprobe")
        return True
    except FFmpegError:
        return False


SOURCES = {  # 소리마다: lavfi 입력, 다듬기 (tests/conftest.py _obs와 같은 씨앗과 필터)
    "voice": ("anoisesrc=c=pink:a=0.1:d={d}:r=48000:seed=1", "{voice},aformat=channel_layouts=stereo"),
    "game": ("anoisesrc=c=brown:a=0.3:d={d}:r=48000:seed=5", "lowpass=f=400,aformat=channel_layouts=stereo"),
    "music": ("anoisesrc=c=white:a=0.05:d={d}:r=48000:seed=7", "highpass=f=3000,aformat=channel_layouts=stereo"),
}
STREAM_ORDER = ("mix", "voice", "game", "music")
TITLES = {"mix": "Mix", "voice": "Mic", "game": "Game", "music": "Music"}


def _ff(*args: str) -> List[str]:
    return [find_tool("ffmpeg"), "-hide_banner", "-nostdin", "-v", "error", "-y", *args]


def _source_args(name: str, d: int) -> Tuple[List[str], str]:
    src, shape = SOURCES[name]
    return ["-f", "lavfi", "-i", src.format(d=d)], shape.format(voice=voice_filter())


def make_obs_recording(cache_dir: Path, seconds: int = SECONDS) -> Path:
    """없으면 만든다. 있으면 그대로 돌려준다.

    소리 넷과 영상을 따로 동시에 만든 뒤(씨앗이 같아 섞은 소리는 나머지 셋의 합과 같다) 복사로 합친다.
    한 번에 만들면 AAC 인코딩이 차례로 돌아 1분쯤, 이렇게 하면 코어 4개에서 15초쯤 걸린다.
    """
    path = media_path(cache_dir)
    if path.is_file() and path.stat().st_size > 0:
        return path
    work = path.parent / (path.stem + ".parts")
    work.mkdir(parents=True, exist_ok=True)
    d = int(seconds)
    jobs: Dict[str, List[str]] = {
        "video": _ff("-f", "lavfi", "-i", f"color=c=0x303338:s=64x36:r={VIDEO_FPS}:d={d}",
                     "-c:v", "mpeg4", "-q:v", "20", str(work / "video.mp4")),
    }
    for name in ("voice", "game", "music"):
        inp, shape = _source_args(name, d)
        jobs[name] = _ff(*inp, "-af", shape, "-c:a", "aac", "-b:a", "64k", str(work / f"{name}.m4a"))
    inputs: List[str] = []
    chains = []
    for i, name in enumerate(("voice", "game", "music")):
        inp, shape = _source_args(name, d)
        inputs += inp
        chains.append(f"[{i}:a]{shape}[s{i}]")
    graph = ";".join(chains) + ";[s0][s1][s2]amix=inputs=3:normalize=0[mix]"
    jobs["mix"] = _ff(*inputs, "-filter_complex", graph, "-map", "[mix]", "-c:a", "aac", "-b:a", "64k",
                      str(work / "mix.m4a"))
    procs = {name: subprocess.Popen(cmd) for name, cmd in jobs.items()}
    failed = [name for name, proc in procs.items() if proc.wait() != 0]
    if failed:
        raise FFmpegError(f"미리보기 녹화를 만들지 못했어요: {', '.join(failed)}")
    mux = ["-i", str(work / "video.mp4")]
    maps = ["-map", "0:v"]
    for i, name in enumerate(STREAM_ORDER, start=1):
        mux += ["-i", str(work / f"{name}.m4a")]
        maps += ["-map", f"{i}:a"]
    meta = []
    for i, name in enumerate(STREAM_ORDER):
        meta += [f"-metadata:s:a:{i}", f"title={TITLES[name]}"]
    tmp = path.with_name(path.stem + ".part.mp4")
    subprocess.run(_ff(*mux, *maps, *meta, "-c", "copy", "-shortest", str(tmp)), check=True)
    tmp.replace(path)
    for f in work.iterdir():
        f.unlink()
    work.rmdir()
    return path


def make_test_tone(cache_dir: Path) -> Path:
    """지난번 연결 시험의 '삐' 소리 (3초, 1kHz): 옛 시험 트랙 'AI 도우미 시험'의 클립."""
    path = Path(cache_dir) / "test_tone.wav"
    if path.is_file() and path.stat().st_size > 0:
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run([find_tool("ffmpeg"), "-hide_banner", "-nostdin", "-v", "error", "-y",
                    "-f", "lavfi", "-i", "sine=f=1000:d=3:r=48000", "-af", "volume=0.3",
                    "-c:a", "pcm_s16le", str(path)], check=True)
    return path
