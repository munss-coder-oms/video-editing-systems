"""화면에 쓰는 숫자·시간 모양 (글은 strings_ko.py의 틀을 쓴다).

- num(2.0) → "2", num(1.5) → "1.5": 버튼 요약 "2초 넘게 쉰 곳"이 "2.0초"가 되지 않게.
- length(72.4) → "1분 12초".
- clock(200.5) → "3:20.5" (타임라인 시작부터, 0.1초까지).
- tc(프레임, fps, 드롭 프레임) → 리졸브 타임코드 "01:03:20:30".
"""

from __future__ import annotations

from typing import Any, Optional

from engine.resolve_link.timecode import frames_to_tc

from . import strings_ko as S


def num(value: Any) -> str:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return str(value)
    v = round(float(value), 2)
    if v == int(v):
        return str(int(v))
    return f"{v:.2f}".rstrip("0").rstrip(".")


def length(seconds: Optional[float]) -> str:
    if seconds is None:
        return ""
    total = int(round(max(0.0, float(seconds))))
    h, rest = divmod(total, 3600)
    m, s = divmod(rest, 60)
    if h:
        return S.LEN_HOUR_MIN.format(h=h, m=m)
    if m:
        return S.LEN_MIN_SEC.format(m=m, s=s)
    return S.LEN_SEC.format(s=s)


def clock(seconds: float) -> str:
    tenths = int(round(max(0.0, float(seconds)) * 10))
    whole, frac = divmod(tenths, 10)
    h, rest = divmod(whole, 3600)
    m, s = divmod(rest, 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}.{frac}"
    return f"{m}:{s:02d}.{frac}"


def tc(frame: int, fps: Any, drop_frame: Any = False) -> str:
    try:
        return frames_to_tc(int(frame), fps, bool(drop_frame))
    except (ValueError, TypeError):
        return ""


def color_word(color: Optional[str]) -> str:
    return S.COLOR_WORDS.get(color or "", color or "")
