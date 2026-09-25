"""머리말: 연결 불빛과 낱말, [연결 확인], ⋯, 타임라인 한 줄 요약(▸ 자세히), 점검용 복사본 경고 (설계 B1.1)."""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from engine.resolve_link.ops import TimelineInfo

from . import connection as conn
from . import strings_ko as S
from . import theme

_STATUS = {
    conn.CONNECTED: (S.DOT_CONNECTED, S.STATUS_CONNECTED, ""),
    conn.NOT_CONNECTED: (S.DOT_OFF, S.STATUS_NOT_CONNECTED, S.CONNECT_HINT),
    conn.RESOLVE_QUIT: (S.DOT_OFF, S.STATUS_RESOLVE_QUIT, S.RESOLVE_QUIT_HINT),
    conn.OLD_SCRIPT: (S.DOT_WARN, S.STATUS_OLD_SCRIPT, S.OLD_SCRIPT_HINT),
    conn.BUSY: (S.DOT_WARN, S.STATUS_BUSY, S.BUSY_HINT),
}
INFO_ROWS = (
    ("version", S.INFO_VERSION),
    ("project", S.INFO_PROJECT),
    ("timeline", S.INFO_TIMELINE),
    ("fps", S.INFO_FPS),
    ("clips", S.INFO_CLIPS),
    ("first_clip", S.INFO_FIRST_CLIP),
)


def file_name(path: Any) -> str:
    """리졸브가 준 경로에서 파일 이름만 (윈도우 경로를 다른 OS에서 받아도)."""
    return re.split(r"[\\/]", path)[-1] if isinstance(path, str) and path else ""


def _length_text(seconds: Optional[float]) -> str:
    if seconds is None:
        return S.SUMMARY_LENGTH_UNKNOWN
    total = int(round(seconds))
    h, rest = divmod(total, 3600)
    m, s = divmod(rest, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def timeline_of(state: Optional[Dict[str, Any]], kind: Optional[str]) -> Optional[TimelineInfo]:
    if not isinstance(state, dict):
        return None
    return TimelineInfo.from_result(state) if kind == "timeline_info" else TimelineInfo.from_state(state)


def summary_text(state: Optional[Dict[str, Any]], kind: Optional[str]) -> str:
    info = timeline_of(state, kind)
    if info is None:
        return S.SUMMARY_NONE
    if not info.has_project:
        return S.SUMMARY_NO_PROJECT
    if not info.has_timeline:
        return S.SUMMARY_NO_TIMELINE
    return S.SUMMARY_LINE.format(timeline=info.timeline or S.INFO_NO_NAME, length=_length_text(info.duration_seconds),
                                 fps=info.fps or "?", audio=info.track_count("audio"))


class HeaderView(QWidget):
    check_clicked = Signal()
    back_clicked = Signal()

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("header")
        self.mode = "full"
        self._hint = ""
        self._summary = S.SUMMARY_NONE
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(4)

        row = QHBoxLayout()
        row.setSpacing(6)
        self.dot = QLabel(S.DOT_OFF)
        self.dot.setFixedWidth(18)
        self.status = QLabel(S.STATUS_NOT_CONNECTED)
        self.status.setWordWrap(False)
        self.status.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.check_btn = QPushButton(S.BTN_CHECK_CONNECTION)
        self.check_btn.setToolTip(S.TIP_CHECK_CONNECTION)
        self.check_btn.clicked.connect(self.check_clicked)
        self.more_btn = QToolButton()
        self.more_btn.setText(S.BTN_MORE)
        self.more_btn.setToolTip(S.TIP_MORE)
        self.more_btn.setAccessibleName(S.TIP_MORE)
        self.more_btn.setPopupMode(QToolButton.InstantPopup)
        self.more_btn.setMinimumSize(theme.HIT_MIN, theme.HIT_MIN)
        row.addWidget(self.dot)
        row.addWidget(self.status, 1)
        row.addWidget(self.check_btn)
        row.addWidget(self.more_btn)
        root.addLayout(row)

        self.hint = QLabel("")
        self.hint.setWordWrap(True)
        self.hint.setProperty("role", "secondary")
        self.hint.setTextInteractionFlags(Qt.TextSelectableByMouse)
        root.addWidget(self.hint)

        summary_row = QHBoxLayout()
        self.summary = QLabel(S.SUMMARY_NONE)
        self.summary.setWordWrap(True)
        self.summary.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.details_btn = QToolButton()
        self.details_btn.setText(S.DETAILS_SHOW)
        self.details_btn.setToolTip(S.TIP_DETAILS)
        self.details_btn.setAccessibleName(S.TIP_DETAILS)
        self.details_btn.setCheckable(True)
        self.details_btn.setMinimumSize(theme.HIT_MIN, theme.HIT_MIN)
        self.details_btn.toggled.connect(self._toggle_details)
        summary_row.addWidget(self.summary, 1)
        summary_row.addWidget(self.details_btn)
        self.summary_row = QWidget()
        self.summary_row.setLayout(summary_row)
        summary_row.setContentsMargins(0, 0, 0, 0)
        root.addWidget(self.summary_row)

        # 예전 창의 "리졸브에서 열려 있는 것" (▸로 펼침)
        self.details = QGroupBox(S.INFO_TITLE)
        form = QFormLayout(self.details)
        self.info: Dict[str, QLabel] = {}
        for key, label in INFO_ROWS:
            value = QLabel(S.INFO_EMPTY)
            value.setWordWrap(True)
            value.setTextInteractionFlags(Qt.TextSelectableByMouse)
            form.addRow(label, value)
            self.info[key] = value
        self.details.setVisible(False)
        root.addWidget(self.details)

        self.warning_row = QWidget()
        warn = QHBoxLayout(self.warning_row)
        warn.setContentsMargins(0, 0, 0, 0)
        self.warning = QLabel(S.PROBE_COPY_WARNING)
        self.warning.setWordWrap(True)
        self.warning.setProperty("role", "warning")
        self.back_btn = QPushButton(S.BTN_BACK_TO_ORIGINAL)
        self.back_btn.clicked.connect(self.back_clicked)
        warn.addWidget(self.warning, 1)
        warn.addWidget(self.back_btn)
        self.warning_row.setVisible(False)
        root.addWidget(self.warning_row)

        self.message = QLabel("")
        self.message.setWordWrap(True)
        self.message.setProperty("role", "secondary")
        self.message.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.message.setVisible(False)
        root.addWidget(self.message)

    # ── 상태 ─────────────────────────────────────────────────────────

    def set_status(self, status: str, checking: bool = False) -> None:
        dot, word, hint = _STATUS.get(status, _STATUS[conn.NOT_CONNECTED])
        if checking and status != conn.CONNECTED:
            dot, word = S.DOT_CHECKING, S.STATUS_CHECKING
        self.dot.setText(dot)
        self.status.setText(word)
        role = theme.status_role(status)
        for label in (self.dot, self.status):
            label.setProperty("role", role)
            label.style().unpolish(label)
            label.style().polish(label)
        self.status.setToolTip(hint or self._summary)
        self._hint = hint
        self.check_btn.setText(S.BTN_RETRY if status == conn.BUSY else S.BTN_CHECK_CONNECTION)
        self._apply_mode()

    def set_message(self, text: str) -> None:
        self.message.setText(text)
        self.message.setToolTip(text)
        self.message.setVisible(bool(text))

    def show_info(self, ping: Optional[Dict[str, Any]], state: Optional[Dict[str, Any]], kind: Optional[str],
                  audio_items: Optional[List[Dict[str, Any]]] = None) -> None:
        """리졸브의 답(ping, timeline_info 또는 예전 state)을 요약 줄과 자세히 칸에 채운다."""
        if ping:
            version = ping.get("resolve_version") or ping.get("product_version") or S.INFO_UNKNOWN_VERSION
            self.info["version"].setText(f"{ping.get('product') or S.INFO_RESOLVE} {version}")
        if not isinstance(state, dict):
            return
        self._summary = summary_text(state, kind)
        self.summary.setText(self._summary)
        self.summary.setToolTip(self._summary)
        project, timeline = state.get("project"), state.get("timeline")
        if project is None:
            self.info["project"].setText(S.SUMMARY_NO_PROJECT)
            timeline_text = S.INFO_EMPTY
        else:
            self.info["project"].setText(project or S.INFO_NO_NAME)
            timeline_text = S.SUMMARY_NO_TIMELINE if timeline is None else (timeline or S.INFO_NO_NAME)
        self.info["timeline"].setText(timeline_text)
        fps = state.get("fps")
        self.info["fps"].setText(f"{fps}{S.INFO_DROP if state.get('drop_frame') else ''}" if fps else S.INFO_EMPTY)
        open_timeline = project is not None and timeline is not None
        paths: List[str] = []
        if kind == "timeline_info":
            tracks = state.get("tracks") if isinstance(state.get("tracks"), dict) else {}

            def count(k: str) -> Optional[int]:
                rows = tracks.get(k) if isinstance(tracks.get(k), list) else []
                values = [r.get("count") for r in rows if isinstance(r, dict)]
                if any(not isinstance(v, int) for v in values):
                    return None
                return sum(values)

            video, audio = count("video"), count("audio")
            clips = (S.INFO_CLIPS_LINE.format(video=video, audio=audio)
                     if open_timeline and video is not None and audio is not None else S.INFO_EMPTY)
            paths = [i.get("path") for i in audio_items or []]
        else:
            items = state.get("items") or {}
            video_items, audio_list = items.get("video") or [], items.get("audio") or []
            clips = S.INFO_CLIPS_LINE.format(video=len(video_items), audio=len(audio_list)) if open_timeline else S.INFO_EMPTY
            if state.get("truncated"):
                clips += S.INFO_CLIPS_TRUNCATED
            paths = [i.get("path") for i in [*video_items, *audio_list] if isinstance(i, dict)]
        self.info["clips"].setText(clips)
        first = next((file_name(p) for p in paths if file_name(p)), "")
        self.info["first_clip"].setText(first or S.INFO_EMPTY)

    def set_probe_copy(self, name: Optional[str], can_switch: bool) -> None:
        self.warning_row.setVisible(bool(name))
        self.back_btn.setVisible(bool(name) and can_switch)
        self.warning.setText(S.PROBE_COPY_WARNING if can_switch else f"{S.PROBE_COPY_WARNING}. {S.PROBE_COPY_NO_RECORD}")

    def _toggle_details(self, on: bool) -> None:
        self.details_btn.setText(S.DETAILS_HIDE if on else S.DETAILS_SHOW)
        self.details.setVisible(on)

    # ── 좁은 화면 ────────────────────────────────────────────────────

    def set_mode(self, mode: str) -> None:
        """full: 모두 / tiles: 두 줄 (상태 + 안내나 요약) / tight: 한 줄 (연결 전 안내만 한 줄 더)."""
        self.mode = mode
        self._apply_mode()

    def _apply_mode(self) -> None:
        mode = self.mode
        hint = self._hint
        if mode == "full":
            self.hint.setText(hint)
            self.hint.setVisible(bool(hint))
            self.summary_row.setVisible(True)
            self.hint.setWordWrap(True)
        elif mode == "tiles":
            # 두 줄: 안내가 있으면 안내, 없으면 요약
            self.hint.setText(hint)
            self.hint.setVisible(bool(hint))
            self.summary_row.setVisible(not hint)
        else:
            # 한 줄. 연결 전에는 스크립트를 누르라는 안내가 가장 중요해서 그 줄만 남긴다
            self.hint.setText(hint)
            self.hint.setVisible(bool(hint))
            self.summary_row.setVisible(False)
            if self.details_btn.isChecked():
                self.details_btn.setChecked(False)
