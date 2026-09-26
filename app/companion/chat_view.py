"""대화 칸 (설계 B1.1, B1.5, B4). 적어 보내면 기본 도우미(chat_flow.py)가 답하고 카드를 띄운다.
자동화 버튼의 카드(목소리 고르기, 확인 카드, 영수증)도 여기에 뜬다.

- Enter 보내기, Shift+Enter 줄 바꿈, Esc 지우기.
- 한글 입력 중(IME 조합 글자가 있을 때) Enter는 글자만 확정하고 보내지 않는다.
- 입력 칸은 44에서 세 줄까지 커진다 (긴 한 줄이 접혀도 센다). 대화 목록은 접을 수 있다 (입력 칸은 늘 보인다).
- 도우미 답 아래의 예문(칩)은 누르면 입력 칸을 채우기만 한다. 보내지 않는다 (고친 뒤 [보내기]).
- 맨 아래 한 줄: "기본 도우미가 답해요 (AI 아님 · 무료)"와 [▾ 대화 접기] (대화 칸 위에 줄을 따로 쓰지 않게).
- 목록은 새 줄이 붙으면 따라 내려간다. 대화 칸보다 큰 카드는 카드 맨 위가 보이게 멈춘다.
  위로 올려 읽는 중이면 따라가지 않는다 (맨 아래로 다시 내리면 다시 따라간다).
- 목록 안쪽 너비는 보이는 너비를 넘지 않는다 (카드가 넓어져 오른쪽이 잘리지 않게).
- 굴리거나 접거나 크기가 바뀌면 view_changed: 창이 머리말 알림과 같은 말이 대화 칸에 보이는지 다시 본다 (shows).
"""

from __future__ import annotations

from typing import List, Optional, Sequence, Tuple

from PySide6.QtCore import QEvent, QPoint, Qt, Signal
from PySide6.QtGui import QGuiApplication, QInputMethodEvent, QKeyEvent
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from . import strings_ko as S
from . import theme

MAX_INPUT_LINES = 3
MAX_MESSAGES = 200  # 이보다 많으면 오래된 것부터 지운다 (카드 포함)
FOLLOW_SLACK = 4  # 맨 아래에서 이만큼 안이면 "맨 아래에 있음" (새 줄을 따라간다)
SHOWN_TOP = 24  # 카드·줄의 윗부분(제목 한 줄)이 이만큼 보이면 "대화 칸에 보임"


class MessageList(QScrollArea):
    """대화 목록: 글 한 줄(QLabel) 또는 카드(QWidget)를 아래로 쌓는다.

    예전 QPlainTextEdit처럼 appendPlainText()와 toPlainText()가 있다 (결과 파일과 시험이 쓴다).
    """

    view_changed = Signal()  # 굴림, 목록 높이·칸 크기가 바뀜

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("chatLog")
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setFrameShape(QFrame.StyledPanel)
        self.inner = QWidget()
        self.inner.setObjectName("messages")
        self.box = QVBoxLayout(self.inner)
        self.box.setContentsMargins(6, 6, 6, 6)
        self.box.setSpacing(6)
        self.box.addStretch(1)
        self.setWidget(self.inner)
        self.items: List[QWidget] = []
        self.follow = True  # 새 줄을 따라 내려감 (사람이 위로 올려 읽는 중이면 False)
        self._anchor: Optional[QWidget] = None  # 방금 붙인 줄: 대화 칸에 다 안 들어가면 그 맨 위에서 멈춘다
        self._moving = False  # 이 목록이 스스로 옮기는 중 (사람이 올린 것과 가르려고)
        bar = self.verticalScrollBar()
        bar.rangeChanged.connect(self._range_changed)
        bar.valueChanged.connect(self._value_changed)

    # ── 너비와 따라가기 ────────────────────────────────────────────────

    def _cap_width(self) -> None:
        """안쪽 너비를 보이는 너비로 묶는다 (넓은 카드가 목록을 옆으로 밀어 오른쪽이 잘리지 않게)."""
        width = self.viewport().width()
        if width > 0 and self.inner.maximumWidth() != width:
            self.inner.setMaximumWidth(width)

    def resizeEvent(self, event) -> None:
        self._cap_width()
        super().resizeEvent(event)

    def viewportEvent(self, event) -> bool:
        if event.type() == QEvent.Resize:
            self._cap_width()
            self.view_changed.emit()
        return super().viewportEvent(event)

    def shows(self, widget: QWidget) -> bool:
        """그 줄(카드)의 윗부분이 지금 보이는 칸 안에 있는지 (제목 한 줄이 가려지지 않고 보임)."""
        if not self.isVisible() or not widget.isVisible() or not self.inner.isAncestorOf(widget):
            return False
        top = widget.mapTo(self.viewport(), QPoint(0, 0)).y()
        need = min(widget.height(), SHOWN_TOP)
        return top >= 0 and top + need <= self.viewport().height()

    def _target(self) -> int:
        bar = self.verticalScrollBar()
        target = bar.maximum()
        w = self._anchor
        if w is not None:
            try:
                # 맨 아래로 가면 새 줄의 맨 위가 가려질 때 (위아래 여백까지 쳐서 칸에 다 안 들어감): 맨 위에서 멈춘다
                if w.parent() is self.inner:
                    target = min(target, max(0, w.y() - self.box.spacing()))
            except RuntimeError:
                self._anchor = None
        return target

    def _reveal(self) -> None:
        bar = self.verticalScrollBar()
        target = self._target()
        if bar.value() != target:
            self._moving = True
            try:
                bar.setValue(target)
            finally:
                self._moving = False

    def _range_changed(self, _lo: int, _hi: int) -> None:
        if self.follow:
            self._reveal()
        self.view_changed.emit()

    def _value_changed(self, value: int) -> None:
        if not self._moving:
            # 사람이 옮김 (휠, 끌기, 키): 맨 아래면 다시 따라가고, 아니면 멈춘다
            self._anchor = None
            self.follow = value >= self.verticalScrollBar().maximum() - FOLLOW_SLACK
        self.view_changed.emit()

    def appendPlainText(self, text: str) -> QLabel:
        label = QLabel(text)
        label.setWordWrap(True)
        label.setTextInteractionFlags(Qt.TextSelectableByMouse | Qt.TextSelectableByKeyboard)
        label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.add_widget(label)
        return label

    def add_widget(self, widget: QWidget) -> QWidget:
        self.box.addWidget(widget)
        self.items.append(widget)
        while len(self.items) > MAX_MESSAGES:
            old = self.items.pop(0)
            self.box.removeWidget(old)
            old.deleteLater()
        # 새 줄은 늘 보여 준다 (위로 올려 읽던 중이어도: 사람이 방금 보냈거나 답을 기다리는 카드다).
        # 옮기는 것은 목록 높이가 바뀐 뒤(rangeChanged)에 한다: 그때라야 새 줄의 자리와 높이를 안다
        self.follow = True
        self._anchor = widget
        return widget

    def scroll_to_end(self) -> None:
        self.follow = True
        self._anchor = None
        self._reveal()

    def toPlainText(self) -> str:
        out = []
        for w in self.items:
            text = w.plain_text() if hasattr(w, "plain_text") else (w.text() if isinstance(w, QLabel) else "")
            if text:
                out.append(text)
        return "\n".join(out)


class ChipLabel(QLabel):
    """예문 칩 하나 (좁은 창에서도 줄을 바꿔 다 보이게 QLabel로). 누르면 pressed(채울 글)."""

    pressed = Signal(str)

    def __init__(self, label: str, fill: str, parent: Optional[QWidget] = None) -> None:
        super().__init__(label, parent)
        self.fill = fill
        self.setWordWrap(True)
        self.setProperty("role", "chip")
        self.setCursor(Qt.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.setFocusPolicy(Qt.TabFocus)
        self.setAccessibleName(label)

    def click(self) -> None:
        self.pressed.emit(self.fill)

    def mouseReleaseEvent(self, event) -> None:
        super().mouseReleaseEvent(event)
        if event.button() == Qt.LeftButton:
            self.click()

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() in (Qt.Key_Return, Qt.Key_Enter, Qt.Key_Space):
            self.click()
            return
        super().keyPressEvent(event)


class ChipRow(QWidget):
    """도우미 답 아래의 예문들 (세로로, 한 줄에 하나)."""

    pressed = Signal(str)

    def __init__(self, chips: List[Tuple[str, str]], parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        box = QVBoxLayout(self)
        box.setContentsMargins(12, 0, 0, 0)
        box.setSpacing(4)
        self.chips: List[ChipLabel] = []
        for label, fill in chips:
            chip = ChipLabel(label, fill)
            chip.pressed.connect(self.pressed.emit)
            box.addWidget(chip)
            self.chips.append(chip)

    def plain_text(self) -> str:
        return "\n".join(f"[{c.text()}]" for c in self.chips)


class ChatInput(QPlainTextEdit):
    send_requested = Signal()

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.preedit = ""  # IME가 아직 조합 중인 글자
        self.setPlaceholderText(S.CHAT_PLACEHOLDER)
        self.setAccessibleName(S.CHAT_PLACEHOLDER)
        self.setTabChangesFocus(True)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setFixedHeight(theme.INPUT_H)
        self.textChanged.connect(self._fit)

    def inputMethodEvent(self, event: QInputMethodEvent) -> None:
        self.preedit = event.preeditString()
        super().inputMethodEvent(event)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        key = event.key()
        if key in (Qt.Key_Return, Qt.Key_Enter):
            if self.preedit:
                # 한글 조합 중: 글자만 확정한다 (보내지도, 줄을 바꾸지도 않는다). 확정은 입력기에 맡긴다
                QGuiApplication.inputMethod().commit()
                event.accept()
                return
            if event.modifiers() & Qt.ShiftModifier:
                self.insertPlainText("\n")
                return
            self.send_requested.emit()
            return
        if key == Qt.Key_Escape and not self.preedit:
            self.clear()
            return
        super().keyPressEvent(event)

    def _fit(self) -> None:
        # 보이는 줄 수 (긴 한 문단이 접혀 두 줄이 되면 두 줄): QPlainTextDocumentLayout의 높이는 줄 수다
        lines = int(round(self.document().size().height())) or self.document().blockCount()
        lines = max(1, min(MAX_INPUT_LINES, lines))
        line_h = self.fontMetrics().lineSpacing()
        extra = theme.INPUT_H - line_h  # 한 줄일 때 44가 되게
        height = max(theme.INPUT_H, extra + line_h * lines)
        if height != self.height():
            self.setFixedHeight(height)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._fit()  # 창 너비가 바뀌면 접히는 줄 수도 바뀐다


class ChatView(QWidget):
    sent = Signal(str)
    collapsed_changed = Signal(bool)
    view_changed = Signal()  # 대화 목록을 굴리거나 접음 (보이는 줄이 바뀜)

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("chat")
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(4)
        self.toggle = QToolButton()
        self.toggle.setText(S.CHAT_COLLAPSE)
        self.toggle.setCheckable(True)
        self.toggle.setProperty("role", "link")
        self.toggle.toggled.connect(self._set_collapsed)

        self.log = MessageList()
        self.log.setAccessibleName(S.CHAT_TITLE)
        self.log.view_changed.connect(self.view_changed)
        self.last_line: Optional[QLabel] = None  # 마지막 글 줄 (머리말 알림과 같은 말인지 창이 본다)
        self.log.setMinimumHeight(60)
        self.log.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        root.addWidget(self.log, 1)

        row = QHBoxLayout()
        row.setSpacing(6)
        self.input = ChatInput()
        self.send_btn = QPushButton(S.CHAT_SEND)
        self.send_btn.setFixedSize(theme.SEND_W, theme.INPUT_H)
        self.send_btn.clicked.connect(self.send)
        self.input.send_requested.connect(self.send)
        row.addWidget(self.input, 1)
        row.addWidget(self.send_btn, 0, Qt.AlignBottom)
        root.addLayout(row)

        self.last_chips: Optional[ChipRow] = None
        # 맨 아래 한 줄: 누가 답하는지 + [▾ 대화 접기]
        bottom = QHBoxLayout()
        bottom.setContentsMargins(0, 0, 0, 0)
        bottom.setSpacing(6)
        self.brain = QLabel(S.CHAT_BRAIN_LINE)
        self.brain.setProperty("role", "secondary")
        self.brain.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        bottom.addWidget(self.brain, 1)
        bottom.addWidget(self.toggle, 0, Qt.AlignRight | Qt.AlignVCenter)
        root.addLayout(bottom)
        self.add_helper(S.CHAT_GREETING)

    @property
    def collapsed(self) -> bool:
        return self.toggle.isChecked()

    def set_collapsed(self, collapsed: bool) -> None:
        self.toggle.setChecked(collapsed)

    def _set_collapsed(self, collapsed: bool) -> None:
        self.toggle.setText(S.CHAT_EXPAND if collapsed else S.CHAT_COLLAPSE)
        self.log.setVisible(not collapsed)
        self.brain.setVisible(not collapsed)
        self.collapsed_changed.emit(collapsed)
        self.view_changed.emit()

    def set_min_height(self, height: int) -> None:
        self.log.setMinimumHeight(max(40, height))

    def add_line(self, who: str, text: str) -> QLabel:
        self.last_line = self.log.appendPlainText(f"{who}: {text}")
        return self.last_line

    def shows(self, widget: QWidget) -> bool:
        """그 카드·줄의 윗부분이 대화 칸에 보이는지 (대화를 접었으면 아니다)."""
        return not self.collapsed and self.log.shows(widget)

    def add_helper(self, text: str, chips: Optional[Sequence[Tuple[str, str]]] = None) -> Optional[ChipRow]:
        """도우미 한 줄. chips: (보이는 글, 입력 칸에 채울 글) 목록."""
        self.add_line(S.CHAT_HELPER, text)
        if not chips:
            return None
        row = ChipRow(list(chips))
        row.pressed.connect(self.fill)
        self.log.add_widget(row)
        self.last_chips = row
        if self.collapsed:
            self.set_collapsed(False)
        return row

    def fill(self, text: str) -> None:
        """칩을 누름: 입력 칸만 채운다 (보내지 않는다)."""
        self.input.setPlainText(text)
        self.input.moveCursor(self.input.textCursor().MoveOperation.End)
        self.input.setFocus()

    def add_card(self, card: QWidget) -> QWidget:
        """카드를 목록 아래에 붙인다. 대화를 접어 두었으면 편다 (카드는 답을 기다리므로)."""
        self.log.add_widget(card)
        if self.collapsed:
            self.set_collapsed(False)
        return card

    def send(self) -> None:
        text = self.input.toPlainText().strip()
        if not text:
            return
        self.input.clear()
        self.add_line(S.CHAT_ME, text)
        self.sent.emit(text)
