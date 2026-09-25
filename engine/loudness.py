"""음량 분석 (A-01).

FFmpeg의 ebur128 필터로 유튜브와 같은 기준(EBU R128 / ITU-R BS.1770)의
통합 음량(LUFS), 최대치(dBTP), 음량 범위(LRA)와 0.1초 단위 음량 곡선을 얻는다.
"""

from __future__ import annotations

import math
import re
from dataclasses import asdict, dataclass, field
from typing import Callable, List, Optional, Sequence, Tuple

from . import ffmpeg
from .probe import format_time

# 0.1초 줄의 M·S는 소리가 전혀 없으면 "nan"이나 "-inf"로 나온다 (예전에는 이런 줄을 건너뛰어
# 디지털 무음으로 쉬는 곳을 찾지 못했다). 이런 값은 FRAME_SILENCE로 적는다.
_FRAME_RE = re.compile(
    r"t:\s*(?P<t>[\d.]+)\s+.*?M:\s*(?P<m>-?(?:[\d.]+|inf|nan))\s+S:\s*(?P<s>-?(?:[\d.]+|inf|nan))"
)
_I_RE = re.compile(r"I:\s*(-?(?:[\d.]+|inf))\s*LUFS")
_LRA_RE = re.compile(r"LRA:\s*(-?(?:[\d.]+|inf))\s*LU")
_PEAK_RE = re.compile(r"Peak:\s*(-?(?:[\d.]+|inf))\s*dBFS")

SILENCE = -70.0


FRAME_SILENCE = -200.0  # 0.1초 줄의 디지털 무음 (방 소음 계산에서는 빠진다: _DIGITAL_SILENCE보다 작다)


def _frame_num(text: str) -> float:
    try:
        v = float(text)
    except ValueError:
        return FRAME_SILENCE
    if math.isinf(v) or math.isnan(v):
        return FRAME_SILENCE
    return v


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
    # keep_frames=True일 때만: 0.1초마다 (t, 0.4초 음량 M) / (t, 3초 음량 S). t는 그 창이 끝나는 시각(초)
    momentary: Optional[List[Tuple[float, float]]] = field(default=None, repr=False)
    short_term: Optional[List[Tuple[float, float]]] = field(default=None, repr=False)

    def to_dict(self) -> dict:
        d = asdict(self)
        d.pop("momentary", None)
        d.pop("short_term", None)
        return d

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
    frames: list[tuple[float, float]], integrated: float, above_lu: float = 8.0, merge_s: float = 0.5
) -> list[Region]:
    """평균보다 above_lu 이상 갑자기 커지는 구간 (웃음, 박수, 큰 소리). merge_s 안으로 붙은 것은 하나로."""
    limit = integrated + above_lu
    regions = _group(
        frames, lambda v: v >= limit, merge_gap=max(0.0, merge_s), min_len=0.0, pick=max
    )
    # ebur128의 순간 음량은 0.4초 창이므로 앞뒤로 조금 넓혀서 보여준다.
    return [Region(max(0.0, r.start - 0.4), r.end, r.level) for r in regions]


# ebur128의 0.4초 음량 M(t)는 [t-0.4, t] 창의 소리다. 조용한 M이 t_a..t_b(0.1초 간격)로 이어지면
# 실제 쉬는 구간은 [t_a-0.5, t_b+0.1]과 [t_a-0.4, t_b] 사이에 있다: 그 가운데 값으로 어림한다.
PAUSE_LEAD = 0.45
PAUSE_TAIL = 0.05
# 0.4초 창이 다 차지 않은 맨 앞 프레임은 쓰지 않는다
_FULL_WINDOW = 0.4
# 프레임 간격(0.1초)보다 조금 넓게: 한 프레임만 빠진 것은 이어진 것으로 본다
_FRAME_GAP = 0.15


def pause_threshold(typical: float, below_lu: float = 25.0, abs_floor: float = -50.0) -> float:
    """이보다 작은 0.4초 음량을 '쉬는 중'으로 본다: 말소리보다 below_lu 작거나, abs_floor(LUFS)보다 작은 곳."""
    return max(typical - below_lu, abs_floor)


def quiet_runs(
    frames: Sequence[Tuple[float, float]], typical: float, below_lu: float = 25.0, abs_floor: float = -50.0
) -> List[Region]:
    """조용한 구간 전부 (길이로 거르지 않고, 여유도 두지 않은 원래 경계). level은 그 안의 가장 큰 M."""
    limit = pause_threshold(typical, below_lu, abs_floor)
    out: List[Region] = []
    run_start: Optional[float] = None
    run_end = 0.0
    loudest = SILENCE
    for t, v in frames:
        if t < _FULL_WINDOW - 1e-6:
            continue
        if v < limit:
            if run_start is not None and t - run_end <= _FRAME_GAP:
                run_end = t
                loudest = max(loudest, v)
            else:
                if run_start is not None:
                    out.append(Region(max(0.0, run_start - PAUSE_LEAD), run_end + PAUSE_TAIL, loudest))
                run_start, run_end, loudest = t, t, v
        elif run_start is not None:
            out.append(Region(max(0.0, run_start - PAUSE_LEAD), run_end + PAUSE_TAIL, loudest))
            run_start = None
    if run_start is not None:
        out.append(Region(max(0.0, run_start - PAUSE_LEAD), run_end + PAUSE_TAIL, loudest))
    return out


def pad_region(r: Region, pad_s: float, *, pad_start: bool = True, pad_end: bool = True) -> Optional[Region]:
    """쉬는 구간의 앞뒤를 pad_s만큼 줄인다 (말 바로 옆은 남겨 둔다). 남는 것이 없으면 None."""
    start = r.start + (pad_s if pad_start else 0.0)
    end = r.end - (pad_s if pad_end else 0.0)
    if end - start <= 0:
        return None
    return Region(start, end, r.level)


def find_pauses(
    frames: Sequence[Tuple[float, float]],
    typical: float,
    below_lu: float = 25.0,
    min_s: float = 1.5,
    pad_s: float = 0.2,
    abs_floor: float = -50.0,
) -> List[Region]:
    """말이 min_s초 넘게 끊긴 곳 (쉬는 곳 표시, 버튼 1).

    frames는 0.4초 음량 M의 (t, LUFS) 목록 (analyze(keep_frames=True).momentary).
    원래 경계로 min_s를 재고, 돌려주는 구간은 앞뒤를 pad_s씩 줄인 것이다.
    """
    out: List[Region] = []
    for r in quiet_runs(frames, typical, below_lu, abs_floor):
        if r.end - r.start + 1e-9 < min_s:
            continue
        padded = pad_region(r, pad_s)
        if padded is not None:
            out.append(padded)
    return out


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
    keep_frames: bool = False,
    low_priority: bool = False,
) -> LoudnessReport:
    """파일(에 audio_filter를 적용한 결과)의 음량을 분석한다. 파일은 바꾸지 않는다.

    source: 오디오를 꺼내는 FFmpeg 필터 그래프 (balance.source_graph, 결과 이름 [src]).
    없으면 첫 번째 오디오 트랙을 그대로 쓴다.
    keep_frames: 0.1초마다의 음량(momentary, short_term)도 돌려준다 (쉬는 곳·튀는 소리 찾기, 분석 캐시).
    low_priority: FFmpeg를 낮은 우선순위로 돌린다 (리졸브를 쓰는 동안).
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
        priority="below_normal" if low_priority else None,
    )
    err = result.stderr

    frames: list[tuple[float, float]] = []
    short: list[tuple[float, float]] = []
    for m in _FRAME_RE.finditer(err):
        t = float(m.group("t"))
        frames.append((t, _frame_num(m.group("m"))))
        short.append((t, _frame_num(m.group("s"))))

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
    if keep_frames:
        report.momentary = frames
        report.short_term = short
    return report
