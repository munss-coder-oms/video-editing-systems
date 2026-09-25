"""아래쪽: [↶ 되돌리기 ▾]와 [결과 저장] (설계 B1.1, B6).

2.1a에는 리졸브에 넣는 일이 아직 없어서 되돌리기 목록은 보여 주기만 한다 (일지 요약, 누를 수 없음).
결과 저장은 작업 중에도 늘 누를 수 있다 (연결이 안 될 때 보내 주는 결과 파일이 가장 중요하므로).
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from PySide6.QtWidgets import QHBoxLayout, QMenu, QPushButton, QToolButton, QWidget

from . import strings_ko as S
from . import theme


class UndoView(QWidget):
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
        self.set_entries([])

    def set_entries(self, entries: List[Dict[str, Any]]) -> None:
        """일지 요약 (새것이 위). 2.1a에서는 모두 누를 수 없고 "다음 판에서 열려요"를 덧붙인다."""
        self.menu.clear()
        if not entries:
            self.menu.addAction(S.UNDO_NOTHING).setEnabled(False)
            return
        for e in reversed(entries):
            text = S.UNDO_ENTRY.format(at=e.get("at") or "", request=e.get("request") or "",
                                       status=e.get("status") or "")
            self.menu.addAction(text).setEnabled(False)
        self.menu.addSeparator()
        self.menu.addAction(S.UNDO_SOON).setEnabled(False)

    def entry_texts(self) -> List[str]:
        return [a.text() for a in self.menu.actions() if not a.isSeparator()]
