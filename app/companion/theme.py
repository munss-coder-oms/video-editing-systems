"""도우미 창 색과 글꼴 (설계 B1.4). 대비는 UX 조사에서 계산한 값.

Pretendard(OFL)는 아직 앱에 넣지 않았다 (2.1a): 설치되어 있으면 쓰고, 없으면 맑은 고딕.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass(frozen=True)
class Tokens:
    bg: str = "#1e1f22"
    card: str = "#2a2b2f"
    card_pressed: str = "#34363b"
    border: str = "#6b6e75"
    text: str = "#ececec"
    secondary: str = "#a9abb0"
    ok: str = "#4cc26a"
    warning: str = "#f0b429"
    error: str = "#ff7b7b"
    primary: str = "#2563eb"
    primary_text: str = "#ffffff"
    focus: str = "#7fb0ff"


TOKENS = Tokens()
FONT_FAMILIES = ("Pretendard", "Malgun Gothic", "Apple SD Gothic Neo", "Noto Sans CJK KR", "sans-serif")

BODY_PX = 14
SLOT_TITLE_PX = 16
CARD_TITLE_PX = 15
MIN_PX = 12

# 크기 (논리 픽셀)
SLOT_ROW_H = 60
TILE_W, TILE_H = 116, 68
INPUT_H = 44
SEND_W = 72
FOOTER_H = 44
HIT_MIN = 40
GEAR = 40
IN_CARD_H = 32  # 카드 안의 작은 단추 ([−][+], 이동, 바꾸기)와 대화 접기·자세히
SCROLL_W = 10
CHAT_MIN_H = 220
ARROW_FILE = "combo-arrow.png"


def px(size: int, scale: int = 100) -> int:
    return max(MIN_PX, int(round(size * scale / 100)))


def arrow_image(folder: Path, t: Tokens = TOKENS) -> Optional[Path]:
    """고르기 칸(QComboBox)의 ▾ 그림 (어두운 바탕에 밝은 세모). 그리지 못하면 None (글자만 보인다)."""
    try:
        from PySide6.QtCore import QPointF, Qt
        from PySide6.QtGui import QColor, QImage, QPainter, QPolygonF

        folder = Path(folder)
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / ARROW_FILE
        img = QImage(20, 12, QImage.Format_ARGB32)
        img.fill(Qt.transparent)
        painter = QPainter(img)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(t.text))
        painter.drawPolygon(QPolygonF([QPointF(1, 1), QPointF(19, 1), QPointF(10, 11)]))
        painter.end()
        return path if img.save(str(path), "PNG") else None
    except (OSError, ImportError, RuntimeError):
        return None


def stylesheet(scale: int = 100, t: Tokens = TOKENS, arrow: Optional[Path] = None) -> str:
    families = ", ".join(f'"{f}"' if " " in f else f for f in FONT_FAMILIES)
    body, title, small = px(BODY_PX, scale), px(SLOT_TITLE_PX, scale), px(MIN_PX, scale)
    card = px(CARD_TITLE_PX, scale)
    arrow_rule = (f'QComboBox::down-arrow {{ image: url("{Path(arrow).as_posix()}"); width: 10px; height: 6px; }}'
                  if arrow is not None else "")
    return f"""
QMainWindow, QWidget#panel, QWidget#checkPage, QWidget#settingsPage, QStackedWidget {{ background: {t.bg}; }}
QDialog, QMessageBox {{ background: {t.bg}; }}
QWidget {{ color: {t.text}; font-family: {families}; font-size: {body}px; }}
QMessageBox QLabel, QDialog QLabel {{ color: {t.text}; background: transparent; }}
QLabel[role="secondary"] {{ color: {t.secondary}; font-size: {small}px; }}
QLabel[role="status-ok"] {{ color: {t.ok}; font-weight: 600; }}
QLabel[role="status-off"] {{ color: {t.secondary}; font-weight: 600; }}
QLabel[role="status-warn"] {{ color: {t.warning}; font-weight: 600; }}
QLabel[role="warning"] {{ color: {t.warning}; }}
QLabel[role="error"] {{ color: {t.error}; }}
QPushButton, QToolButton {{
    background: {t.card}; color: {t.text}; border: 1px solid {t.border}; border-radius: 6px;
    min-height: {HIT_MIN}px; padding: 0 10px;
}}
QPushButton:pressed, QToolButton:pressed {{ background: {t.card_pressed}; }}
QPushButton:disabled, QToolButton:disabled {{ color: {t.secondary}; border-color: #45474d; }}
QPushButton:focus, QToolButton:focus, QPlainTextEdit:focus, QTextEdit:focus {{ border: 2px solid {t.focus}; }}
QPushButton[kind="primary"] {{ background: {t.primary}; color: {t.primary_text}; border-color: {t.primary}; }}
QPushButton[kind="primary"]:disabled {{ background: {t.card}; color: {t.secondary}; border-color: #45474d; }}
QToolButton::menu-indicator {{ image: none; width: 0px; }}
QPushButton#checkBtn, QToolButton#moreBtn, QToolButton#undoBtn, QPushButton#reportBtn {{
    min-height: {HIT_MIN}px; max-height: {HIT_MIN}px;
}}
QPushButton[kind="slot"] {{ text-align: left; padding: 6px 10px; }}
QPushButton[kind="slot"]:disabled {{ background: {t.bg}; border-style: dashed; }}
QLabel[role="slot-title"] {{ font-size: {title}px; font-weight: 600; }}
QLabel[role="tile-title"] {{ font-size: {body}px; font-weight: 600; }}
QLabel[role="slot-title"]:disabled, QLabel[role="tile-title"]:disabled {{ color: {t.secondary}; }}
QFrame#card, QGroupBox {{ background: {t.card}; border: 1px solid {t.border}; border-radius: 8px; }}
QFrame#card QLabel {{ background: transparent; }}
QLabel[role="card-title"] {{ font-size: {card}px; font-weight: 600; }}
QLabel[role="item"] {{ padding: 2px 0; }}
QToolButton[role="chip"] {{ border-radius: 14px; min-height: 32px; padding: 0 10px; color: {t.focus}; }}
QLabel[role="chip"] {{
    border: 1px solid {t.border}; border-radius: 12px; padding: 4px 10px; color: {t.focus}; background: {t.card};
}}
QToolButton[role="step"], QPushButton[role="step"] {{ min-width: {HIT_MIN - 10}px; padding: 0 4px; }}
QToolButton[role="step"][box="field"] {{
    min-width: {HIT_MIN - 10}px; max-width: {HIT_MIN - 10}px; min-height: {HIT_MIN - 2}px; max-height: {HIT_MIN - 2}px;
}}
QSpinBox[stepped="true"], QDoubleSpinBox[stepped="true"] {{ min-height: {HIT_MIN - 2}px; max-height: {HIT_MIN - 2}px; }}
QToolButton[role="small"], QPushButton[role="small"], QToolButton[role="step"][box="small"] {{
    min-height: {IN_CARD_H}px; max-height: {IN_CARD_H}px; padding: 0 8px;
}}
QToolButton[role="step"][box="small"] {{ padding: 0 4px; }}
QToolButton[role="link"] {{
    background: transparent; border: 1px solid transparent; color: {t.focus};
    min-height: {IN_CARD_H}px; max-height: {IN_CARD_H}px; padding: 0 6px;
}}
QToolButton[role="link"]:focus {{ border: 2px solid {t.focus}; }}
QScrollArea#chatLog {{ background: {t.bg}; border: 1px solid {t.border}; border-radius: 6px; }}
QScrollArea#settingsScroll {{ background: {t.bg}; border: none; }}
QWidget#messages, QWidget#settingsInner {{ background: {t.bg}; }}
QWidget#progressRow {{ background: {t.card}; border: 1px solid {t.border}; border-radius: 6px; }}
QWidget#progressRow QLabel {{ background: transparent; }}
QProgressBar {{ background: {t.card_pressed}; border: none; border-radius: 3px; }}
QProgressBar::chunk {{ background: {t.primary}; border-radius: 3px; }}
QComboBox, QSpinBox, QDoubleSpinBox, QLineEdit {{
    background: {t.card}; color: {t.text}; border: 1px solid {t.border}; border-radius: 6px; min-height: 32px;
    padding: 0 6px;
}}
QComboBox {{ padding: 0 34px 0 8px; }}
QComboBox:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled, QLineEdit:disabled {{ color: {t.secondary}; }}
QComboBox::drop-down {{
    subcontrol-origin: padding; subcontrol-position: center right; width: 28px;
    border: none; border-left: 1px solid {t.border};
    border-top-right-radius: 6px; border-bottom-right-radius: 6px;
}}
{arrow_rule}
QComboBox QAbstractItemView {{
    background: {t.card}; color: {t.text}; border: 1px solid {t.border}; outline: 0;
    selection-background-color: {t.card_pressed}; selection-color: {t.text};
}}
QWidget#settingsPage {{ background: {t.bg}; }}
QPlainTextEdit, QTextEdit, QListWidget {{
    background: {t.card}; color: {t.text}; border: 1px solid {t.border}; border-radius: 6px;
}}
QMenu {{ background: {t.card}; color: {t.text}; border: 1px solid {t.border}; }}
QMenu::item:selected {{ background: {t.card_pressed}; }}
QMenu::item:disabled {{ color: {t.secondary}; }}
QScrollBar:vertical {{ background: transparent; width: {SCROLL_W}px; margin: 0; }}
QScrollBar:horizontal {{ background: transparent; height: {SCROLL_W}px; margin: 0; }}
QScrollBar::handle:vertical {{ background: {t.border}; border-radius: 4px; min-height: 24px; margin: 1px; }}
QScrollBar::handle:horizontal {{ background: {t.border}; border-radius: 4px; min-width: 24px; margin: 1px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0px; height: 0px; border: none; background: none; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: none; }}
"""


def status_role(status: str) -> str:
    if status == "connected":
        return "status-ok"
    if status in ("busy", "old_script"):
        return "status-warn"
    return "status-off"
