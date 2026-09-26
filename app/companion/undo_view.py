"""아래쪽: [↶ 되돌리기 ▾]와 [결과 저장] (설계 B1.1, B6.3).

되돌리기 목록: 지금 타임라인의 일지(새것이 위). 넣은 것(넣음·일부 넣음)은 눌러서 뺄 수 있다 (먼저 묻는다).
맨 아래 "도우미가 넣은 것 모두 빼기"는 일지가 아니라 지금 타임라인에서 꼬리표를 직접 찾는다.
Ctrl+Z 안내: 도우미가 넣은 것은 여기서 빼 달라고 적는다 (Ctrl+Z 시험 답이 "다른 것이 되돌아감"이면 경고를 덧붙인다).
결과 저장은 작업 중에도 늘 누를 수 있다 (연결이 안 될 때 보내 주는 결과 파일이 가장 중요하므로).
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QHBoxLayout, QMenu, QPushButton, QToolButton, QWidget

from . import strings_ko as S
from . import theme

UNDOABLE = ("applied", "partial")


class UndoView(QWidget):
    undo_requested = Signal(str)  # 제안 번호
    remove_all_requested = Signal()

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("footer")
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)
        self.undo_btn = QToolButton()
        self.undo_btn.setText(S.BTN_UNDO)
        self.undo_btn.setAccessibleName(S.BTN_UNDO)
        self.undo_btn.setPopupMode(QToolButton.InstantPopup)
        self.undo_btn.setMinimumHeight(theme.FOOTER_H)
        self.menu = QMenu(self.undo_btn)
        self.undo_btn.setMenu(self.menu)
        self.report_btn = QPushButton(S.BTN_REPORT)
        self.report_btn.setToolTip(S.TIP_REPORT)
        self.report_btn.setMinimumHeight(theme.FOOTER_H)
        row.addWidget(self.undo_btn)
        row.addStretch(1)
        row.addWidget(self.report_btn)
        self.setFixedHeight(theme.FOOTER_H + 4)
        self.entries: List[Dict[str, Any]] = []
        self.warn_ctrl_z = False
        self.connected = False
        self.set_entries([])

    def set_entries(self, entries: List[Dict[str, Any]], *, connected: Optional[bool] = None,
                    warn_ctrl_z: Optional[bool] = None) -> None:
        """일지 요약 (Journal.summaries, 오래된 것부터). 화면에는 새것이 위."""
        if connected is not None:
            self.connected = connected
        if warn_ctrl_z is not None:
            self.warn_ctrl_z = warn_ctrl_z
        self.entries = list(entries)
        self.menu.clear()
        if not self.entries:
            self.menu.addAction(S.UNDO_NOTHING).setEnabled(False)
        for e in reversed(self.entries):
            status = e.get("status") or ""
            text = S.UNDO_ENTRY.format(at=_short_time(e.get("at")), request=e.get("request") or "",
                                       status=S.UNDO_STATUS.get(status, status))
            act = self.menu.addAction(text)
            pid = e.get("proposal_id")
            if status in UNDOABLE and pid:
                act.triggered.connect(lambda _=False, p=pid: self.undo_requested.emit(p))
            else:
                act.setEnabled(False)
        self.menu.addSeparator()
        all_act = self.menu.addAction(S.UNDO_ALL)
        all_act.setEnabled(self.connected)
        all_act.triggered.connect(self.remove_all_requested)
        hint = S.UNDO_HINT
        if self.warn_ctrl_z:
            hint = f"{hint} · {S.UNDO_HINT_WARN}"
        self.menu.addAction(hint).setEnabled(False)

    def set_connected(self, connected: bool) -> None:
        if connected != self.connected:
            self.set_entries(self.entries, connected=connected)

    def entry_texts(self) -> List[str]:
        """일지 줄만 (모두 빼기와 안내 줄은 빼고)."""
        out = []
        for a in self.menu.actions():
            if a.isSeparator():
                break
            out.append(a.text())
        return out

    def action_for(self, proposal_id: str):
        """시험용: 그 제안의 줄."""
        items = [e for e in reversed(self.entries)]
        actions = [a for a in self.menu.actions()]
        for e, a in zip(items, actions):
            if e.get("proposal_id") == proposal_id:
                return a
        return None

    def remove_all_action(self):
        return next(a for a in self.menu.actions() if a.text() == S.UNDO_ALL)


def _short_time(at: Any) -> str:
    """일지 시각 "2026-09-26T14:02:10+0900" → "14:02"."""
    if isinstance(at, str) and "T" in at:
        return at.split("T", 1)[1][:5]
    return str(at or "")
