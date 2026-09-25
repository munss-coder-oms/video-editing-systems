"""⋯ > 연결 점검 쪽 (설계 B1.1): 예전 창의 시험 도구(표시 찍기·소리 넣기·시험 흔적 지우기)와 [기능 점검].

[기능 점검]은 확인을 받은 뒤에만 한다 (점검용 복사본을 만들었다 지움). [남은 점검용 복사본 지우기]는
점검 기록(probe_state.json)이 있을 때만 보이고, 누르면 다시 한 번 묻는다.
"""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPlainTextEdit, QPushButton, QVBoxLayout, QWidget

from . import strings_ko as S


class CheckPage(QWidget):
    back_clicked = Signal()

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("checkPage")
        root = QVBoxLayout(self)
        root.setSpacing(6)
        top = QHBoxLayout()
        self.back_btn = QPushButton(S.BTN_BACK)
        self.back_btn.clicked.connect(self.back_clicked)
        self.title = QLabel(S.CHECK_TITLE)
        self.title.setProperty("role", "slot-title")
        top.addWidget(self.back_btn)
        top.addWidget(self.title, 1)
        root.addLayout(top)

        intro = QLabel(S.CHECK_INTRO)
        intro.setWordWrap(True)
        intro.setProperty("role", "secondary")
        root.addWidget(intro)

        self.probe_btn = self._button(root, S.BTN_PROBE, S.TIP_PROBE)
        self.probe_btn.setProperty("kind", "primary")
        self.marker_btn = self._button(root, S.BTN_TEST_MARKER, S.TIP_TEST_MARKER)
        self.audio_btn = self._button(root, S.BTN_TEST_AUDIO, S.TIP_TEST_AUDIO)
        self.cleanup_btn = self._button(root, S.BTN_TEST_CLEANUP, S.TIP_TEST_CLEANUP)
        self.leftover_btn = self._button(root, S.BTN_DELETE_LEFTOVER, "")
        self.leftover_btn.setVisible(False)

        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setPlaceholderText(S.LOG_PLACEHOLDER)
        self.log_view.setTextInteractionFlags(Qt.TextSelectableByMouse | Qt.TextSelectableByKeyboard)
        root.addWidget(self.log_view, 1)

    def _button(self, layout: QVBoxLayout, text: str, tip: str) -> QPushButton:
        btn = QPushButton(text)
        if tip:
            btn.setToolTip(tip)
        layout.addWidget(btn)
        return btn

    def log(self, text: str) -> None:
        self.log_view.appendPlainText(text)
