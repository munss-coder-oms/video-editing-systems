"""리졸브 타임라인 흉내 그림 (약 840×120): 가짜 리졸브가 실제로 받은 표시를 그 프레임에 그린다.

리졸브 화면이 아니다. 도우미가 표시를 어디에 넣었는지(또는 뺐는지) 보이려는 그림이라, 가짜 리졸브의
표시 목록을 그대로 읽어 그린다 (보낸 것과 그림이 어긋날 수 없게).

- 위: 이름표 "리졸브 타임라인 (흉내)"와 표시 수 (도우미 표시, 옛 시험 표시, 직접 찍은 표시).
- 눈금: 타임라인의 타임코드(01:00:00:00부터)와 분.
- 표시: 리졸브 표시 색. 길이 있는 표시는 막대. 도우미 표시(aih)는 채운 깃발, 직접 찍은 표시는 속이 빈 깃발.
- 재생 위치: 흰 세로줄.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Iterable, Optional, Tuple

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPen, QPolygonF

from engine.resolve_link.timecode import frames_to_tc, tc_to_frames

WIDTH, HEIGHT = 840, 120
PAD = 14
LABEL = "리졸브 타임라인 (흉내)"
COUNTS = "도우미 표시 {ours}개 · 직접 찍은 표시 {user}개"
COUNTS_LEGACY = "도우미 표시 {ours}개 · 옛 시험 표시 {legacy}개 · 직접 찍은 표시 {user}개"
LEGEND_OURS = "도우미가 넣은 표시"
LEGEND_USER = "직접 찍은 표시"
LEGEND_PLAYHEAD = "재생 위치 {tc}"
MINUTE = "{m}분"

# 리졸브 표시 색 (화면에서 보이는 색에 가깝게)
MARKER_RGB = {
    "Blue": "#2f7ff5", "Cyan": "#22c3d6", "Green": "#36b35a", "Yellow": "#f2c230", "Red": "#e5484d",
    "Pink": "#f06bb0", "Purple": "#9b5de5", "Fuchsia": "#d633b0", "Rose": "#f2798a", "Lavender": "#b39ddb",
    "Sky": "#7cc4f2", "Mint": "#5fd3b0", "Lemon": "#e8f06a", "Sand": "#c8a77a", "Cocoa": "#8d6446",
    "Cream": "#f1e6c8",
}
BG, RULER_BG, LANE_BG = "#16171a", "#1f2125", "#25272c"
TEXT, MUTED, TICK = "#e4e6eb", "#9a9ea8", "#5d616b"
KOREAN = ["Noto Sans CJK KR", "Malgun Gothic", "Apple SD Gothic Neo", "Noto Sans KR", "sans-serif"]
MONO = ["IBM Plex Mono", "DejaVu Sans Mono", "Consolas", "Menlo", "monospace"]


def _font(families, px: float, bold: bool = False, mono: bool = False) -> QFont:
    f = QFont()
    f.setFamilies(list(families))
    f.setPixelSize(max(1, int(round(px))))
    f.setBold(bold)
    if mono:
        f.setStyleHint(QFont.Monospace)
    return f


def is_ours(marker: Dict[str, Any]) -> bool:
    """도우미가 넣은 표시 (지금 꼬리표 aih:… 와 1차 시험판의 aih_test)."""
    return str(marker.get("custom") or "").startswith("aih")


def is_legacy(marker: Dict[str, Any]) -> bool:
    return str(marker.get("custom") or "") == "aih_test"


def playhead_frame(info: Dict[str, Any]) -> Optional[int]:
    """재생 위치(current_tc)를 타임라인 시작부터 센 프레임으로."""
    try:
        fps, drop = info.get("fps"), bool(info.get("drop_frame"))
        return tc_to_frames(info["current_tc"], fps, drop) - tc_to_frames(info["start_tc"], fps, drop)
    except (KeyError, TypeError, ValueError):
        return None


def render(markers: Dict[int, Dict[str, Any]], info: Dict[str, Any], path: Path, *,
           scale: int = 2) -> Tuple[int, int]:
    """표시 목록({프레임: 표시})과 타임라인 정보로 PNG를 쓴다. 돌려주는 값은 화면에 놓을 크기 (논리 픽셀)."""
    fps_text = info.get("fps") or "60"
    fps = float(fps_text)
    length = max(1, int(info["end_frame"]) - int(info["start_frame"]))
    start_base = tc_to_frames(info.get("start_tc") or "01:00:00:00", fps_text, bool(info.get("drop_frame")))
    img = QImage(WIDTH * scale, HEIGHT * scale, QImage.Format_ARGB32_Premultiplied)
    img.setDevicePixelRatio(scale)
    img.fill(QColor(BG))
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing)
    p.setRenderHint(QPainter.TextAntialiasing)
    span = WIDTH - 2 * PAD

    def x_of(frame: float) -> float:
        return PAD + max(0.0, min(1.0, frame / length)) * span

    legacy = sum(1 for m in markers.values() if is_legacy(m))
    ours = sum(1 for m in markers.values() if is_ours(m)) - legacy
    user = len(markers) - ours - legacy
    counts = (COUNTS_LEGACY if legacy else COUNTS).format(ours=ours, legacy=legacy, user=user)
    # 이름표와 표시 수
    p.setPen(QColor(TEXT))
    p.setFont(_font(KOREAN, 12, bold=True))
    p.drawText(QRectF(PAD, 3, 300, 18), Qt.AlignLeft | Qt.AlignVCenter, LABEL)
    p.setPen(QColor(MUTED))
    p.setFont(_font(KOREAN, 11))
    p.drawText(QRectF(WIDTH - PAD - 520, 3, 520, 18), Qt.AlignRight | Qt.AlignVCenter, counts)

    # 눈금: 1분마다 타임코드, 10초마다 짧은 눈금
    ruler = QRectF(PAD, 24, span, 22)
    p.fillRect(ruler, QColor(RULER_BG))
    lane = QRectF(PAD, 46, span, 36)
    p.fillRect(lane, QColor(LANE_BG))
    step = int(round(fps * 10))
    p.setFont(_font(MONO, 9.5, mono=True))
    for i, frame in enumerate(range(0, length + 1, step)):
        x = x_of(frame)
        major = i % 6 == 0
        p.setPen(QPen(QColor(TICK if not major else MUTED), 1))
        p.drawLine(QPointF(x, 46 - (9 if major else 4)), QPointF(x, 46))
        if major:
            tc = frames_to_tc(start_base + frame, fps_text, bool(info.get("drop_frame")))
            if x + 66 <= WIDTH - PAD + 2:
                p.setPen(QColor(MUTED))
                p.drawText(QRectF(x + 3, 25, 80, 12), Qt.AlignLeft | Qt.AlignVCenter, tc)
            p.setPen(QColor(MUTED))
            p.setFont(_font(KOREAN, 10))
            p.drawText(QRectF(x - 20, 83, 40, 13), Qt.AlignHCenter | Qt.AlignVCenter,
                       MINUTE.format(m=int(frame // (fps * 60))))
            p.setFont(_font(MONO, 9.5, mono=True))

    # 표시: 길이 있는 것은 막대, 시작 자리에 깃발
    for frame, m in sorted(markers.items(), key=lambda kv: (is_ours(kv[1]), kv[0])):
        color = QColor(MARKER_RGB.get(str(m.get("color")), "#cccccc"))
        x0 = x_of(frame)
        dur = max(1, int(m.get("duration") or 1))
        if dur > 1:
            x1 = max(x0 + 2.5, x_of(frame + dur))
            p.fillRect(QRectF(x0, 49, x1 - x0, 8), color)
        line = QColor(color)
        line.setAlpha(170)
        pen = QPen(line, 1.2)
        if not is_ours(m):
            pen.setStyle(Qt.DashLine)
        p.setPen(pen)
        p.drawLine(QPointF(x0, 58), QPointF(x0, 81))
        flag = QPolygonF([QPointF(x0 - 4, 47), QPointF(x0 + 4, 47), QPointF(x0 + 4, 55), QPointF(x0, 59),
                          QPointF(x0 - 4, 55)])
        if is_ours(m):
            p.setPen(QPen(QColor(BG), 0.8))
            p.setBrush(color)
        else:
            p.setPen(QPen(color, 1.6))
            p.setBrush(QColor(LANE_BG))
        p.drawPolygon(flag)
        p.setBrush(Qt.NoBrush)

    # 재생 위치
    ph = playhead_frame(info)
    tc_text = info.get("current_tc") or ""
    if ph is not None and 0 <= ph <= length:
        x = x_of(ph)
        p.setPen(QPen(QColor("#f5f6f8"), 1.5))
        p.drawLine(QPointF(x, 36), QPointF(x, 82))
        p.setBrush(QColor("#f5f6f8"))
        p.setPen(Qt.NoPen)
        p.drawPolygon(QPolygonF([QPointF(x - 4, 34), QPointF(x + 4, 34), QPointF(x, 40)]))
        p.setBrush(Qt.NoBrush)

    # 범례
    y = 100
    p.setFont(_font(KOREAN, 11))
    x = float(PAD)
    for filled, text in ((True, LEGEND_OURS), (False, LEGEND_USER)):
        swatch = QRectF(x, y + 2, 9, 9)
        if filled:
            p.fillRect(swatch, QColor(MARKER_RGB["Blue"]))
        else:
            p.setPen(QPen(QColor(MARKER_RGB["Blue"]), 1.4))
            p.drawRect(swatch)
        p.setPen(QColor(MUTED))
        w = p.fontMetrics().horizontalAdvance(text)
        p.drawText(QRectF(x + 14, y - 1, w + 4, 15), Qt.AlignLeft | Qt.AlignVCenter, text)
        x += 14 + w + 18
    p.setPen(QPen(QColor("#f5f6f8"), 1.5))
    p.drawLine(QPointF(x + 4, y), QPointF(x + 4, y + 13))
    p.setPen(QColor(MUTED))
    p.drawText(QRectF(x + 12, y - 1, 300, 15), Qt.AlignLeft | Qt.AlignVCenter,
               LEGEND_PLAYHEAD.format(tc=tc_text))
    p.end()
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    if not img.save(str(path), "PNG"):
        raise OSError(f"그림을 저장하지 못했어요: {path}")
    return WIDTH, HEIGHT


def colors_of(markers: Dict[int, Dict[str, Any]], ours_only: bool = True) -> Iterable[str]:
    return sorted({str(m.get("color")) for m in markers.values() if is_ours(m) or not ours_only})
