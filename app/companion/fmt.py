"""화면에 쓰는 숫자·시간 모양 (글은 strings_ko.py의 틀을 쓴다).

- num(2.0) → "2", num(1.5) → "1.5": 버튼 요약 "2초 넘게 쉰 곳"이 "2.0초"가 되지 않게.
- length(72.4) → "1분 12초", length(120) → "2분".
- clock_time("14:02") → "오후 2:02" (영수증의 벽시계 시각).
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
        return S.LEN_HOUR_MIN.format(h=h, m=m) if m else S.LEN_HOUR.format(h=h)
    if m:
        return S.LEN_MIN_SEC.format(m=m, s=s) if s else S.LEN_MIN.format(m=m)
    return S.LEN_SEC.format(s=s)


def clock_time(at: Any) -> str:
    """벽시계 시각 "14:02", "14:02:10", "2026-09-26T14:02:10+0900" → "오후 2:02" (영수증·되돌리기 목록)."""
    text = str(at or "")
    if "T" in text:
        text = text.split("T", 1)[1]
    parts = text.split(":")
    try:
        hour, minute = int(parts[0]), int(parts[1][:2])
    except (IndexError, ValueError):
        return str(at or "")
    h12 = hour % 12 or 12
    tmpl = S.CLOCK_AM if hour < 12 else S.CLOCK_PM
    return tmpl.format(h=h12, mm=f"{minute:02d}")


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


def spoken(seconds: Optional[float]) -> str:
    """말하듯 쓴 시간: 3800 → "1시간 3분 20초", 200.5 → "3분 20.5초" (입력 칸에 다시 채울 때)."""
    if seconds is None:
        return ""
    tenths = int(round(max(0.0, float(seconds)) * 10))
    whole, frac = divmod(tenths, 10)
    h, rest = divmod(whole, 3600)
    m, s = divmod(rest, 60)
    sec = f"{s}.{frac}" if frac else str(s)
    if h:
        return S.SPOKEN_HMS.format(h=h, m=m, s=sec)
    if m:
        return S.SPOKEN_MS.format(m=m, s=sec)
    return S.SPOKEN_S.format(s=sec)
