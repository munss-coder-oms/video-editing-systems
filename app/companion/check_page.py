"""⋯ > 점검 도구 쪽 (설계 B1.1): [기능 점검]과 [결과 저장], 그 아래 "예전 시험 도구"
(표시 찍기·소리 넣기·시험 흔적 지우기).
[확인 질문 다시 보기]는 Ctrl+Z 시험(M3)과 자르기 시험(M2)의 답 카드를 대화 칸에 다시 띄운다 (설계 B7.3).

[기능 점검]은 확인을 받은 뒤에만 한다 (점검용 복사본을 만들었다 지움). [남은 점검용 복사본 지우기]는
점검 기록(probe_state.json)이 있을 때만 보이고, 누르면 다시 한 번 묻는다.
안내 아래의 알림 줄(notice)에 이 쪽에서 누른 일의 진행과 결과가 뜬다 (본 쪽 머리말 알림과 따로).
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
        root.setContentsMargins(12, 10, 12, 8)  # 본 쪽(panel)과 같은 여백
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

        # 이 쪽에서 누른 일의 알림 (기능 점검 중… 5/9, 마쳤어요, 결과 파일을 보내 주세요). 본 쪽 머리말에는 띄우지 않는다
        self.notice = QLabel("")
        self.notice.setObjectName("checkNotice")
        self.notice.setWordWrap(True)
        # 테두리·안쪽 여백이 있는 QLabel은 저절로 들여쓰기를 더해 높이를 잴 때만 한 줄 더 잡는다 (위에 빈 줄처럼 보임)
        self.notice.setIndent(0)
        self.notice.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.notice.setVisible(False)
        root.addWidget(self.notice)

        self.probe_btn = self._button(root, S.BTN_PROBE, S.TIP_PROBE)
        self.probe_btn.setProperty("kind", "primary")
        self.leftover_btn = self._button(root, S.BTN_DELETE_LEFTOVER, "")
        self.leftover_btn.setVisible(False)
        self.manual_btn = self._button(root, S.BTN_MANUAL_CHECKS, S.TIP_MANUAL_CHECKS)
        # 기능 점검 뒤 보내 줄 결과 파일 (아래쪽 [결과 저장]과 같다)
        self.report_btn = self._button(root, S.BTN_REPORT, S.TIP_REPORT)
        self.report_hint = QLabel(S.PROBE_REPORT_HINT)
        self.report_hint.setWordWrap(True)
        self.report_hint.setProperty("role", "secondary")
        root.addWidget(self.report_hint)
        self.old_tools = QLabel(S.CHECK_OLD_TOOLS)
        self.old_tools.setProperty("role", "secondary")
        root.addWidget(self.old_tools)
        self.marker_btn = self._button(root, S.BTN_TEST_MARKER, S.TIP_TEST_MARKER)
        self.audio_btn = self._button(root, S.BTN_TEST_AUDIO, S.TIP_TEST_AUDIO)
        self.cleanup_btn = self._button(root, S.BTN_TEST_CLEANUP, S.TIP_TEST_CLEANUP)

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

    def set_notice(self, text: str) -> None:
        self.notice.setText(text)
        self.notice.setToolTip(text)
        self.notice.setVisible(bool(text))
