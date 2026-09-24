"""음량 분석 (A-01).

FFmpeg의 ebur128 필터로 유튜브와 같은 기준(EBU R128 / ITU-R BS.1770)의
통합 음량(LUFS), 최대치(dBTP), 음량 범위(LRA)와 0.1초 단위 음량 곡선을 얻는다.
"""

from __future__ import annotations

import math
import re
from dataclasses import asdict, dataclass, field
from typing import Callable, List, Optional

from . import ffmpeg
from .probe import format_time

_FRAME_RE = re.compile(
    r"t:\s*(?P<t>[\d.]+)\s+.*?M:\s*(?P<m>-?(?:[\d.]+|inf))\s+S:\s*(?P<s>-?(?:[\d.]+|inf))"
)
_I_RE = re.compile(r"I:\s*(-?(?:[\d.]+|inf))\s*LUFS")
_LRA_RE = re.compile(r"LRA:\s*(-?(?:[\d.]+|inf))\s*LU")
_PEAK_RE = re.compile(r"Peak:\s*(-?(?:[\d.]+|inf))\s*dBFS")

SILENCE = -70.0


def _num(text: str) -> float:
    try:
        v = float(text)
    except ValueError:
        return SILENCE
    if math.isinf(v) or math.isnan(v):
        return SILENCE
    return v


@dataclass
class Region:
    start: float
    end: float
    level: float  # 해당 구간의 최대(튀는 구간) 또는 평균(작은 구간) 음량, LUFS

    def describe(self) -> str:
        return f"{format_time(self.start)} ~ {format_time(self.end)} ({self.level:.1f} LUFS)"


@dataclass
class LoudnessReport:
    integrated: float  # LUFS
    true_peak: float  # dBTP
    lra: float  # LU
    # 말소리 구간의 보통 음량 (무음을 뺀 3초 음량의 중앙값). 튀는 소리에 덜 휘둘린다.
    typical: float = SILENCE
    # 말을 쉬는 순간의 바닥 소음 (0.4초 음량의 하위 10%). 작은 소리 올리기가 이보다 충분히 큰 소리만 키운다.
    # 아주 조용한 방은 -70보다 작을 수 있어 SILENCE로 자르지 않는다. 잴 수 없으면 None.
    noise_floor: Optional[float] = None
    spikes: List[Region] = field(default_factory=list)
    quiet: List[Region] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)

    def summary(self) -> str:
        return (
            f"평균 음량 {self.integrated:.1f} LUFS (말소리 {self.typical:.1f}), 최대치 {self.true_peak:.1f} dBTP, "
            f"음량 범위 {self.lra:.1f} LU, 튀는 구간 {len(self.spikes)}곳, "
            f"작은 구간 {len(self.quiet)}곳"
        )


def _group(
    frames: list[tuple[float, float]],
    predicate: Callable[[float], bool],
    *,
    merge_gap: float,
    min_len: float,
    pick: Callable[[list[float]], float],
) -> list[Region]:
    regions: list[Region] = []
    cur_start: Optional[float] = None
    cur_end = 0.0
    values: list[float] = []
    for t, v in frames:
        if predicate(v):
            if cur_start is not None and t - cur_end <= merge_gap:
                cur_end = t
                values.append(v)
            else:
                if cur_start is not None and cur_end - cur_start >= min_len:
                    regions.append(Region(cur_start, cur_end, pick(values)))
                cur_start, cur_end, values = t, t, [v]
    if cur_start is not None and cur_end - cur_start >= min_len:
        regions.append(Region(cur_start, cur_end, pick(values)))
    return regions


def typical_level(short: list[tuple[float, float]], integrated: float) -> float:
    """무음(평균보다 20 LU 이상 작은 곳)을 뺀 3초 음량의 중앙값."""
    values = sorted(v for _, v in short if v > integrated - 20.0)
    if not values:
        return integrated
    return values[len(values) // 2]


# ebur128은 소리가 전혀 없는 구간(디지털 무음)을 -120.7로 적는다. 이것은 방 소음이 아니므로 뺀다.
_DIGITAL_SILENCE = -120.0


def noise_floor(momentary: list[tuple[float, float]]) -> Optional[float]:
    """0.4초 음량 중 하위 10% 값. 말 사이사이 쉬는 순간의 방 소음·배경음 크기에 가깝다."""
    values = sorted(v for _, v in momentary if v > _DIGITAL_SILENCE)
    if not values:
        return None
    return values[len(values) // 10]


def find_spikes(
    frames: list[tuple[float, float]], integrated: float, above_lu: float = 8.0
) -> list[Region]:
    """평균보다 above_lu 이상 갑자기 커지는 구간 (웃음, 박수, 큰 소리)."""
    limit = integrated + above_lu
    regions = _group(
        frames, lambda v: v >= limit, merge_gap=0.5, min_len=0.0, pick=max
    )
    # ebur128의 순간 음량은 0.4초 창이므로 앞뒤로 조금 넓혀서 보여준다.
    return [Region(max(0.0, r.start - 0.4), r.end, r.level) for r in regions]


def find_quiet(
    frames: list[tuple[float, float]], integrated: float, below_lu: float = 10.0
) -> list[Region]:
    """말은 하고 있지만 평균보다 below_lu 이상 작은 구간 (무음은 제외)."""
    low = integrated - below_lu
    floor = max(integrated - 30.0, -60.0)
    return _group(
        frames,
        lambda v: floor < v < low,
        merge_gap=0.5,
        min_len=2.0,
        pick=lambda vs: sum(vs) / len(vs),
    )


def analyze(
    path: str,
    *,
    source: Optional[str] = None,
    audio_filter: Optional[str] = None,
    duration: Optional[float] = None,
    progress: Optional[ffmpeg.ProgressCallback] = None,
    is_cancelled: Optional[Callable[[], bool]] = None,
) -> LoudnessReport:
    """파일(에 audio_filter를 적용한 결과)의 음량을 분석한다. 파일은 바꾸지 않는다.

    source: 오디오를 꺼내는 FFmpeg 필터 그래프 (balance.source_graph, 결과 이름 [src]).
    없으면 첫 번째 오디오 트랙을 그대로 쓴다.
    """
    chain = f"{audio_filter}," if audio_filter else ""
    chain += "ebur128=peak=true:framelog=info"
    if source:
        # -copyts: source 그래프는 파일에 적힌 시각을 그대로 받아 영상 첫 프레임에 맞춘다 (balance.INPUT_OPTIONS)
        args = ["-copyts", "-i", str(path), "-filter_complex", f"{source};[src]{chain}[out]", "-map", "[out]"]
    else:
        args = ["-i", str(path), "-map", "0:a:0", "-vn", "-sn", "-dn", "-af", chain]
    result = ffmpeg.run(
        [*args, "-f", "null", "-"],
        duration=duration,
        progress=progress,
        is_cancelled=is_cancelled,
    )
    err = result.stderr

    frames: list[tuple[float, float]] = []
    short: list[tuple[float, float]] = []
    for m in _FRAME_RE.finditer(err):
        t = float(m.group("t"))
        frames.append((t, _num(m.group("m"))))
        short.append((t, _num(m.group("s"))))

    summary = err[err.rfind("Summary:"):] if "Summary:" in err else err
    i_match = _I_RE.search(summary)
    lra_match = _LRA_RE.search(summary)
    peak_match = _PEAK_RE.search(summary)
    if not i_match:
        raise ffmpeg.FFmpegError("음량 분석 결과를 읽지 못했습니다 (ebur128 요약 없음).")

    integrated = _num(i_match.group(1))
    report = LoudnessReport(
        integrated=integrated,
        true_peak=_num(peak_match.group(1)) if peak_match else 0.0,
        lra=_num(lra_match.group(1)) if lra_match else 0.0,
    )
    if integrated > SILENCE:
        report.typical = typical_level(short, integrated)
        report.noise_floor = noise_floor(frames)
        report.spikes = find_spikes(frames, report.typical)
        report.quiet = find_quiet(short, report.typical)
    return report
