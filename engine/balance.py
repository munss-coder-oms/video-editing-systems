"""음량 밸런스 처리 (A-01~A-04, R-01).

순서:
1. 원본 음량 분석
2. 편집 명령(구간 음량 → 튀는 소리 → 작은 소리)을 FFmpeg 필터로 바꿔 적용한 결과를 분석
3. 목표 음량까지 필요한 증폭값을 계산하고, 최대치를 넘지 않게 리미터를 걸어 WAV로 저장
4. 저장한 WAV를 다시 분석해 목표에서 벗어나 있으면 한 번 더 보정

모든 단계는 같은 방식으로 오디오를 꺼낸다 (source_graph): 영상 첫 프레임을 0초로 맞추고,
중간에 끊긴 곳은 무음으로 채우고, 48kHz 스테레오로 바꾼다. 그래서 명령의 시각은 항상 영상 시각이고,
WAV의 첫 샘플은 영상 첫 프레임과 같은 순간이다.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable, List, Optional, Sequence

from . import ffmpeg
from .commands import AudioContext, Command, NormalizeLoudness
from .fcpxml import timeline_frames
from .loudness import SILENCE, LoudnessReport, analyze
from .probe import MediaInfo

OUTPUT_SAMPLE_RATE = 48000
OUTPUT_CHANNELS = 2  # 모노 원본도 스테레오로 내보낸다 (리졸브가 모노를 양쪽에 복사해 3dB 커지는 문제 방지)
# 리미터는 목표 최대치보다 조금 낮게 걸어 샘플 사이 최대치(true peak)까지 여유를 둔다.
_LIMITER_MARGIN_DB = 0.6
_TOLERANCE_LU = 0.3
# 원본이 이보다 작으면 마이크가 빠졌거나 거의 무음인 트랙으로 보고 크게 키우지 않는다.
NEAR_SILENT_LUFS = -45.0
_NEAR_SILENT_MAX_BOOST_DB = 25.0

StageCallback = Callable[[str, float], None]
"""(현재 단계 설명, 전체 진행률 0~1)을 받는 함수."""


@dataclass
class BalanceResult:
    output_wav: str
    before: LoudnessReport
    after: LoudnessReport
    applied_gain_db: float
    filter_chain: str
    commands: List[dict] = field(default_factory=list)
    audio_tracks: List[int] = field(default_factory=list)  # 쓴 오디오 트랙 (0부터)
    warnings: List[str] = field(default_factory=list)  # 사용자에게 알릴 점

    def to_dict(self) -> dict:
        return asdict(self)


def _limiter(true_peak: float) -> str:
    limit = 10 ** ((true_peak - _LIMITER_MARGIN_DB) / 20)
    # 4배 오버샘플링한 상태에서 리미터를 걸어 true peak를 잡고, 다시 48kHz로 되돌린다.
    # latency=1: 리미터 지연을 보정해 영상과 싱크가 어긋나지 않게 한다.
    return (
        "aresample=192000,"
        f"alimiter=limit={max(limit, 0.0625):.6f}:attack=1:release=60:level=0:latency=1,"
        f"aresample={OUTPUT_SAMPLE_RATE}"
    )


def track_filter(media: MediaInfo) -> str:
    """오디오 트랙 하나를 영상 첫 프레임 기준 시각으로 맞추는 필터.

    - 카메라·휴대폰 파일은 오디오가 영상보다 조금 늦게(또는 먼저) 시작하는 경우가 있다.
      FFmpeg는 파일에서 가장 먼저 시작하는 트랙을 0초로 당기므로, 영상 시작 시각만큼 더 빼면
      영상 첫 프레임이 0초가 된다.
    - aresample의 first_pts=0은 0초 전의 소리는 자르고, 0초부터 소리가 늦게 시작하면 무음으로 채운다.
      async=1은 중간에 소리가 끊긴 곳(이어 붙인 파일, OBS 녹화)도 무음으로 채워 뒤쪽이 밀리지 않게 한다.
    """
    offset = (media.video_start - media.format_start) if media.has_video else 0.0
    shift = f"asetpts=PTS-{offset:.6f}/TB," if abs(offset) > 1e-6 else ""
    return (
        f"{shift}aresample={OUTPUT_SAMPLE_RATE}:async=1:min_hard_comp=0.020:first_pts=0,"
        f"aformat=sample_fmts=fltp:sample_rates={OUTPUT_SAMPLE_RATE}:channel_layouts=stereo"
    )


def selected_tracks(media: MediaInfo, tracks: Optional[Sequence[int]] = None) -> List[int]:
    """처리할 오디오 트랙 번호. 지정하지 않으면 모든 트랙을 섞는다.

    게임 소리와 마이크가 따로 녹음된 영상(OBS, 엔비디아 녹화)에서 첫 트랙만 쓰면
    목소리가 통째로 빠지므로, 기본은 모두 섞기다.
    """
    count = len(media.audio_tracks) or (1 if media.has_audio else 0)
    if tracks is None or len(tracks) == 0:
        return list(range(count))
    chosen = sorted(set(int(t) for t in tracks))
    bad = [t for t in chosen if not 0 <= t < count]
    if bad:
        raise ValueError(f"없는 오디오 트랙입니다: {', '.join(str(t + 1) for t in bad)}번 (이 파일은 {count}개)")
    return chosen


def source_graph(media: MediaInfo, tracks: Optional[Sequence[int]] = None) -> str:
    """원본에서 처리할 오디오를 꺼내는 FFmpeg 필터 그래프. 결과 이름은 [src]."""
    chosen = selected_tracks(media, tracks)
    if not chosen:
        raise ffmpeg.FFmpegError("이 영상에는 오디오가 없습니다.")
    per_track = track_filter(media)
    if len(chosen) == 1:
        return f"[0:a:{chosen[0]}]{per_track}[src]"
    parts = [f"[0:a:{t}]{per_track}[t{i}]" for i, t in enumerate(chosen)]
    inputs = "".join(f"[t{i}]" for i in range(len(chosen)))
    parts.append(f"{inputs}amix=inputs={len(chosen)}:normalize=0:duration=longest[src]")
    return ";".join(parts)


def output_samples(media: MediaInfo) -> Optional[int]:
    """정리된 WAV의 정확한 샘플 수. 리졸브 타임라인의 영상 길이와 똑같게 맞춘다."""
    if not media.has_video:
        return None
    frames = timeline_frames(media)
    if frames <= 0:
        return None
    return round(frames * OUTPUT_SAMPLE_RATE / media.fps)


def build_dynamics_chain(commands: List[Command], ctx: AudioContext) -> str:
    """normalize_loudness를 뺀 나머지 명령을 순서대로 FFmpeg 필터 체인으로 만든다."""
    parts: list[str] = []
    for cmd in sorted(commands, key=lambda c: c.stage):
        f = cmd.audio_filter(ctx)
        if f:
            parts.append(f)
    return ",".join(parts)


class _Progress:
    """단계별 진행률. 보정 단계가 다시 돌아도 막대가 뒤로 가지 않는다."""

    def __init__(self, on_stage: Optional[StageCallback]):
        self.on_stage = on_stage
        self.last = 0.0

    def emit(self, name: str, value: float) -> None:
        if not self.on_stage:
            return
        self.last = max(self.last, min(1.0, value))
        self.on_stage(name, self.last)

    def span(self, name: str, lo: float, hi: float) -> Callable[[float], None]:
        self.emit(name, lo)
        return lambda p: self.emit(name, lo + (hi - lo) * max(0.0, min(1.0, p)))


def balance(
    media: MediaInfo,
    commands: List[Command],
    output_wav: str | Path,
    *,
    audio_tracks: Optional[Sequence[int]] = None,
    on_stage: Optional[StageCallback] = None,
    is_cancelled: Optional[Callable[[], bool]] = None,
) -> BalanceResult:
    if not media.has_audio:
        raise ffmpeg.FFmpegError("이 영상에는 오디오가 없습니다.")
    for cmd in commands:
        cmd.validate()

    normalize = next((c for c in commands if isinstance(c, NormalizeLoudness)), None)
    others = [c for c in commands if not isinstance(c, NormalizeLoudness)]
    output_wav = Path(output_wav)
    output_wav.parent.mkdir(parents=True, exist_ok=True)
    tracks = selected_tracks(media, audio_tracks)
    graph = source_graph(media, tracks)
    samples = output_samples(media)
    warnings: list[str] = []

    # 진행률 구간 (분석은 빠르고, 오버샘플링 리미터가 들어가는 저장 단계가 가장 느림)
    progress = _Progress(on_stage)
    src = media.path
    dur = media.duration or None

    before = analyze(
        src, source=graph, duration=dur,
        progress=progress.span("원본 음량 분석", 0.0, 0.15), is_cancelled=is_cancelled,
    )
    input_lufs = before.typical if before.typical > SILENCE else -23.0
    ctx = AudioContext(
        input_lufs=input_lufs,
        duration=media.duration,
        channels=OUTPUT_CHANNELS,
        noise_floor=before.noise_floor if before.noise_floor > SILENCE else None,
    )
    chain = build_dynamics_chain(others, ctx)

    gain_db = 0.0
    max_gain: Optional[float] = None
    if normalize:
        mid = analyze(
            src, source=graph, audio_filter=chain or None, duration=dur,
            progress=progress.span("처리 결과 미리 분석", 0.15, 0.35), is_cancelled=is_cancelled,
        )
        if mid.integrated > SILENCE:
            gain_db = max(-30.0, min(40.0, normalize.target_lufs - mid.integrated))
            if SILENCE < before.integrated < NEAR_SILENT_LUFS:
                # 거의 무음인 트랙을 목표까지 키우면 잡음만 커진다.
                max_gain = before.integrated + _NEAR_SILENT_MAX_BOOST_DB - mid.integrated
                if gain_db > max_gain:
                    gain_db = max_gain
                    warnings.append(
                        f"원본 소리가 거의 없습니다 ({before.integrated:.1f} LUFS). 마이크가 빠졌거나 "
                        f"꺼져 있었는지 확인하세요. 잡음만 커지지 않도록 {_NEAR_SILENT_MAX_BOOST_DB:.0f}dB까지만 "
                        "키웠습니다."
                    )
    else:
        progress.emit("처리 결과 미리 분석", 0.35)
    if before.integrated <= SILENCE:
        warnings.append(
            "원본에 소리가 거의 없습니다 (무음). 마이크가 빠졌거나 꺼져 있었는지 확인하세요. "
            "음량은 바꾸지 않았습니다."
        )

    ceiling = normalize.true_peak if normalize else 0.0

    def full_chain(gain: float, peak: float) -> str:
        parts = [chain] if chain else []
        if normalize:
            parts.append(f"volume={gain:.3f}dB")
            parts.append(_limiter(peak))
        if samples:
            # 영상보다 짧으면 끝을 무음으로 채우고, 길면 잘라서 타임라인 길이와 정확히 맞춘다.
            parts.append(f"apad=whole_len={samples},atrim=end_sample={samples}")
        return ",".join(parts) or "anull"

    def render(gain: float, peak: float, lo: float, hi: float) -> str:
        fc = full_chain(gain, peak)
        tmp = output_wav.with_suffix(".tmp.wav")
        try:
            ffmpeg.run(
                [
                    "-y", "-i", src,
                    "-filter_complex", f"{graph};[src]{fc}[out]", "-map", "[out]",
                    "-ar", str(OUTPUT_SAMPLE_RATE), "-ac", str(OUTPUT_CHANNELS), "-c:a", "pcm_s24le",
                    # 4GB가 넘는 긴 오디오(약 4시간)도 깨지지 않게 필요하면 RF64 형식으로 저장
                    "-rf64", "auto",
                    "-f", "wav", str(tmp),
                ],
                duration=dur,
                progress=progress.span("정리된 오디오 저장", lo, hi),
                is_cancelled=is_cancelled,
            )
            # 이전 결과 WAV를 리졸브나 재생 프로그램이 열고 있으면 여기서 실패한다 (PermissionError).
            tmp.replace(output_wav)
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise
        return fc

    def check(lo: float, hi: float) -> LoudnessReport:
        return analyze(
            str(output_wav), duration=dur,
            progress=progress.span("결과 확인", lo, hi), is_cancelled=is_cancelled,
        )

    fc = render(gain_db, ceiling, 0.35, 0.72)
    after = check(0.72, 0.8)

    # 리미터가 많이 눌렀으면 평균 음량이 목표보다 조금 낮아지거나, 최대치가 넘을 수 있어 보정한다.
    if normalize and after.integrated > SILENCE:
        for attempt in range(2):
            diff = normalize.target_lufs - after.integrated
            over = after.true_peak - normalize.true_peak
            new_gain = gain_db + diff if abs(diff) > _TOLERANCE_LU else gain_db
            if max_gain is not None:
                new_gain = min(new_gain, max_gain)
            new_ceiling = ceiling - (over + 0.1) if over > 0.05 else ceiling
            if abs(new_gain - gain_db) < 0.05 and new_ceiling == ceiling:
                break
            gain_db, ceiling = new_gain, max(normalize.true_peak - 6.0, new_ceiling)
            base = 0.8 + 0.1 * attempt
            fc = render(gain_db, ceiling, base, base + 0.07)
            after = check(base + 0.07, base + 0.1)

        if abs(after.integrated - normalize.target_lufs) > 1.0 and max_gain is None:
            warnings.append(
                f"평균 음량이 목표({normalize.target_lufs:.0f} LUFS)와 "
                f"{after.integrated - normalize.target_lufs:+.1f} 차이 납니다. 음악·박수처럼 큰 소리가 많은 영상에서 "
                "생길 수 있습니다. 리졸브에서 오디오 트랙 볼륨으로 조금 조절하세요."
            )
        if after.true_peak > normalize.true_peak + 0.1:
            warnings.append(
                f"최대치가 {after.true_peak:.1f} dBTP로 목표({normalize.true_peak:.0f})보다 조금 높습니다. "
                "유튜브가 이 부분을 살짝 눌러 줄 수 있습니다."
            )

    progress.emit("완료", 1.0)
    return BalanceResult(
        output_wav=str(output_wav),
        before=before,
        after=after,
        applied_gain_db=round(gain_db, 2),
        filter_chain=f"{graph};[src]{fc}[out]",
        commands=[c.to_dict() for c in commands],
        audio_tracks=tracks,
        warnings=warnings,
    )
