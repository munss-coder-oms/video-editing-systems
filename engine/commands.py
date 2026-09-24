"""편집 명령 (PRD 8장).

자동화와 (나중에 만들) 채팅이 모두 같은 형식의 편집 명령을 만들고,
엔진은 이 명령 목록만 보고 결과를 만든다.

새 편집 기능을 추가할 때는 이 파일에 명령 클래스 하나를 만들고
@register를 붙이면 끝이다. 다른 코드는 고치지 않는다 (PRD 7.4 원칙 2).
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field, fields
from typing import ClassVar, Dict, List, Optional, Type

COMMAND_SCHEMA_VERSION = 1


class CommandError(ValueError):
    """잘못된 편집 명령."""


@dataclass
class AudioContext:
    """오디오 필터를 만들 때 명령이 참고하는 분석 정보."""

    input_lufs: float  # 원본 말소리의 보통 음량 (loudness.typical_level)
    duration: float
    channels: int = 2  # 엔진은 항상 스테레오로 바꿔서 처리한다
    noise_floor: Optional[float] = None  # 말을 쉬는 순간의 바닥 소음 (LUFS). 모르면 None

    @property
    def channel_level(self) -> float:
        """채널 하나의 말소리 크기 (dBFS 근사).

        LUFS는 채널을 모두 더한 값이지만 압축기와 작은 소리 올리기는 채널마다 동작하므로,
        채널 수만큼 빼야 모노와 스테레오가 같은 강도로 처리된다.
        """
        return self.input_lufs - 10 * math.log10(max(1, self.channels))


_REGISTRY: Dict[str, Type["Command"]] = {}


def register(cls: Type["Command"]) -> Type["Command"]:
    if not cls.op:
        raise TypeError(f"{cls.__name__}에 op 이름이 없습니다.")
    if cls.op in _REGISTRY:
        raise TypeError(f"편집 명령 '{cls.op}'이(가) 이미 등록되어 있습니다.")
    _REGISTRY[cls.op] = cls
    return cls


def registered_ops() -> List[str]:
    return sorted(_REGISTRY)


@dataclass
class Command:
    op: ClassVar[str] = ""
    # 적용 순서. 값이 작을수록 먼저 적용된다 (구간 음량 → 튀는 소리 → 작은 소리 → 전체 음량).
    stage: ClassVar[int] = 50

    reason: str = ""

    def validate(self) -> None:
        """값이 올바른지 확인한다. 틀리면 CommandError."""

    def audio_filter(self, ctx: AudioContext) -> Optional[str]:
        """이 명령을 FFmpeg 오디오 필터 문자열로 바꾼다. 오디오와 무관하면 None."""
        return None

    def to_dict(self) -> dict:
        return {"op": self.op, **asdict(self)}

    @staticmethod
    def from_dict(data: dict) -> "Command":
        data = dict(data)
        op = data.pop("op", None)
        cls = _REGISTRY.get(op or "")
        if cls is None:
            raise CommandError(f"알 수 없는 편집 명령입니다: {op!r}")
        known = {f.name for f in fields(cls)}
        unknown = set(data) - known
        if unknown:
            raise CommandError(f"'{op}' 명령에 없는 값입니다: {', '.join(sorted(unknown))}")
        try:
            cmd = cls(**data)
        except TypeError as exc:
            raise CommandError(f"'{op}' 명령 형식이 잘못되었습니다: {exc}") from exc
        cmd.validate()
        return cmd


STRENGTHS = ("weak", "medium", "strong")
STRENGTH_LABELS = {"weak": "약하게", "medium": "보통", "strong": "강하게"}


def _check_strength(value: str) -> None:
    if value not in STRENGTHS:
        raise CommandError(f"strength는 {', '.join(STRENGTHS)} 중 하나여야 합니다: {value!r}")


def _db_to_linear(db: float) -> float:
    return 10 ** (db / 20)


@register
@dataclass
class Gain(Command):
    """구간 음량 조절. 예: 2분 10초~2분 15초를 6dB 낮추기 (C-03에서 사용)."""

    op: ClassVar[str] = "gain"
    stage: ClassVar[int] = 10

    start: float = 0.0
    end: float = 0.0
    db: float = 0.0
    fade: float = 0.05  # 경계에서 딸깍 소리가 나지 않게 서서히 바꾸는 시간(초)

    def validate(self) -> None:
        if self.start < 0 or self.end <= self.start:
            raise CommandError("gain: 끝 시간이 시작 시간보다 커야 합니다.")
        if not -40 <= self.db <= 20:
            raise CommandError("gain: db는 -40~20 사이여야 합니다.")
        if not 0 <= self.fade <= 2:
            raise CommandError("gain: fade는 0~2초 사이여야 합니다.")

    def audio_filter(self, ctx: AudioContext) -> str:
        g = _db_to_linear(self.db)
        fade = max(self.fade, 0.001)
        s, e = self.start, self.end
        expr = f"1+({g:.6f}-1)*clip(min(t-{s:.3f}\\,{e:.3f}-t)/{fade:.3f}\\,0\\,1)"
        return f"volume=volume='{expr}':eval=frame"


@register
@dataclass
class TamePeaks(Command):
    """튀는 소리 낮추기 (A-03). 평균 음량보다 크게 튀는 부분만 압축한다."""

    op: ClassVar[str] = "tame_peaks"
    stage: ClassVar[int] = 20

    strength: str = "medium"
    # 평균 음량보다 몇 dB 위부터 누를지. 비우면 strength에 맞춰 자동.
    threshold_db: Optional[float] = None

    _PRESETS: ClassVar[dict] = {
        # 기준(평균 대비 dB), 압축 비율
        "weak": (9.0, 3.0),
        "medium": (6.0, 4.0),
        "strong": (3.0, 6.0),
    }

    def validate(self) -> None:
        _check_strength(self.strength)
        if self.threshold_db is not None and not 0 <= self.threshold_db <= 24:
            raise CommandError("tame_peaks: threshold_db는 0~24 사이여야 합니다.")

    def audio_filter(self, ctx: AudioContext) -> str:
        above, ratio = self._PRESETS[self.strength]
        if self.threshold_db is not None:
            above = self.threshold_db
        threshold_db = min(-1.0, max(-60.0, ctx.channel_level + above))
        threshold = _db_to_linear(threshold_db)
        return (
            f"acompressor=threshold={threshold:.6f}:ratio={ratio}:attack=5:release=200"
            ":knee=4:detection=rms:makeup=1"
        )


@register
@dataclass
class LiftQuiet(Command):
    """작은 목소리 올리기 (A-04). 조용히 말한 구간을 끌어올려 전체를 고르게 한다.

    소리 크기에 따라 올리는 양이 정해진 곡선(FFmpeg compand)을 쓴다.
    - 보통 말소리: 그대로
    - 보통보다 8~18dB 작은 말소리: 최대치만큼 올림
    - 말을 쉬는 순간의 바닥 소음(방 소음, 선풍기, 배경음): 올리지 않음
    쉬는 구간이 길어도 잡음이 커지지 않고, 쉬는 중간의 짧은 한마디도 그대로 들린다.
    """

    op: ClassVar[str] = "lift_quiet"
    stage: ClassVar[int] = 30

    strength: str = "medium"

    _PRESETS: ClassVar[dict] = {"weak": 5.0, "medium": 8.0, "strong": 12.0}  # 최대로 올리는 양(dB)
    # compand가 재는 소리 크기는 짧게 들어 올리고(0.03초) 천천히 내려(0.8초) RMS보다 약 3dB 크다.
    _ENVELOPE_OFFSET_DB: ClassVar[float] = 3.0
    _ATTACK: ClassVar[float] = 0.03
    _DECAY: ClassVar[float] = 0.8

    def validate(self) -> None:
        _check_strength(self.strength)

    def curve(self, ctx: AudioContext) -> Optional[List[tuple]]:
        """(입력 dB, 출력 dB) 점 목록. 올릴 수 없는 소리(너무 시끄러운 녹음 등)면 None."""
        lift = self._PRESETS[self.strength]
        level = ctx.channel_level + self._ENVELOPE_OFFSET_DB  # 보통 말소리
        # 바닥 소음이 말소리보다 15dB 넘게 작을 때만 믿는다. 아니면 쉬는 구간이 없는 녹음으로 본다.
        floor = level - 40.0
        if ctx.noise_floor is not None and ctx.noise_floor < ctx.input_lufs - 15.0:
            noise = ctx.noise_floor - 10 * math.log10(max(1, ctx.channels)) + self._ENVELOPE_OFFSET_DB
            floor = max(floor, noise + 6.0)  # 바닥 소음보다 6dB 위까지는 그대로 둔다
        floor = max(floor, -85.0)
        upper = level - 8.0
        lower = max(level - 18.0, floor + lift)  # 곡선이 너무 가파르지 않게
        if lower > upper - 2.0:  # 잡음이 큰 녹음: 올리는 양을 줄인다
            lower = upper - 2.0
            lift = lower - floor
        if lift < 0.5 or level >= -1.0:
            return None
        return [
            (-90.0, -90.0),
            (floor, floor),
            (lower, lower + lift),
            (upper, upper + lift * 0.6),
            (level, level),
            (0.0, 0.0),
        ]

    def audio_filter(self, ctx: AudioContext) -> Optional[str]:
        points = self.curve(ctx)
        if not points:
            return None
        text = "|".join(f"{i:.1f}/{o:.1f}" for i, o in points)
        level = points[-2][0]
        return (
            f"compand=attacks={self._ATTACK}:decays={self._DECAY}:points={text}"
            f":soft-knee=4:volume={level:.1f}"
        )


@register
@dataclass
class NormalizeLoudness(Command):
    """전체 음량 맞추기 (A-02). 유튜브 기준 -14 LUFS, 최대치 -1 dBTP.

    두 번 분석해서 정확한 증폭값을 정하므로 엔진(balance.py)이 직접 처리한다.
    """

    op: ClassVar[str] = "normalize_loudness"
    stage: ClassVar[int] = 90

    target_lufs: float = -14.0
    true_peak: float = -1.0

    def validate(self) -> None:
        if not -30 <= self.target_lufs <= -5:
            raise CommandError("normalize_loudness: target_lufs는 -30~-5 사이여야 합니다.")
        if not -9 <= self.true_peak <= 0:
            raise CommandError("normalize_loudness: true_peak는 -9~0 사이여야 합니다.")


def default_balance_commands(strength: str = "medium", target_lufs: float = -14.0) -> List[Command]:
    """영상을 열었을 때 자동으로 적용하는 기본 음량 정리 명령."""
    _check_strength(strength)
    return [
        TamePeaks(strength=strength, reason="자동: 튀는 소리 낮추기"),
        LiftQuiet(strength=strength, reason="자동: 작은 목소리 올리기"),
        NormalizeLoudness(target_lufs=target_lufs, reason="자동: 유튜브 기준 음량 맞추기"),
    ]


def commands_from_list(items: List[dict]) -> List[Command]:
    return [Command.from_dict(item) for item in items]
