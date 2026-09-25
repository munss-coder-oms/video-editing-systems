"""대화 칸에 뜨는 카드 (설계 B4.5, B2.5 4~7).

- Card: 제목, 표(할 일/언제/얼마나/트랙/몇 곳/리졸브/그대로), 알림 줄, 단추 줄. 단추를 누르면
  clicked(열쇠)를 보낸다. 단추는 누른 뒤 창이 새 모양으로 바꿀 때까지 한 번만 눌린다.
- ProposalCard: 제안(engine.edits.proposal.Proposal) → 확인 카드 → 영수증 (같은 자리에서 바뀐다).
  영수증은 계획이 아니라 리졸브에서 다시 읽은 결과(ApplyOutcome)로 쓴다.
- QuestionCard: 짧은 질문과 답 단추 (다시 누를 때 [바꾸기] [더하기], 확인 질문 M2/M3 등).

글은 모두 strings_ko.py에서 온다. 리졸브에는 아무것도 묻지 않는다 (단추를 누르면 창이 일을 시작한다).

2.1b에서 뺀 것: 표시 줄마다의 [리졸브에서 보기](재생 위치 옮기기, jump_to)는 2.1c에서 더한다
(Lua 쪽 jump_to op와 기능 점검 C5 결과를 함께 써야 해서). 지금은 줄에 시각과 타임코드만 보인다.
영수증의 [되돌리기]는 묻지 않고 바로 뺀다 (그 카드의 것만, 되돌리기 목록 쪽은 먼저 묻는다).
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from . import fmt
from . import strings_ko as S

INLINE_ITEMS = 3  # 몇 곳: 처음 세 곳은 바로 보이고 나머지는 ▸


def _label(text: str, role: Optional[str] = None, wrap: bool = True) -> QLabel:
    lab = QLabel(text)
    lab.setWordWrap(wrap)
    lab.setTextInteractionFlags(Qt.TextSelectableByMouse)
    lab.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
    if role:
        lab.setProperty("role", role)
    return lab


class Card(QFrame):
    """카드 한 장. 열쇠로 단추를 찾고, plain_text()는 결과 파일과 시험에 쓴다."""

    clicked = Signal(str)

    def __init__(self, title: str = "", parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("card")
        self.setFrameShape(QFrame.StyledPanel)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Maximum)
        self.box = QVBoxLayout(self)
        self.box.setContentsMargins(10, 8, 10, 8)
        self.box.setSpacing(4)
        self.title = _label(title, "card-title")
        self.box.addWidget(self.title)
        self.body = QWidget()
        self.body_box = QVBoxLayout(self.body)
        self.body_box.setContentsMargins(0, 0, 0, 0)
        self.body_box.setSpacing(4)
        self.box.addWidget(self.body)
        self.button_row = QHBoxLayout()
        self.button_row.setSpacing(6)
        self.box.addLayout(self.button_row)
        self.buttons: Dict[str, QPushButton] = {}
        self.busy = False
        self.locked = False  # 눌러서 일을 시작했음 (끝나면 창이 새 모양으로 바꾼다)
        self._primary: List[str] = []  # 작업 중에 꺼 둘 단추 (리졸브에 넣기, 되돌리기 ...)

    # ── 내용 ──────────────────────────────────────────────────────────

    def clear_body(self) -> None:
        _drop_layout(self.body_box)

    def add_line(self, text: str, role: Optional[str] = None) -> QLabel:
        lab = _label(text, role)
        self.body_box.addWidget(lab)
        return lab

    def add_rows(self, rows: Sequence[Tuple[str, str]]) -> QGridLayout:
        grid = QGridLayout()
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(2)
        for r, (key, value) in enumerate(rows):
            k = _label(key, "secondary", wrap=False)
            k.setSizePolicy(QSizePolicy.Minimum, QSizePolicy.Preferred)
            grid.addWidget(k, r, 0, Qt.AlignTop)
            grid.addWidget(_label(value), r, 1)
        grid.setColumnStretch(1, 1)
        self.body_box.addLayout(grid)
        return grid

    def set_buttons(self, buttons: Iterable[Tuple[str, str, bool]], blocked_while_busy: Iterable[str] = ()) -> None:
        """(열쇠, 글, 강조) 목록으로 단추 줄을 새로 만든다."""
        _drop_layout(self.button_row)
        self.buttons = {}
        self.locked = False
        self._primary = list(blocked_while_busy)
        for key, text, primary in buttons:
            btn = QPushButton(text)
            if primary:
                btn.setProperty("kind", "primary")
            btn.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)
            btn.clicked.connect(lambda _=False, k=key: self._press(k))
            self.button_row.addWidget(btn)
            self.buttons[key] = btn
        self.button_row.addStretch(1)
        self._apply_enabled()

    def _press(self, key: str) -> None:
        if self.locked:
            return
        self.clicked.emit(key)

    def lock(self) -> None:
        """일을 시작했음: 끝날 때까지 이 카드의 단추를 누를 수 없다."""
        self.locked = True
        self._apply_enabled()

    def unlock(self) -> None:
        self.locked = False
        self._apply_enabled()

    def set_busy(self, busy: bool) -> None:
        """다른 일이 도는 중 (설계 B10: [리졸브에 넣기]와 되돌리기는 꺼 둔다)."""
        self.busy = busy
        self._apply_enabled()

    def _apply_enabled(self) -> None:
        for key, btn in self.buttons.items():
            btn.setEnabled(not self.locked and not (self.busy and key in self._primary))

    def close_card(self, note: Optional[str] = None) -> None:
        """더 할 것이 없는 카드: 단추를 없애고 (있으면) 한 줄을 남긴다."""
        self.set_buttons([])
        if note:
            self.add_line(note, "secondary")

    def plain_text(self) -> str:
        parts = [self.title.text()]
        parts += _texts(self.body)
        parts += [f"[{b.text()}]" for b in self.buttons.values()]
        return "\n".join(p for p in parts if p)


def _drop_layout(layout) -> None:
    """줄을 모두 뗀다 (바로 부모에서 떼어 plain_text와 화면에서 곧바로 빠지게)."""
    while layout.count():
        item = layout.takeAt(0)
        w = item.widget()
        if w is not None:
            w.hide()
            w.setParent(None)
            w.deleteLater()
        elif item.layout() is not None:
            _drop_layout(item.layout())
            item.layout().deleteLater()


def _shown(lab: QLabel, widget: QWidget) -> bool:
    """일부러 숨긴 줄(▸ 더 보기 전의 목록 등)만 뺀다. 방금 더해서 아직 그려지지 않은 줄은 넣는다."""
    w = lab
    while w is not None and w is not widget:
        if w.isHidden() and w.testAttribute(Qt.WA_WState_ExplicitShowHide):
            return False
        w = w.parentWidget()
    return True


def _texts(widget: QWidget) -> List[str]:
    return [lab.text() for lab in widget.findChildren(QLabel) if lab.text() and _shown(lab, widget)]


class QuestionCard(Card):
    """질문 한 줄과 답 단추. answers = [(열쇠, 글, 강조)]."""

    def __init__(self, title: str, lines: Sequence[str], answers: Sequence[Tuple[str, str, bool]],
                 parent: Optional[QWidget] = None, blocked_while_busy: Iterable[str] = ()) -> None:
        super().__init__(title, parent)
        for line in lines:
            if line:
                self.add_line(line)
        self.set_buttons(answers, blocked_while_busy)

    def answered(self, text: str) -> None:
        self.close_card(text)


# ── 제안 카드 ─────────────────────────────────────────────────────────

def _params_text(params: Dict[str, Any]) -> Dict[str, str]:
    return {k: fmt.num(v) for k, v in params.items()}


def _when(p) -> str:
    fps = p.fps or 1.0
    scope = p.scope or {}
    if scope.get("kind") == "in_out" and scope.get("lo") is not None:
        a = (scope["lo"] - p.tl_start) / fps
        b = (scope["hi"] - p.tl_start) / fps
        return S.WHEN_RANGE.format(a=fmt.clock(a), b=fmt.clock(b))
    end = p.tl_end if p.tl_end is not None else p.tl_start
    return S.WHEN_WHOLE.format(length=fmt.length((end - p.tl_start) / fps))


def _item_lines(p) -> List[str]:
    fps = p.fps or 1.0
    drop = (p.timeline or {}).get("drop_frame")
    fps_text = (p.timeline or {}).get("fps") or fps
    out = []
    for row in p.rows:
        at = fmt.clock((row.start - p.tl_start) / fps)
        out.append(f"{at}  {row.name}  {fmt.tc(row.start, fps_text, drop)}".rstrip())
    return out


def _warning_lines(p) -> List[str]:
    out = []
    for key, n in (p.warnings or {}).items():
        if key == "too_many":
            which = "too_many_pauses" if p.kind == "mark_pauses" else "too_many_spikes"
            out.append(S.WARNINGS[which].format(found=n, cap=p.count))
        elif key in S.WARNINGS:
            out.append(S.WARNINGS[key].format(n=n))
    return out


def voice_row_text(voice) -> str:
    why = S.VOICE_REMEMBERED if voice.remembered else S.VOICE_JUST_PICKED
    return S.VOICE_ROW.format(n=voice.stream + 1, why=why)


def proposal_rows(p, replace_count: int = 0, compact: bool = False) -> List[Tuple[str, str]]:
    """카드의 고정 줄 (설계 B4.5). compact이면 리졸브와 그대로를 한 줄로."""
    params = _params_text(p.params)
    color = fmt.color_word(p.color)
    if p.kind == "mark_pauses":
        what = S.WHAT_PAUSES.format(min_s=params.get("min_s", ""))
        how = S.HOW_PAUSES.format(total=fmt.length(p.total_s))
    else:
        what = S.WHAT_SPIKES.format(above_lu=params.get("above_lu", ""))
        top = max((r.value for r in p.rows), default=0.0)
        how = S.HOW_SPIKES.format(max=fmt.num(round(top, 1)))
    tracks = ", ".join(f"A{t}" for t in p.tracks)
    resolve = (S.RESOLVE_POINT if p.point_only else S.RESOLVE_RANGE).format(color_word=color, n=p.count)
    if replace_count:
        resolve += "\n" + S.RESOLVE_REPLACE.format(color_word=color, n=replace_count)
    rows = [(S.ROW_WHAT, what), (S.ROW_WHEN, _when(p)), (S.ROW_HOW, how), (S.ROW_TRACK, tracks),
            (S.ROW_COUNT, S.COUNT_LINE.format(n=p.count))]
    if compact:
        rows.append((S.ROW_RESOLVE, f"{resolve} · {S.KEEP_MARKERS}"))
    else:
        rows += [(S.ROW_RESOLVE, resolve), (S.ROW_KEEP, S.KEEP_MARKERS)]
    return rows


def receipt_text(outcome, color: Optional[str]) -> str:
    """영수증 첫 줄 (다시 읽은 수로)."""
    word = fmt.color_word(color)
    if outcome.status == "applied":
        return S.RECEIPT_OK.format(at=outcome.at, color_word=word, n=outcome.placed)
    if outcome.status == "partial":
        return S.RECEIPT_PARTIAL.format(expected=outcome.expected, placed=outcome.placed)
    return S.RECEIPT_NONE


class ProposalCard(Card):
    """제안 → 확인 카드 → 영수증. clicked: apply, cancel, voice, undo, resume, remove_placed, replan, force."""

    def __init__(self, proposal, *, replace_count: int = 0, compact: bool = False,
                 parent: Optional[QWidget] = None) -> None:
        super().__init__("", parent)
        self.proposal = proposal
        self.replace_count = replace_count
        self.compact = compact
        self.state = "proposal"  # proposal / receipt / undone / closed
        self.outcome = None
        self.more_btn: Optional[QToolButton] = None
        self.more_list: Optional[QLabel] = None
        self.voice_btn: Optional[QPushButton] = None
        self.show_proposal()

    def show_proposal(self) -> None:
        p = self.proposal
        self.clear_body()
        self.state = "proposal"
        if p.count == 0:
            self.title.setText(S.CARD_TITLE_NONE)
            params = _params_text(p.params)
            text = S.CARD_NONE_PAUSES.format(min_s=params.get("min_s", "")) if p.kind == "mark_pauses" else \
                S.CARD_NONE_SPIKES.format(above_lu=params.get("above_lu", ""))
            self.add_line(text)
            self._voice_row()
            for line in _warning_lines(p):
                self.add_line(line, "warning")
            self.set_buttons([("cancel", S.BTN_CLOSE, False)])
            return
        self.title.setText(S.CARD_TITLE_FOUND)
        self.add_rows(proposal_rows(p, self.replace_count, self.compact))
        self._items()
        self._voice_row()
        for line in _warning_lines(p):
            self.add_line(line, "warning")
        self.set_buttons([("apply", S.BTN_APPLY, True), ("cancel", S.BTN_CANCEL, False)],
                         blocked_while_busy=("apply",))

    def _items(self) -> None:
        lines = _item_lines(self.proposal)
        if not lines:
            return
        head = _label("\n".join(lines[:INLINE_ITEMS]), "secondary")
        self.body_box.addWidget(head)
        rest = lines[INLINE_ITEMS:]
        if rest:
            self.more_btn = QToolButton()
            self.more_btn.setText(S.COUNT_MORE.format(n=len(rest)))
            self.more_btn.setCheckable(True)
            self.more_list = _label("\n".join(rest), "secondary")
            self.more_list.setVisible(False)
            self.more_btn.toggled.connect(self._toggle_more)
            self.body_box.addWidget(self.more_btn, 0, Qt.AlignLeft)
            self.body_box.addWidget(self.more_list)

    def _toggle_more(self, on: bool) -> None:
        rest = len(self.proposal.rows) - INLINE_ITEMS
        self.more_btn.setText(S.COUNT_LESS if on else S.COUNT_MORE.format(n=rest))
        self.more_list.setVisible(on)

    def _voice_row(self) -> None:
        v = self.proposal.voice
        if v is None:
            return
        row = QHBoxLayout()
        row.setSpacing(6)
        key = _label(S.ROW_VOICE, "secondary", wrap=False)
        key.setSizePolicy(QSizePolicy.Minimum, QSizePolicy.Preferred)
        row.addWidget(key)
        row.addWidget(_label(voice_row_text(v)), 1)
        self.voice_btn = QPushButton(S.BTN_CHANGE_VOICE)
        self.voice_btn.setToolTip(S.TIP_CHANGE_VOICE)
        self.voice_btn.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)
        self.voice_btn.clicked.connect(lambda: self._press("voice"))
        row.addWidget(self.voice_btn)
        self.body_box.addLayout(row)
        if v.doubled:
            self.add_line(S.DOUBLED_VOICE, "warning")

    def _apply_enabled(self) -> None:
        super()._apply_enabled()
        if self.voice_btn is not None:
            try:
                # 목소리 [바꾸기]도 다시 계산이라 다른 일을 하는 동안은 꺼 둔다 (설계 B10)
                self.voice_btn.setEnabled(self.state == "proposal" and not self.locked and not self.busy)
            except RuntimeError:
                self.voice_btn = None  # 지운 줄

    # ── 넣은 뒤 ───────────────────────────────────────────────────────

    def show_changed(self) -> None:
        """넣기 직전에 다시 읽어 보니 타임라인이 달라짐: [다시 계산] [그래도 넣기] (표시만 넣는 일이라 허락)."""
        self.add_line(S.TIMELINE_CHANGED, "warning")
        self.set_buttons([("replan", S.BTN_REPLAN, True), ("force", S.BTN_FORCE, False),
                          ("cancel", S.BTN_CANCEL, False)], blocked_while_busy=("force",))

    def show_receipt(self, outcome) -> None:
        self.outcome = outcome
        self.state = "receipt"
        self.clear_body()
        self.voice_btn = None
        p = self.proposal
        self.title.setText(receipt_text(outcome, p.color))
        lines: List[Tuple[str, Optional[str]]] = []
        if outcome.status == "partial":
            lines.append((S.RECEIPT_OK.format(at=outcome.at, color_word=fmt.color_word(p.color), n=outcome.placed),
                          None))
        replaced = sum(outcome.replaced.values()) if outcome.replaced else 0
        if replaced:
            lines.append((S.RECEIPT_REPLACED.format(color_word=fmt.color_word(p.color), n=replaced), "secondary"))
        if outcome.point_fallback and not p.point_only:
            lines.append((S.RECEIPT_POINT, "secondary"))
        if outcome.skipped_existing:
            lines.append((S.RECEIPT_SKIPPED.format(n=outcome.skipped_existing), "secondary"))
        if outcome.receipt.get("readback") is False:
            lines.append((S.RECEIPT_NO_READBACK, "warning"))
        if outcome.dur_ok is False:
            lines.append((S.RECEIPT_DUR_DIFF, "secondary"))
        if outcome.error and outcome.status != "applied":
            lines.append((S.RECEIPT_ERROR.format(reason=outcome.error), "secondary"))
        for text, role in lines:
            self.add_line(text, role)
        if outcome.status == "applied":
            self.set_buttons([("undo", S.BTN_UNDO_ONE, False)], blocked_while_busy=("undo",))
        elif outcome.status == "partial":
            self.set_buttons([("resume", S.BTN_RESUME, True), ("remove_placed", S.BTN_REMOVE_PLACED, False)],
                             blocked_while_busy=("resume", "remove_placed"))
        else:
            self.set_buttons([])

    def show_undone(self, undo, at: str) -> None:
        self.state = "undone"
        self.clear_body()
        self.title.setText(S.RECEIPT_UNDONE.format(at=at, color_word=fmt.color_word(self.proposal.color),
                                                   n=undo.deleted))
        if undo.already_gone:
            self.add_line(S.UNDO_SKIPPED.format(n=undo.already_gone), "secondary")
        if undo.remaining:
            self.add_line(S.UNDO_LEFT.format(n=undo.remaining), "warning")
        self.set_buttons([])

    def show_message(self, text: str, role: Optional[str] = "warning") -> None:
        """영수증 아래에 한 줄 (되돌리기를 못 했을 때 등). 단추는 그대로 다시 누를 수 있다."""
        self.add_line(text, role)
        self.unlock()

    def supersede(self) -> None:
        """같은 버튼의 새 카드가 나옴: 이 카드는 흐리게 "바뀜"."""
        if self.state == "proposal":
            self.state = "closed"
            self.close_card(S.CARD_SUPERSEDED)
            self.setEnabled(False)

    def cancel(self) -> None:
        self.state = "closed"
        self.close_card(S.CARD_CANCELLED)

    def replaced(self) -> None:
        """다시 눌러 [바꾸기]로 이 영수증의 표시가 새것으로 바뀜."""
        if self.state == "receipt":
            self.state = "undone"
            self.close_card(S.CARD_REPLACED)
