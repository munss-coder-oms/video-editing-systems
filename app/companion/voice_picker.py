"""목소리 고르기 카드 (설계 B2.3, 사용자 설명 2 "버튼을 누르면" 1).

녹화 파일 안의 소리마다 한 줄: "소리 2 · 스테레오 · 타임라인에서 켜짐", [▶ 3초 듣기], [이걸로].
- 한 소리가 나머지를 모두 섞은 것처럼 보이면 (추정) 그 줄과 위에 알린다. 고르는 것은 사람이다.
- 섞은 소리와 다른 소리가 함께 타임라인에서 켜져 있으면 "목소리가 두 번 들릴 수 있어요" (어느 소리인지 적어서,
  이 카드에 한 번만. 확인 카드마다 되풀이하지 않는다).
- 3초 듣기는 이 PC에서만 튼다 (리졸브는 건드리지 않는다). 트는 3초 동안 그 단추는 "■ 듣는 중".
- 고르면 카드는 한 줄("목소리는 소리 2로 정했어요 …")로 접힌다.
- 할 일(들어 보고 골라 주세요)은 보통 글, 섞은 소리 추정은 작은 글. 줄의 꼬리표("전체 소리 (추정)")는
  한 덩어리로 줄을 바꾼다 (낱말 사이를 줄 바꿈 없는 빈칸으로).
- [▶ 3초 듣기]와 [■ 듣는 중]은 너비가 같다 (StableButton: 글꼴이 입혀진 뒤 두 글 중 넓은 쪽으로).
- 줄 너비가 "소리 N"과 두 단추에 모자라면 단추를 글 아래로 내린다 (ReflowRow: 좁은 창·넓은 글꼴에서 "소리 N"이 잘리지 않게).
- "같이 꺼야 두 번 들리지 않아요"(트랙 끄기 제안)는 트랙을 바꾸는 일이라 2.2에서 더한다. 지금은 알리기만 한다.
"""

from __future__ import annotations

from typing import Dict, List, Optional

from PySide6.QtCore import QSize, Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QBoxLayout,
    QHBoxLayout,
    QPushButton,
    QSizePolicy,
    QStyle,
    QStyleOptionButton,
    QVBoxLayout,
    QWidget,
)

from . import strings_ko as S
from .cards import Card, _label

QUIET_LUFS = -50.0  # 이보다 작으면 "거의 조용함"
LISTEN_MS = 3000  # 3초 듣기: 그동안 단추에 "■ 듣는 중"
NBSP = "\u00a0"
TAG_SEP = " · "


class StableButton(QPushButton):
    """글이 바뀌어도 너비가 그대로인 단추: 크기 힌트를 바뀔 글 가운데 가장 넓은 것으로 잰다.

    만들 때 fontMetrics()로 재면 스타일시트의 글꼴(14px, ▶의 대체 글꼴)이 입혀지기 전이라 모자란다.
    크기 힌트는 쓸 때마다 지금 글꼴과 스타일로, 글마다 QPushButton이 재는 방법 그대로 잰다.
    (QPushButton의 minimumSizeHint는 sizeHint를 부르므로 따로 넓히지 않는다: 두 번 넓어진다)"""

    def __init__(self, texts: List[str], parent: Optional[QWidget] = None) -> None:
        super().__init__(texts[0], parent)
        self._texts = list(texts)

    def _hint_for(self, text: str) -> QSize:
        opt = QStyleOptionButton()
        self.initStyleOption(opt)
        opt.text = text
        size = self.fontMetrics().size(Qt.TextShowMnemonic, text)
        opt.rect.setSize(size)
        return self.style().sizeFromContents(QStyle.CT_PushButton, opt, size, self)

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt 이름
        hint = super().sizeHint()
        for text in self._texts:
            hint = hint.expandedTo(self._hint_for(text))
        return hint

    def minimumSizeHint(self) -> QSize:  # noqa: N802
        return self.sizeHint()


class ReflowRow(QWidget):
    """왼쪽 글 + 오른쪽 단추 한 줄. 너비가 모자라면 단추를 글 아래 줄로 내린다.

    한 줄에 두면 좁은 창이나 넓은 글꼴(윈도우 검사: 380px, 글자 130%)에서 단추는 그대로이고
    "소리 1"만 눌려 잘렸다. 방향은 받은 너비로만 정하므로 크기가 바뀌어도 오가며 흔들리지 않는다.
    """

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.box = QBoxLayout(QBoxLayout.LeftToRight, self)
        self.box.setContentsMargins(0, 2, 0, 2)
        self.box.setSpacing(6)
        self.keep_whole: Optional[QWidget] = None  # 잘리면 안 되는 글 ("소리 1")
        self.side: Optional[QHBoxLayout] = None  # 오른쪽 단추들

    def needed_width(self) -> int:
        if self.keep_whole is None or self.side is None:
            return 0
        m = self.box.contentsMargins()
        return (self.keep_whole.sizeHint().width() + self.box.spacing() + self.side.sizeHint().width()
                + m.left() + m.right())

    def _reflow(self, width: int) -> None:
        want = QBoxLayout.LeftToRight if width >= self.needed_width() else QBoxLayout.TopToBottom
        if self.box.direction() != want:
            self.box.setDirection(want)
            if self.side is not None:
                self.box.setAlignment(self.side, Qt.AlignLeft if want == QBoxLayout.TopToBottom else Qt.Alignment())

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt 이름
        self._reflow(event.size().width())
        super().resizeEvent(event)

    def minimumSizeHint(self) -> QSize:  # noqa: N802
        # 아래로 내린 모양의 최소 너비: 한 줄 모양의 최소 너비 때문에 카드가 대화 칸보다 넓어지지 않게
        hint = super().minimumSizeHint()
        if self.side is None:
            return hint
        m = self.box.contentsMargins()
        width = max(self.side.minimumSize().width(),
                    self.keep_whole.sizeHint().width() if self.keep_whole is not None else 0)
        return QSize(min(hint.width(), width + m.left() + m.right()), hint.height())


def doubled_text(mix: Optional[int]) -> str:
    """목소리가 두 번 들릴 수 있음: 전체 소리가 몇 번인지 알면 그 번호까지."""
    return S.DOUBLED_VOICE.format(mix=mix + 1) if mix is not None else S.DOUBLED_VOICE_SHORT


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
    # 꼬리표 하나는 한 덩어리: "전체 소리 (추정)"이 "전체" / "소리 (추정)"으로 갈라지지 않게
    return TAG_SEP.join(p.replace(" ", NBSP) for p in parts)


class VoicePickerCard(Card):
    """clicked 대신 listen(스트림), picked(스트림)을 보낸다. 취소는 clicked("cancel")."""

    listen = Signal(int)
    picked = Signal(int)

    def __init__(self, question, parent: Optional[QWidget] = None) -> None:
        super().__init__(S.VOICE_TITLE, parent)
        self.question = question
        self.listen_buttons: Dict[int, QPushButton] = {}
        self.pick_buttons: Dict[int, QPushButton] = {}
        self.playing: Optional[int] = None
        self._play_timer = QTimer(self)
        self._play_timer.setSingleShot(True)
        self._play_timer.timeout.connect(lambda: self.set_playing(None))
        q = question
        if q.reason == "changed":
            self.add_line(S.VOICE_CHANGED, "warning")
        elif q.reason == "change":
            self.add_line(S.VOICE_CHANGE)
        # 할 일(골라 주세요)이 먼저, 보통 글로. 섞은 소리 추정은 덧붙이는 말이라 작은 글로
        self.add_line(S.VOICE_INTRO.format(n=len(q.streams)))
        if q.mix is not None:
            self.add_line(S.fill(S.VOICE_MIX, n=q.mix + 1), "secondary")
        if q.doubled:
            self.add_line(doubled_text(q.mix), "warning")
        for choice in q.streams:
            self.body_box.addWidget(self._row(choice))
        self.set_buttons([("cancel", S.BTN_CANCEL, False)])

    def _row(self, choice) -> QWidget:
        row = ReflowRow()
        box = row.box
        text = QVBoxLayout()
        text.setSpacing(0)
        name = _label(S.VOICE_STREAM.format(n=choice.index + 1), "card-title", wrap=False)
        text.addWidget(name)
        text.addWidget(_label(stream_detail(choice, self.question.mix, self.question.previous), "secondary"))
        box.addLayout(text, 1)
        # "■ 듣는 중"으로 바뀌어도 단추 너비가 흔들리지 않게 두 글 중 넓은 쪽으로 (줄마다 같은 자리)
        listen = StableButton([S.BTN_LISTEN, S.BTN_LISTENING])
        listen.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        listen.setAccessibleName(f"{S.VOICE_STREAM.format(n=choice.index + 1)} {S.BTN_LISTEN}")
        listen.clicked.connect(lambda _=False, i=choice.index: self.listen.emit(i))
        pick = QPushButton(S.BTN_PICK)
        pick.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)
        pick.setAccessibleName(f"{S.VOICE_STREAM.format(n=choice.index + 1)} {S.BTN_PICK}")
        pick.clicked.connect(lambda _=False, i=choice.index: self._pick(i))
        side = QHBoxLayout()
        side.setSpacing(6)
        side.addWidget(listen)
        side.addWidget(pick)
        box.addLayout(side)
        row.keep_whole, row.side = name, side
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

    def set_playing(self, index: Optional[int], ms: int = LISTEN_MS) -> None:
        """트는 동안(3초) 그 줄의 단추를 "■ 듣는 중"으로. None이면 모두 "▶ 3초 듣기"로 돌린다."""
        self.playing = index
        for i, btn in self.listen_buttons.items():
            try:
                btn.setText(S.BTN_LISTENING if i == index else S.BTN_LISTEN)
            except RuntimeError:
                continue
        if index is None:
            self._play_timer.stop()
        else:
            self._play_timer.start(ms)

    def _apply_enabled(self) -> None:
        super()._apply_enabled()
        # [이걸로]는 다시 계산이라 다른 일이 도는 동안 꺼 둔다 (설계 B10). 3초 듣기는 이 PC에서만이라 그대로
        for btn in getattr(self, "pick_buttons", {}).values():
            btn.setEnabled(not self.locked and not self.busy)
        for btn in getattr(self, "listen_buttons", {}).values():
            btn.setEnabled(not self.locked)

    def done(self, index: Optional[int], note: Optional[str] = None) -> None:
        """고름: 카드를 한 줄("목소리는 소리 2로 정했어요 …")로 접는다. 취소: 줄은 두고 단추만 끈다."""
        self._play_timer.stop()
        if note is None and index is not None:
            text = S.fill(S.VOICE_PICKED, n=index + 1)
            self.clear_body()
            self.listen_buttons, self.pick_buttons = {}, {}
            self.title.setText(text)
            self.close_card(None, announce=text)
        else:
            self.close_card(note if note is not None else S.CARD_CANCELLED)
        self.locked = True
        self._apply_enabled()
