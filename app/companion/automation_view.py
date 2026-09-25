"""자동화 버튼 1·2·3 (설계 B1.1, B1.2, B2).

버튼마다 이름, 설정 요약 한 줄, 마지막 결과(또는 못 쓰는 이유) 한 줄. 준비 중인 일(소리 고르게 등)은
"준비 중"으로 꺼 두고 풍선 도움말로 까닭을 적는다.

넓은 화면: 버튼마다 한 줄(60) + ⚙(40×40). 낮은 화면: 세 칸 타일(약 116×68), ⚙는 숨긴다.
계산하는 동안에는 그 버튼 자리가 진행 줄([멈추기] 포함)로 바뀐다 (타일이면 타일 아래 한 줄).
진행 줄은 대화 칸을 접어도 버튼 자리에 늘 보이므로, 설계의 "접었을 때 머리말의 작은 진행 표시"는 따로 두지 않았다.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from engine.automation.kinds import is_ready

from . import fmt
from . import strings_ko as S
from . import theme


def kind_name(kind: Optional[str]) -> str:
    return S.KIND_NAMES.get(kind or "", kind or "")


def slot_summary(slot: Dict[str, Any]) -> str:
    """버튼 아래 요약 한 줄 (숫자는 2.0 → 2)."""
    kind = slot.get("kind")
    params = dict(slot.get("params") or {})
    template = S.SLOT_SUMMARY.get(kind or "")
    if template is None:
        return S.SLOT_SUMMARY_NOT_READY.format(name=kind_name(kind)) if kind else ""
    color = params.get("color") or params.get("marker_color")
    values = {k: fmt.num(v) for k, v in params.items()}
    values["color_word"] = fmt.color_word(color)
    try:
        text = template.format(**values)
    except (KeyError, IndexError, ValueError):
        return ""
    if params.get("scope") == "in_out":
        text += S.SLOT_SCOPE_IN_OUT
    return text


class SlotButton(QPushButton):
    """이름(16/600), 설정 한 줄, 마지막 결과(또는 못 쓰는 이유) 한 줄."""

    def __init__(self, slot: Dict[str, Any], parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setProperty("kind", "slot")
        self.number = int(slot["slot"])
        self.kind = slot.get("kind")
        self.name = slot.get("name") or S.SLOT_DEFAULT_NAMES.get(self.number, "")
        self.ready = is_ready(self.kind)
        self.last_receipt: Optional[str] = None
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
        """None이면 쓸 수 있음(마지막 결과를 보인다), 글이 있으면 꺼 두고 그 이유를 보인다."""
        self.setEnabled(reason is None)
        self.receipt.setText(reason if reason is not None else (self.last_receipt or S.SLOT_NEVER_RUN))
        self.setToolTip(reason or self.summary.text())


class ProgressRow(QWidget):
    """계산·넣기 진행: 한 줄 글, 얇은 막대, [멈추기] (멈출 수 없는 일이면 숨긴다)."""

    stop_clicked = Signal()

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("progressRow")
        box = QVBoxLayout(self)
        box.setContentsMargins(10, 4, 4, 4)
        box.setSpacing(2)
        top = QHBoxLayout()
        top.setSpacing(6)
        self.label = QLabel("")
        self.label.setWordWrap(True)
        self.label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.stop_btn = QPushButton(S.BTN_STOP)
        self.stop_btn.setToolTip(S.TIP_STOP)
        self.stop_btn.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)
        self.stop_btn.clicked.connect(self.stop_clicked)
        top.addWidget(self.label, 1)
        top.addWidget(self.stop_btn)
        box.addLayout(top)
        self.bar = QProgressBar()
        self.bar.setRange(0, 1000)
        self.bar.setTextVisible(False)
        self.bar.setFixedHeight(6)
        box.addWidget(self.bar)
        self.setMinimumHeight(theme.SLOT_ROW_H - 8)

    def set_progress(self, text: str, frac: Optional[float], can_stop: bool) -> None:
        self.label.setText(text)
        if frac is None:
            self.bar.setRange(0, 0)  # 얼마나 남았는지 모름: 움직이는 막대
        else:
            self.bar.setRange(0, 1000)
            self.bar.setValue(int(max(0.0, min(1.0, frac)) * 1000))
        self.stop_btn.setVisible(can_stop)
        self.stop_btn.setEnabled(can_stop)


class AutomationView(QWidget):
    slot_clicked = Signal(int)
    settings_clicked = Signal(int)
    stop_clicked = Signal()

    def __init__(self, slots: List[Dict[str, Any]], parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("automations")
        self.grid = QGridLayout(self)
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setSpacing(6)
        self.mode = "rows"
        self.buttons: List[SlotButton] = []
        self.gears: List[QToolButton] = []
        self.progress = ProgressRow(self)
        self.progress.stop_clicked.connect(self.stop_clicked)
        self.progress.setVisible(False)
        self.running: Optional[int] = None
        self._reason: Optional[str] = None
        self.set_slots(slots)

    def set_slots(self, slots: List[Dict[str, Any]]) -> None:
        """설정이 바뀌면 (⚙ 저장, 순서 바꾸기) 버튼을 새로 만든다. 마지막 결과 줄은 그대로 둔다."""
        receipts = {b.number: b.last_receipt for b in self.buttons}
        for w in [*self.buttons, *self.gears]:
            self.grid.removeWidget(w)
            w.hide()
            w.deleteLater()
        self.buttons, self.gears = [], []
        for slot in slots:
            btn = SlotButton(slot, self)
            btn.last_receipt = receipts.get(btn.number)
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
        self.set_reason(self._reason, self.running)

    def button(self, number: int) -> SlotButton:
        return next(b for b in self.buttons if b.number == number)

    def set_mode(self, mode: str) -> None:
        if mode != self.mode:
            self.mode = mode
            self._place()

    def _place(self) -> None:
        for w in [*self.buttons, *self.gears, self.progress]:
            self.grid.removeWidget(w)
        n = len(self.buttons)
        for i, (btn, gear) in enumerate(zip(self.buttons, self.gears)):
            btn.set_mode(self.mode)
            running = self.running == btn.number and self.progress.isVisibleTo(self)
            if self.mode == "rows":
                if running:
                    # 계산하는 버튼 자리가 진행 줄로 바뀐다 (⚙는 그대로 누를 수 있다)
                    btn.setVisible(False)
                    self.grid.addWidget(self.progress, i, 0)
                else:
                    btn.setVisible(True)
                    self.grid.addWidget(btn, i, 0)
                self.grid.addWidget(gear, i, 1, Qt.AlignVCenter)
                gear.setVisible(True)
            else:
                btn.setVisible(True)
                self.grid.addWidget(btn, 0, i)
                gear.setVisible(False)  # 좁은 타일에서는 ⚙를 숨긴다 (설정은 넓은 화면에서)
        if self.mode != "rows" and self.progress.isVisibleTo(self):
            self.grid.addWidget(self.progress, 1, 0, 1, max(1, n))
        for col in range(max(3, n)):
            self.grid.setColumnStretch(col, 1 if self.mode == "tiles" else 0)
        self.grid.setColumnStretch(0, 1)

    def set_reason(self, reason: Optional[str], running: Optional[int] = None) -> None:
        """모든 버튼의 상태. reason이 None이면 준비된 버튼은 켠다. 준비 중인 일은 늘 "준비 중"."""
        self._reason = reason
        for btn in self.buttons:
            if not btn.ready:
                btn.set_reason(S.SLOT_DISABLED_NOT_READY)
                btn.setToolTip(S.TIP_SLOT_NOT_READY.format(name=kind_name(btn.kind)))
            elif running is not None and btn.number == running:
                btn.set_reason(S.SLOT_RUNNING)
            elif running is not None:
                btn.set_reason(S.SLOT_DISABLED_BUSY)
            else:
                btn.set_reason(reason)

    def set_receipt(self, number: int, text: Optional[str]) -> None:
        for btn in self.buttons:
            if btn.number == number:
                btn.last_receipt = text
                if btn.isEnabled():
                    btn.receipt.setText(text or S.SLOT_NEVER_RUN)

    def show_progress(self, number: Optional[int], text: str, frac: Optional[float], can_stop: bool) -> None:
        was = (self.running, self.progress.isVisibleTo(self))
        self.running = number
        self.progress.set_progress(text, frac, can_stop)
        self.progress.setVisible(True)
        if was != (number, True):
            self._place()

    def hide_progress(self) -> None:
        if self.progress.isVisibleTo(self) or self.running is not None:
            self.running = None
            self.progress.setVisible(False)
            self._place()
