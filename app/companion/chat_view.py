"""대화 칸 (설계 B1.1, B1.5). 적어 보내면 아직은 "대화는 곧 열려요"라고 답한다 (대화는 2.1c).
대화는 리졸브에 아무것도 묻지 않는다. 자동화 버튼의 카드(목소리 고르기, 확인 카드, 영수증)도 여기에 뜬다.

- Enter 보내기, Shift+Enter 줄 바꿈, Esc 지우기.
- 한글 입력 중(IME 조합 글자가 있을 때) Enter는 글자만 확정하고 보내지 않는다.
- 입력 칸은 44에서 세 줄까지 커진다. 대화 목록은 접을 수 있다 (입력 칸은 늘 보인다).
"""

from __future__ import annotations

from typing import List, Optional

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QInputMethodEvent, QKeyEvent
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


class MessageList(QScrollArea):
    """대화 목록: 글 한 줄(QLabel) 또는 카드(QWidget)를 아래로 쌓는다.

    예전 QPlainTextEdit처럼 appendPlainText()와 toPlainText()가 있다 (결과 파일과 시험이 쓴다).
    """

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
        QTimer.singleShot(0, self.scroll_to_end)
        return widget

    def scroll_to_end(self) -> None:
        bar = self.verticalScrollBar()
        bar.setValue(bar.maximum())

    def toPlainText(self) -> str:
        out = []
        for w in self.items:
            text = w.plain_text() if hasattr(w, "plain_text") else (w.text() if isinstance(w, QLabel) else "")
            if text:
                out.append(text)
        return "\n".join(out)


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
                # 한글 조합 중: 글자만 확정한다 (보내지 않음)
                super().keyPressEvent(event)
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
        lines = max(1, min(MAX_INPUT_LINES, self.document().blockCount()))
        line_h = self.fontMetrics().lineSpacing()
        extra = theme.INPUT_H - line_h  # 한 줄일 때 44가 되게
        self.setFixedHeight(max(theme.INPUT_H, extra + line_h * lines))


class ChatView(QWidget):
    sent = Signal(str)
    collapsed_changed = Signal(bool)

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("chat")
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(4)
        self.toggle = QToolButton()
        self.toggle.setText(S.CHAT_COLLAPSE)
        self.toggle.setCheckable(True)
        self.toggle.setMinimumHeight(theme.HIT_MIN)
        self.toggle.toggled.connect(self._set_collapsed)
        root.addWidget(self.toggle, 0, Qt.AlignLeft)

        self.log = MessageList()
        self.log.setAccessibleName(S.CHAT_TITLE)
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

        self.brain = QLabel(S.CHAT_BRAIN_LINE)
        self.brain.setProperty("role", "secondary")
        root.addWidget(self.brain)
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

    def set_min_height(self, height: int) -> None:
        self.log.setMinimumHeight(max(40, height))

    def add_line(self, who: str, text: str) -> None:
        self.log.appendPlainText(f"{who}: {text}")

    def add_helper(self, text: str) -> None:
        self.add_line(S.CHAT_HELPER, text)

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
