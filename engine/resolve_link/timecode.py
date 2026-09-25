"""리졸브 타임코드 ↔ 프레임 수.

리졸브가 주는 타임코드는 "01:00:10:15" 꼴이고, 드롭 프레임(29.97/59.94) 타임라인에서는
마지막 구분자가 ';'이다 ("01:00:10;15"). 드롭 프레임은 매분 앞 2프레임(59.94는 4프레임)을
건너뛰되 10분마다는 건너뛰지 않는다.
"""

from __future__ import annotations

import re
from typing import Tuple, Union

Fps = Union[str, int, float]

_TC_RE = re.compile(r"^\s*(\d+)[:;.,](\d{1,2})[:;.,](\d{1,2})([:;.,])(\d{1,3})\s*$")


def parse_fps(fps: Fps) -> Tuple[float, int]:
    """"29.97", "23.976", 30, "59.94 DF" 같은 값 → (실제 fps, 타임코드에 쓰는 정수 fps)."""
    if isinstance(fps, str):
        text = fps.strip().upper()
        for suffix in ("NDF", "DF", "FPS"):
            if text.endswith(suffix):
                text = text[: -len(suffix)].strip()
        try:
            value = float(text.replace(",", "."))
        except ValueError:
            raise ValueError(f"프레임 속도를 알 수 없습니다: {fps!r}") from None
    else:
        value = float(fps)
    if not value > 0 or value > 1000:
        raise ValueError(f"프레임 속도를 알 수 없습니다: {fps!r}")
    return value, max(1, int(round(value)))


def _drop_count(fps: Fps) -> int:
    """드롭 프레임에서 매분 건너뛰는 프레임 수. 드롭 프레임이 없는 속도면 0."""
    value, nominal = parse_fps(fps)
    if nominal in (30, 60) and abs(value - nominal) > 0.001:
        return 2 if nominal == 30 else 4
    return 0


def _as_bool(flag) -> bool:
    # 리졸브 설정값은 "1"/"0" 같은 글자로 온다.
    if isinstance(flag, str):
        return flag.strip().lower() in ("1", "true", "yes", "on", "df")
    return bool(flag)


def tc_to_frames(tc: str, fps: Fps, drop_frame=None) -> int:
    """타임코드 → 00:00:00:00부터 센 프레임 수.

    drop_frame이 None이면 마지막 구분자가 ';'인지로 정한다. 드롭 프레임은 29.97/59.94에서만 적용된다.
    """
    match = _TC_RE.match(tc or "")
    if not match:
        raise ValueError(f"타임코드 형식이 아닙니다: {tc!r}")
    hh, mm, ss, sep, ff = match.groups()
    hh, mm, ss, ff = int(hh), int(mm), int(ss), int(ff)
    _, nominal = parse_fps(fps)
    if mm >= 60 or ss >= 60 or ff >= nominal:
        raise ValueError(f"타임코드 값이 프레임 속도({fps})와 맞지 않습니다: {tc!r}")
    drop = (sep == ";") if drop_frame is None else _as_bool(drop_frame)
    frames = (hh * 3600 + mm * 60 + ss) * nominal + ff
    skip = _drop_count(fps) if drop else 0
    if skip:
        minutes = hh * 60 + mm
        frames -= skip * (minutes - minutes // 10)
    return frames


def frames_to_tc(frames: int, fps: Fps, drop_frame=False) -> str:
    """프레임 수 → 타임코드. 드롭 프레임이면 마지막 구분자를 ';'로 쓴다."""
    frames = int(frames)
    if frames < 0:
        raise ValueError("프레임 수는 0 이상이어야 합니다")
    _, nominal = parse_fps(fps)
    skip = _drop_count(fps) if _as_bool(drop_frame) else 0
    if skip:
        per_min = nominal * 60 - skip
        per_10min = per_min * 10 + skip
        tens, rest = divmod(frames, per_10min)
        frames += skip * 9 * tens
        if rest > skip:
            frames += skip * ((rest - skip) // per_min)
    ff = frames % nominal
    total_seconds = frames // nominal
    ss = total_seconds % 60
    mm = (total_seconds // 60) % 60
    hh = total_seconds // 3600
    return f"{hh:02d}:{mm:02d}:{ss:02d}{';' if skip else ':'}{ff:02d}"


def is_drop_frame(fps: Fps, *timecodes, flag=None) -> bool:
    """드롭 프레임으로 셀지 한 번만 정한다.

    리졸브가 보여 준 타임코드 가운데 하나라도 ';'를 쓰거나, 프레임 속도에 DF 표시가 있거나,
    설정값(flag)이 드롭 프레임이라고 하면 드롭 프레임. 두 타임코드를 서로 다른 규칙으로 세면
    그 차이(재생 위치)가 수십 프레임 어긋나기 때문이다.
    """
    if any(isinstance(tc, str) and ";" in tc for tc in timecodes):
        return True
    if isinstance(fps, str) and re.search(r"(?<!N)DF\s*$", fps.strip().upper()):
        return True
    return flag is not None and _as_bool(flag)


def tc_offset(tc: str, start_tc: str, fps: Fps, drop_frame=None) -> int:
    """타임라인 시작 타임코드부터 tc까지의 프레임 수 (마커 위치 등에 쓰는 값). 두 값은 같은 규칙으로 센다."""
    drop = is_drop_frame(fps, tc, start_tc, flag=drop_frame)
    return tc_to_frames(tc, fps, drop) - tc_to_frames(start_tc, fps, drop)
