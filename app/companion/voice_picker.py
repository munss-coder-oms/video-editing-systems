"""목소리 고르기 카드 (설계 B2.3, 사용자 설명 2 "버튼을 누르면" 1).

녹화 파일 안의 소리마다 한 줄: "소리 2 · 스테레오 · 타임라인에서 켜짐", [▶ 3초 듣기], [이걸로].
- 한 소리가 나머지를 모두 섞은 것처럼 보이면 (추정) 그 줄과 위에 알린다. 고르는 것은 사람이다.
- 섞은 소리와 다른 소리가 함께 타임라인에서 켜져 있으면 "목소리가 두 번 들리고 있을 수 있어요".
- 3초 듣기는 이 PC에서만 튼다 (리졸브는 건드리지 않는다).
- "같이 꺼야 두 번 들리지 않아요"(트랙 끄기 제안)는 트랙을 바꾸는 일이라 2.2에서 더한다. 지금은 알리기만 한다.
"""

from __future__ import annotations

from typing import Dict, List, Optional

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QHBoxLayout, QPushButton, QSizePolicy, QVBoxLayout, QWidget

from . import strings_ko as S
from .cards import Card, _label

QUIET_LUFS = -50.0  # 이보다 작으면 "거의 조용함"


def stream_detail(choice, mix: Optional[int], previous: Optional[int]) -> str:
    parts: List[str] = []
    ch = int(choice.channels or 0)
    parts.append(S.VOICE_CHANNELS.get(ch) or S.VOICE_CHANNELS_N.format(n=ch))
    parts.append(S.VOICE_ON_TIMELINE if choice.enabled_on_timeline else S.VOICE_OFF_TIMELINE)
    integrated = (choice.profile or {}).get("integrated")
    if isinstance(integrated, (int, float)) and integrated < QUIET_LUFS:
        parts.append(S.VOICE_QUIET)
    if mix is not None and choice.index == mix:
        parts.append(S.VOICE_MIX_MARK)
    if previous is not None and choice.index == previous:
        parts.append(S.VOICE_PREVIOUS_MARK)
    return " · ".join(parts)


class VoicePickerCard(Card):
    """clicked 대신 listen(스트림), picked(스트림)을 보낸다. 취소는 clicked("cancel")."""

    listen = Signal(int)
    picked = Signal(int)

    def __init__(self, question, parent: Optional[QWidget] = None) -> None:
        super().__init__(S.VOICE_TITLE, parent)
        self.question = question
        self.listen_buttons: Dict[int, QPushButton] = {}
        self.pick_buttons: Dict[int, QPushButton] = {}
        q = question
        if q.reason == "changed":
            self.add_line(S.VOICE_CHANGED, "warning")
        elif q.reason == "change":
            self.add_line(S.VOICE_CHANGE)
        self.add_line(S.VOICE_INTRO.format(n=len(q.streams)), "secondary")
        if q.mix is not None:
            self.add_line(S.VOICE_MIX.format(n=q.mix + 1))
        if q.doubled:
            self.add_line(S.DOUBLED_VOICE, "warning")
        for choice in q.streams:
            self.body_box.addWidget(self._row(choice))
        self.set_buttons([("cancel", S.BTN_CANCEL, False)])

    def _row(self, choice) -> QWidget:
        row = QWidget()
        box = QHBoxLayout(row)
        box.setContentsMargins(0, 2, 0, 2)
        box.setSpacing(6)
        text = QVBoxLayout()
        text.setSpacing(0)
        name = _label(S.VOICE_STREAM.format(n=choice.index + 1), "card-title", wrap=False)
        text.addWidget(name)
        text.addWidget(_label(stream_detail(choice, self.question.mix, self.question.previous), "secondary"))
        box.addLayout(text, 1)
        listen = QPushButton(S.BTN_LISTEN)
        listen.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)
        listen.setAccessibleName(f"{S.VOICE_STREAM.format(n=choice.index + 1)} {S.BTN_LISTEN}")
        listen.clicked.connect(lambda _=False, i=choice.index: self.listen.emit(i))
        pick = QPushButton(S.BTN_PICK)
        pick.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)
        pick.setAccessibleName(f"{S.VOICE_STREAM.format(n=choice.index + 1)} {S.BTN_PICK}")
        pick.clicked.connect(lambda _=False, i=choice.index: self._pick(i))
        box.addWidget(listen)
        box.addWidget(pick)
        self.listen_buttons[choice.index] = listen
        self.pick_buttons[choice.index] = pick
        return row

    def _pick(self, index: int) -> None:
        if self.locked:
            return
        self.picked.emit(index)

    def set_listening(self, index: Optional[int]) -> None:
        """꺼내는 동안 그 줄의 [▶ 3초 듣기]만 잠깐 꺼 둔다."""
        for i, btn in self.listen_buttons.items():
            btn.setEnabled(index != i and not self.locked)

    def _apply_enabled(self) -> None:
        super()._apply_enabled()
        for btn in (*getattr(self, "pick_buttons", {}).values(), *getattr(self, "listen_buttons", {}).values()):
            btn.setEnabled(not self.locked)

    def done(self, index: Optional[int], note: Optional[str] = None) -> None:
        """고름(또는 취소): 단추를 모두 끈다."""
        self.close_card(note if note is not None else S.VOICE_PICKED.format(n=(index or 0) + 1))
        self.locked = True
        self._apply_enabled()
