"""자동화 버튼 1·2·3 (설계 B1.1, B2). 2.1a에서는 모양과 연결 확인까지만: 누르면 먼저 연결을 확인하고
"다음 판에서 열려요"라고 알린다. 3번(소리 고르게)은 "준비 중"으로 꺼 둔다.

넓은 화면: 버튼마다 한 줄(60) + ⚙(40×40). 낮은 화면: 세 칸 타일(약 116×68), ⚙는 숨긴다.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QGridLayout, QLabel, QPushButton, QSizePolicy, QToolButton, QVBoxLayout, QWidget

from . import strings_ko as S
from . import theme

READY_KINDS = ("mark_pauses", "mark_spikes")  # 2.1에서 쓸 수 있게 되는 종류 (balance_voice는 2.2)


def slot_summary(slot: Dict[str, Any]) -> str:
    params = dict(slot.get("params") or {})
    template = S.SLOT_SUMMARY.get(slot.get("kind"), "")
    color = params.get("color") or params.get("marker_color")
    params["color_word"] = S.COLOR_WORDS.get(color, color or "")
    try:
        return template.format(**params)
    except (KeyError, IndexError, ValueError):
        return ""


class SlotButton(QPushButton):
    """이름(16/600), 설정 한 줄, 마지막 결과(또는 못 쓰는 이유) 한 줄."""

    def __init__(self, slot: Dict[str, Any], parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setProperty("kind", "slot")
        self.number = int(slot["slot"])
        self.kind = slot.get("kind")
        self.name = slot.get("name") or S.SLOT_DEFAULT_NAMES.get(self.number, "")
        self.ready = self.kind in READY_KINDS
        box = QVBoxLayout(self)
        box.setContentsMargins(10, 4, 10, 4)
        box.setSpacing(0)
        self.title = QLabel(self.name)
        self.title.setProperty("role", "slot-title")
        self.summary = QLabel(slot_summary(slot))
        self.summary.setProperty("role", "secondary")
        self.receipt = QLabel(S.SLOT_NEVER_RUN)
        self.receipt.setProperty("role", "secondary")
        for label in (self.title, self.summary, self.receipt):
            label.setAttribute(Qt.WA_TransparentForMouseEvents)
            label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
            box.addWidget(label)
        self.setAccessibleName(self.name)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.set_mode("rows")

    def set_mode(self, mode: str) -> None:
        tiles = mode != "rows"
        if tiles:
            # 좁은 타일: 이름은 조금 작게(14/600), 이름과 이유는 줄을 바꿔 잘리지 않게
            self.setMinimumSize(theme.TILE_W - 20, theme.TILE_H)
            self.setMaximumHeight(theme.TILE_H + 8)
        else:
            self.setMinimumSize(0, theme.SLOT_ROW_H)
            self.setMaximumHeight(theme.SLOT_ROW_H + 16)
        self.summary.setVisible(not tiles)
        self.title.setProperty("role", "tile-title" if tiles else "slot-title")
        for label in (self.title, self.receipt):
            label.setWordWrap(tiles)
            label.style().unpolish(label)
            label.style().polish(label)

    def set_reason(self, reason: Optional[str]) -> None:
        """None이면 쓸 수 있음, 글이 있으면 꺼 두고 그 이유를 보인다."""
        self.setEnabled(reason is None)
        self.receipt.setText(reason if reason is not None else S.SLOT_NEVER_RUN)
        self.setToolTip(reason or self.summary.text())


class AutomationView(QWidget):
    slot_clicked = Signal(int)
    settings_clicked = Signal(int)

    def __init__(self, slots: List[Dict[str, Any]], parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("automations")
        self.grid = QGridLayout(self)
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setSpacing(6)
        self.mode = "rows"
        self.buttons: List[SlotButton] = []
        self.gears: List[QToolButton] = []
        for slot in slots:
            btn = SlotButton(slot, self)
            btn.clicked.connect(lambda _=False, n=btn.number: self.slot_clicked.emit(n))
            gear = QToolButton(self)
            gear.setText(S.SLOT_SETTINGS)
            gear.setToolTip(S.TIP_SLOT_SETTINGS)
            gear.setAccessibleName(f"{btn.name} {S.TIP_SLOT_SETTINGS}")
            gear.setFixedSize(theme.GEAR, theme.GEAR)
            gear.clicked.connect(lambda _=False, n=btn.number: self.settings_clicked.emit(n))
            self.buttons.append(btn)
            self.gears.append(gear)
        self._place()

    def button(self, number: int) -> SlotButton:
        return next(b for b in self.buttons if b.number == number)

    def set_mode(self, mode: str) -> None:
        if mode != self.mode:
            self.mode = mode
            self._place()

    def _place(self) -> None:
        for w in [*self.buttons, *self.gears]:
            self.grid.removeWidget(w)
        for i, (btn, gear) in enumerate(zip(self.buttons, self.gears)):
            btn.set_mode(self.mode)
            if self.mode == "rows":
                self.grid.addWidget(btn, i, 0)
                self.grid.addWidget(gear, i, 1, Qt.AlignVCenter)
                gear.setVisible(True)
            else:
                self.grid.addWidget(btn, 0, i)
                gear.setVisible(False)  # 좁은 타일에서는 ⚙를 숨긴다 (설정은 넓은 화면이나 다음 판의 메뉴에서)
        for col in range(max(3, len(self.buttons))):
            self.grid.setColumnStretch(col, 1 if self.mode == "tiles" else 0)
        self.grid.setColumnStretch(0, 1)

    def set_reason(self, reason: Optional[str], running: Optional[int] = None) -> None:
        """모든 버튼의 상태. reason이 None이면 준비된 버튼은 켠다. 3번(소리 고르게)은 늘 "준비 중"."""
        for btn in self.buttons:
            if not btn.ready:
                btn.set_reason(S.SLOT_DISABLED_NOT_READY)
                btn.setToolTip(S.TIP_SLOT_NOT_READY)
            elif running is not None and btn.number != running:
                btn.set_reason(S.SLOT_DISABLED_BUSY)
            else:
                btn.set_reason(reason)
