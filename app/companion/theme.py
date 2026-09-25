"""도우미 창 색과 글꼴 (설계 B1.4). 대비는 UX 조사에서 계산한 값.

Pretendard(OFL)는 아직 앱에 넣지 않았다 (2.1a): 설치되어 있으면 쓰고, 없으면 맑은 고딕.
"""

from __future__ import annotations

from dataclasses import dataclass


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
MIN_PX = 12

# 크기 (논리 픽셀)
SLOT_ROW_H = 60
TILE_W, TILE_H = 116, 68
INPUT_H = 44
SEND_W = 72
FOOTER_H = 44
HIT_MIN = 40
GEAR = 40
CHAT_MIN_H = 220


def px(size: int, scale: int = 100) -> int:
    return max(MIN_PX, int(round(size * scale / 100)))


def stylesheet(scale: int = 100, t: Tokens = TOKENS) -> str:
    families = ", ".join(f'"{f}"' if " " in f else f for f in FONT_FAMILIES)
    body, title, small = px(BODY_PX, scale), px(SLOT_TITLE_PX, scale), px(MIN_PX, scale)
    return f"""
QMainWindow, QWidget#panel, QWidget#checkPage, QStackedWidget {{ background: {t.bg}; }}
QWidget {{ color: {t.text}; font-family: {families}; font-size: {body}px; }}
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
QPushButton[kind="slot"] {{ text-align: left; padding: 6px 10px; }}
QLabel[role="slot-title"] {{ font-size: {title}px; font-weight: 600; }}
QLabel[role="tile-title"] {{ font-size: {body}px; font-weight: 600; }}
QFrame#card, QGroupBox {{ background: {t.card}; border: 1px solid {t.border}; border-radius: 8px; }}
QPlainTextEdit, QTextEdit, QListWidget {{
    background: {t.card}; color: {t.text}; border: 1px solid {t.border}; border-radius: 6px;
}}
QMenu {{ background: {t.card}; color: {t.text}; border: 1px solid {t.border}; }}
QMenu::item:selected {{ background: {t.card_pressed}; }}
QMenu::item:disabled {{ color: {t.secondary}; }}
"""


def status_role(status: str) -> str:
    if status == "connected":
        return "status-ok"
    if status in ("busy", "old_script"):
        return "status-warn"
    return "status-off"
