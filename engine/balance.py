"""음량 밸런스 처리 (A-01~A-04, R-01).

순서:
1. 원본 음량 분석
2. 편집 명령(구간 음량 → 튀는 소리 → 작은 소리)을 FFmpeg 필터로 바꿔 적용한 결과를 분석
3. 목표 음량까지 필요한 증폭값을 계산하고, 최대치를 넘지 않게 리미터를 걸어 WAV로 저장
4. 저장한 WAV를 다시 분석해 목표에서 벗어나 있으면 한 번 더 보정
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable, List, Optional

from . import ffmpeg
from .commands import AudioContext, Command, NormalizeLoudness
from .loudness import SILENCE, LoudnessReport, analyze
from .probe import MediaInfo

OUTPUT_SAMPLE_RATE = 48000
# 리미터는 목표 최대치보다 조금 낮게 걸어 샘플 사이 최대치(true peak)까지 여유를 둔다.
_LIMITER_MARGIN_DB = 0.6
_TOLERANCE_LU = 0.3

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


def sync_filter(media: MediaInfo) -> str:
    """WAV의 첫 샘플이 영상 첫 프레임과 같은 순간이 되도록 앞을 채우거나 자른다.

    카메라·휴대폰 파일은 오디오가 영상보다 조금 늦게(또는 먼저) 시작하는 경우가 있다.
    """
    if not media.has_video:
        return ""
    d = media.audio_start - media.video_start
    if d > 0.0005:
        samples = round(d * (media.audio_sample_rate or OUTPUT_SAMPLE_RATE))
        # 시각(t)을 0부터 다시 세야 구간 음량(gain) 같은 시간 기준 명령이 영상 시각과 맞는다.
        return f"asetpts=PTS-STARTPTS,adelay=delays={samples}S:all=1"
    if d < -0.0005:
        return f"asetpts=PTS-STARTPTS,atrim=start={-d:.6f},asetpts=PTS-STARTPTS"
    return ""


def build_dynamics_chain(commands: List[Command], ctx: AudioContext) -> str:
    """normalize_loudness를 뺀 나머지 명령을 순서대로 FFmpeg 필터 체인으로 만든다."""
    parts: list[str] = []
    for cmd in sorted(commands, key=lambda c: c.stage):
        f = cmd.audio_filter(ctx)
        if f:
            parts.append(f)
    return ",".join(parts)


def balance(
    media: MediaInfo,
    commands: List[Command],
    output_wav: str | Path,
    *,
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

    # 단계별 가중치 (분석은 빠르고, 오버샘플링 리미터가 들어가는 저장 단계가 가장 느림)
    weights = [("원본 음량 분석", 0.2), ("처리 결과 미리 분석", 0.25), ("정리된 오디오 저장", 0.4), ("결과 확인", 0.15)]

    def step(index: int):
        name, weight = weights[index]
        base = sum(w for _, w in weights[:index])
        if on_stage:
            on_stage(name, base)

        def cb(p: float) -> None:
            if on_stage:
                on_stage(name, base + weight * p)

        return cb

    src = media.path
    dur = media.duration or None

    before = analyze(src, duration=dur, progress=step(0), is_cancelled=is_cancelled)
    input_lufs = before.typical if before.typical > SILENCE else -23.0
    ctx = AudioContext(input_lufs=input_lufs, duration=media.duration)
    chain = build_dynamics_chain(others, ctx)

    align = sync_filter(media)
    gain_db = 0.0
    if normalize:
        mid = analyze(
            src,
            audio_filter=",".join(p for p in (align, chain) if p) or None,
            duration=dur,
            progress=step(1),
            is_cancelled=is_cancelled,
        )
        if mid.integrated > SILENCE:
            gain_db = max(-30.0, min(40.0, normalize.target_lufs - mid.integrated))
    elif on_stage:
        step(1)(1.0)

    def full_chain(gain: float) -> str:
        parts = [align] if align else []
        if chain:
            parts.append(chain)
        if normalize:
            parts.append(f"volume={gain:.3f}dB")
            parts.append(_limiter(normalize.true_peak))
        else:
            parts.append(f"aresample={OUTPUT_SAMPLE_RATE}")
        return ",".join(parts)

    def render(gain: float) -> str:
        fc = full_chain(gain)
        tmp = output_wav.with_suffix(".tmp.wav")
        try:
            ffmpeg.run(
                [
                    "-y", "-i", src, "-map", "0:a:0", "-vn", "-sn", "-dn",
                    "-af", fc,
                    "-ar", str(OUTPUT_SAMPLE_RATE), "-c:a", "pcm_s24le",
                    "-f", "wav", str(tmp),
                ],
                duration=dur,
                progress=step(2),
                is_cancelled=is_cancelled,
            )
            # 이전 결과 WAV를 리졸브나 재생 프로그램이 열고 있으면 여기서 실패한다 (PermissionError).
            tmp.replace(output_wav)
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise
        return fc

    fc = render(gain_db)
    after = analyze(str(output_wav), duration=dur, progress=step(3), is_cancelled=is_cancelled)

    # 리미터가 많이 눌렀으면 평균 음량이 목표보다 조금 낮아질 수 있어 한 번 더 보정한다.
    if normalize and after.integrated > SILENCE:
        for _ in range(2):
            diff = normalize.target_lufs - after.integrated
            if abs(diff) <= _TOLERANCE_LU:
                break
            gain_db += diff
            fc = render(gain_db)
            after = analyze(str(output_wav), duration=dur, progress=step(3), is_cancelled=is_cancelled)

    if on_stage:
        on_stage("완료", 1.0)
    return BalanceResult(
        output_wav=str(output_wav),
        before=before,
        after=after,
        applied_gain_db=round(gain_db, 2),
        filter_chain=fc,
        commands=[c.to_dict() for c in commands],
    )
