"""대화 칸에 뜨는 카드 (설계 B4.5, B2.5 4~7).

- Card: 제목, 표(할 일/언제/얼마나/트랙/몇 곳/리졸브/그대로), 알림 줄, 단추 줄. 단추를 누르면
  clicked(열쇠)를 보낸다. 단추는 누른 뒤 창이 새 모양으로 바꿀 때까지 한 번만 눌린다.
- ProposalCard: 제안(engine.edits.proposal.Proposal) → 확인 카드 → 영수증 (같은 자리에서 바뀐다).
  영수증은 계획이 아니라 리졸브에서 다시 읽은 결과(ApplyOutcome)로 쓴다.
- QuestionCard: 짧은 질문과 답 단추 (다시 누를 때 [바꾸기] [더하기], 확인 질문 M2/M3 등).

글은 모두 strings_ko.py에서 온다. 리졸브에는 아무것도 묻지 않는다 (단추를 누르면 창이 일을 시작한다).

표시 줄마다 [보기](리졸브에서 보기: 재생 위치만 옮김, jump_to)가 있고 줄을 눌러도 된다 (2.1c).
대화에서 온 카드는 값마다 출처(말씀하신 값 / 기본값 / 설정값 / 도우미가 찾음)를 붙이고, 범위 지킴이의
막음(빨강, [리졸브에 넣기]를 누를 수 없음)과 경고(주황)를 보이고, 숫자를 [−][+]로 고칠 수 있다.
쉬는 곳·튀는 소리 카드에는 "이대로 자동화 버튼에 저장 ▸"이 붙는다 (구간은 저장하지 않는다).
영수증의 [되돌리기]는 묻지 않고 바로 뺀다 (그 카드의 것만, 되돌리기 목록 쪽은 먼저 묻는다).
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractButton,
    QComboBox,
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


class ClickLabel(QLabel):
    """누르면 clicked를 보내는 글 줄 (영수증 목록의 한 줄을 누르면 재생 위치를 옮긴다)."""

    clicked = Signal()

    def __init__(self, text: str = "", parent: Optional[QWidget] = None) -> None:
        super().__init__(text, parent)
        self.setWordWrap(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.setProperty("role", "item")

    def mouseReleaseEvent(self, event) -> None:
        super().mouseReleaseEvent(event)
        if event.button() == Qt.LeftButton and self.isEnabled():
            self.clicked.emit()


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
        self.extra: Dict[str, QAbstractButton] = {}  # 줄 안의 단추 ([−][+], 보기, 저장): 열쇠 → 단추
        self._extra_busy: List[str] = []  # 다른 일을 하는 동안 꺼 둘 줄 안 단추
        self.busy = False
        self.locked = False  # 눌러서 일을 시작했음 (끝나면 창이 새 모양으로 바꾼다)
        self._primary: List[str] = []  # 작업 중에 꺼 둘 단추 (리졸브에 넣기, 되돌리기 ...)

    # ── 내용 ──────────────────────────────────────────────────────────

    def clear_body(self) -> None:
        _drop_layout(self.body_box)
        self.drop_extras()

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

    def add_extra(self, key: str, btn: QAbstractButton, *, blocked_while_busy: bool = True) -> QAbstractButton:
        """줄 안의 단추. 누르면 clicked(열쇠). 일하는 동안 꺼 둘지 정한다."""
        btn.clicked.connect(lambda _=False, k=key: self._press(k))
        self.extra[key] = btn
        if blocked_while_busy:
            self._extra_busy.append(key)
        self._apply_enabled()
        return btn

    def drop_extras(self) -> None:
        self.extra = {}
        self._extra_busy = []

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
        for key, btn in list(self.extra.items()):
            try:
                btn.setEnabled(not self.locked and not (self.busy and key in self._extra_busy))
            except RuntimeError:
                self.extra.pop(key, None)  # 지운 줄

    def close_card(self, note: Optional[str] = None) -> None:
        """더 할 것이 없는 카드: 단추를 없애고 (있으면) 한 줄을 남긴다. 줄의 [보기]만 남긴다."""
        self.set_buttons([])
        for key in [k for k in self.extra if not k.startswith("view:")]:
            btn = self.extra.pop(key)
            try:
                btn.hide()
            except RuntimeError:
                pass
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
    out = []
    for w in widget.findChildren(QWidget):
        if isinstance(w, QLabel) and w.text() and _shown(w, widget):
            out.append(w.text())
    return out


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

MAIN_PARAM = {"mark_pauses": "min_s", "mark_spikes": "above_lu"}  # 카드에서 [−][+]로 고치는 값


def _params_text(params: Dict[str, Any]) -> Dict[str, str]:
    return {k: fmt.num(v) for k, v in params.items() if not isinstance(v, (list, dict))}


def tagged(value: str, src: Optional[str], slot_name: Optional[str] = None) -> str:
    """값 뒤에 출처 (설계 B4.4): "2초 넘게 쉰 곳 표시 · 말씀하신 값"."""
    if not src or src not in S.PROVENANCE:
        return value
    word = S.PROVENANCE_SLOT.format(name=slot_name) if src == "setting" and slot_name else S.PROVENANCE[src]
    return S.TAGGED.format(value=value, source=word)


def _secs(p, frame: int) -> float:
    return (int(frame) - p.tl_start) / (p.fps or 1.0)


def _tc(p, frame: int) -> str:
    """절대 프레임 → 리졸브 타임코드 (타임라인 시작 타임코드 기준)."""
    t = p.timeline or {}
    fps_text = t.get("fps") or p.fps
    start_tc = t.get("start_tc")
    drop = t.get("drop_frame")
    if start_tc:
        from engine.resolve_link.timecode import tc_to_frames

        try:
            base = tc_to_frames(start_tc, fps_text, drop)
            return fmt.tc(base + (int(frame) - p.tl_start), fps_text, drop)
        except (ValueError, TypeError):
            pass
    return fmt.tc(frame, fps_text, drop)


def colors_word(p) -> str:
    words = [fmt.color_word(c) for c in (p.colors or [])]
    return S.COLOR_JOIN.join(words) if words else fmt.color_word(p.color)


def _when(p) -> str:
    scope = p.scope or {}
    kind = scope.get("kind")
    if kind in ("in_out", "range") and scope.get("lo") is not None:
        a, b = fmt.clock(_secs(p, scope["lo"])), fmt.clock(_secs(p, scope["hi"]))
        if scope.get("src") == "around":
            return S.WHEN_AROUND.format(a=a, b=b)
        return S.WHEN_RANGE.format(a=a, b=b)
    end = p.tl_end if p.tl_end is not None else p.tl_start
    return S.WHEN_WHOLE.format(length=fmt.length((end - p.tl_start) / (p.fps or 1.0)))


def _item_line(p, i: int) -> str:
    row = p.rows[i]
    at = fmt.clock(_secs(p, row.start))
    if p.kind == "mark":
        spec = p.specs[i]
        return S.ITEM_LINE.format(at=at, color_word=fmt.color_word(spec.color), name=row.name,
                                  tc=_tc(p, row.start)).rstrip()
    return f"{at}  {row.name}  {_tc(p, row.start)}".rstrip()


def _item_source(p, i: int) -> Optional[str]:
    items = p.params.get("items") if p.kind == "mark" else None
    if not items or i >= len(items):
        return None
    src = items[i].get("src") or {}

    def word(key: str) -> str:
        return S.PROVENANCE.get(src.get(key) or "default", S.PROVENANCE["default"])

    return S.ITEM_SOURCE.format(at=word("at"), color=word("color"), name=word("name"))


def _item_lines(p) -> List[str]:
    return [_item_line(p, i) for i in range(len(p.rows))]


def _warning_lines(p) -> List[str]:
    out = []
    for key, n in (p.warnings or {}).items():
        if key == "too_many":
            which = "too_many_pauses" if p.kind == "mark_pauses" else "too_many_spikes"
            out.append(S.WARNINGS[which].format(found=n, cap=p.count))
        elif key in S.WARNINGS:
            out.append(S.WARNINGS[key].format(n=n))
    return out


def note_lines(notes: Sequence[Tuple[str, Dict[str, Any]]], fps: float = 1.0) -> List[str]:
    """두뇌·계획이 남긴 줄 (리졸브 시간으로 봤어요, 앞뒤 5초 안에서 찾아요 …)."""
    out = []
    for code, data in notes or []:
        data = dict(data or {})
        if code == "refused":
            key = data.get("code")
            text = S.CHAT_REPLIES.get(key)
            if text:
                out.append(S.CHAT_REFUSED_ALSO.format(text=_fill(text, data)))
            continue
        tmpl = S.CARD_NOTES.get(code)
        if tmpl is None:
            continue
        if "seconds" in data and code == "tc_read":
            data["at"] = fmt.clock(float(data["seconds"]))
        data = {k: fmt.num(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else v
                for k, v in data.items()}
        line = _fill(tmpl, data)
        if line not in out:
            out.append(line)
    return out


def _fill(tmpl: str, data: Dict[str, Any]) -> str:
    try:
        return tmpl.format(**data)
    except (KeyError, IndexError, ValueError):
        return tmpl.split("{")[0].strip()


def guard_lines(guard) -> List[Tuple[str, str]]:
    """범위 지킴이 줄: (글, 역할). 막음은 error, 경고는 warning."""
    out: List[Tuple[str, str]] = []
    if guard is None:
        return out
    for g in guard.lines:
        d = dict(g.data)
        if g.code == "outside_tracks":
            d["tracks"] = ", ".join(f"A{t}" for t in d.get("tracks") or [])
        if g.code in ("whole", "half"):
            d["length"] = fmt.length(d.get("length_s"))
        text = _fill(S.GUARD.get(g.code, g.code), d)
        out.append((text, "error" if g.level == "block" else "warning"))
    return out


def voice_row_text(voice) -> str:
    why = S.VOICE_REMEMBERED if voice.remembered else S.VOICE_JUST_PICKED
    return S.VOICE_ROW.format(n=voice.stream + 1, why=why)


def _mark_rows(p, compact: bool) -> List[Tuple[str, str]]:
    items = p.params.get("items") or []
    prov = p.provenance or {}
    n = p.count
    if n == 1:
        spec = p.specs[0]
        what = tagged(S.WHAT_MARK.format(color_word=fmt.color_word(spec.color), name=spec.name),
                      (items[0].get("src") or {}).get("name") if items else None)
        row = p.rows[0]
        if row.end - row.start > 1:
            when = S.WHEN_SPAN.format(a=fmt.clock(_secs(p, row.start)), b=fmt.clock(_secs(p, row.end)))
        else:
            when = S.WHEN_POINT.format(at=fmt.clock(_secs(p, row.start)))
        when = tagged(when, (items[0].get("src") or {}).get("at") if items else None)
    else:
        what = S.WHAT_MARKS.format(n=n)
        when = S.WHEN_ITEMS.format(a=fmt.clock(_secs(p, p.rows[0].start)), b=fmt.clock(_secs(p, p.rows[-1].start)),
                                   n=n)
        when = tagged(when, prov.get("at") if prov.get("at") in S.PROVENANCE else None)
    spans = sum(1 for s in p.specs if s.dur > 1)
    points = n - spans
    if spans and points:
        how = S.HOW_MIXED.format(points=points, spans=spans)
    elif spans:
        how = S.HOW_SPAN.format(length=fmt.length(sum(r.end - r.start for r in p.rows) / (p.fps or 1.0)))
    else:
        how = S.HOW_POINT
    color_src = prov.get("color") if prov.get("color") in S.PROVENANCE else None
    resolve = tagged(S.RESOLVE_MARKS.format(colors=colors_word(p), n=n), color_src)
    rows = [(S.ROW_WHAT, what), (S.ROW_WHEN, when), (S.ROW_HOW, how), (S.ROW_TRACK, S.TRACK_NONE),
            (S.ROW_COUNT, S.COUNT_LINE.format(n=n))]
    if compact:
        rows.append((S.ROW_RESOLVE, f"{resolve} · {S.KEEP_MARKERS}"))
    else:
        rows += [(S.ROW_RESOLVE, resolve), (S.ROW_KEEP, S.KEEP_MARKERS)]
    return rows


def proposal_rows(p, replace_count: int = 0, compact: bool = False,
                  slot_name: Optional[str] = None) -> List[Tuple[str, str]]:
    """카드의 고정 줄 (설계 B4.5). compact이면 리졸브와 그대로를 한 줄로. 대화 카드는 값마다 출처."""
    if p.kind == "mark":
        return _mark_rows(p, compact)
    params = _params_text(p.params)
    prov = p.provenance if p.from_chat else {}
    color = fmt.color_word(p.color)
    if p.kind == "mark_pauses":
        what = S.WHAT_PAUSES.format(min_s=params.get("min_s", ""))
        how = S.HOW_PAUSES.format(total=fmt.length(p.total_s))
    else:
        what = S.WHAT_SPIKES.format(above_lu=params.get("above_lu", ""))
        top = max((r.value for r in p.rows), default=0.0)
        how = S.HOW_SPIKES.format(max=fmt.num(round(top, 1)))
    what = tagged(what, prov.get(MAIN_PARAM.get(p.kind, "")), slot_name)
    when = tagged(_when(p), prov.get("range"), slot_name)
    tracks = ", ".join(f"A{t}" for t in p.tracks)
    resolve = (S.RESOLVE_POINT if p.point_only else S.RESOLVE_RANGE).format(color_word=color, n=p.count)
    resolve = tagged(resolve, prov.get("color"), slot_name)
    if replace_count:
        resolve += "\n" + S.RESOLVE_REPLACE.format(color_word=color, n=replace_count)
    count = S.COUNT_LINE.format(n=p.count)
    if prov:
        count = tagged(count, prov.get("places"))
    rows = [(S.ROW_WHAT, what), (S.ROW_WHEN, when), (S.ROW_HOW, how), (S.ROW_TRACK, tracks), (S.ROW_COUNT, count)]
    if compact:
        rows.append((S.ROW_RESOLVE, f"{resolve} · {S.KEEP_MARKERS}"))
    else:
        rows += [(S.ROW_RESOLVE, resolve), (S.ROW_KEEP, S.KEEP_MARKERS)]
    return rows


def receipt_text(outcome, color: Optional[str], word: Optional[str] = None) -> str:
    """영수증 첫 줄 (다시 읽은 수로). word: 여러 색이면 "빨간·파란"."""
    word = word or fmt.color_word(color)
    if outcome.status == "applied":
        return S.RECEIPT_OK.format(at=outcome.at, color_word=word, n=outcome.placed)
    if outcome.status == "partial":
        return S.RECEIPT_PARTIAL.format(expected=outcome.expected, placed=outcome.placed)
    return S.RECEIPT_NONE


def _step_button(text: str, tip: str) -> QToolButton:
    btn = QToolButton()
    btn.setText(text)
    btn.setToolTip(tip)
    btn.setAccessibleName(tip)
    btn.setProperty("role", "step")
    return btn


class ProposalCard(Card):
    """제안 → 확인 카드 → 영수증. clicked: apply, cancel, voice, undo, resume, remove_placed, replan, force,
    view:<i>(그 줄로 재생 위치), dec:/inc:<값>(대화 카드의 [−][+]), save:<n>(자동화 n에 저장), leftover."""

    def __init__(self, proposal, *, replace_count: int = 0, compact: bool = False, guard=None,
                 save_slots: Optional[Sequence[Tuple[int, str]]] = None, slot_name: Optional[str] = None,
                 parent: Optional[QWidget] = None) -> None:
        super().__init__("", parent)
        self.proposal = proposal
        self.replace_count = replace_count
        self.compact = compact
        self.guard = guard
        self.save_slots = list(save_slots) if save_slots else None
        self.slot_name = slot_name
        self.state = "proposal"  # proposal / receipt / undone / closed
        self.outcome = None
        self.more_btn: Optional[QToolButton] = None
        self.more_list: Optional[QWidget] = None
        self.voice_btn: Optional[QPushButton] = None
        self.save_combo: Optional[QComboBox] = None
        self.item_labels: List[ClickLabel] = []
        self.show_proposal()

    @property
    def blocked(self) -> bool:
        return bool(self.guard is not None and self.guard.blocked)

    @property
    def editable(self) -> bool:
        return bool(self.proposal.from_chat) and self.state == "proposal"

    def set_proposal(self, proposal, guard=None) -> None:
        """[−][+]로 고친 새 계산 (같은 자리에서 바뀐다)."""
        self.proposal = proposal
        self.guard = guard
        self.show_proposal()

    def show_proposal(self) -> None:
        p = self.proposal
        self.clear_body()
        self.state = "proposal"
        self.more_btn = self.more_list = self.voice_btn = self.save_combo = None
        if p.count == 0:
            self.title.setText(S.CARD_TITLE_NONE)
            params = _params_text(p.params)
            text = S.CARD_NONE_PAUSES.format(min_s=params.get("min_s", "")) if p.kind == "mark_pauses" else \
                S.CARD_NONE_SPIKES.format(above_lu=params.get("above_lu", ""))
            self.add_line(text)
            if self.editable and p.kind in MAIN_PARAM:
                self._edit_row()
            self._voice_row()
            self._notes()
            for line in _warning_lines(p):
                self.add_line(line, "warning")
            self._guard()
            self.set_buttons([("cancel", S.BTN_CLOSE, False)])
            return
        if p.kind == "mark":
            self.title.setText(S.OFFER_TITLES.get(p.offer, S.CARD_TITLE_PROPOSE) if p.offer else S.CARD_TITLE_PROPOSE)
        else:
            self.title.setText(S.CARD_TITLE_FOUND)
        self.add_rows(proposal_rows(p, self.replace_count, self.compact, self.slot_name))
        if self.editable and p.kind in MAIN_PARAM:
            self._edit_row()
        if self.editable and p.offer == "audio":
            self._db_row()
        self._items()
        self._voice_row()
        self._notes()
        for line in _warning_lines(p):
            self.add_line(line, "warning")
        self._guard()
        self._save_row()
        apply_label = S.BTN_OFFER_YES if p.offer else S.BTN_APPLY
        cancel_label = S.BTN_OFFER_NO if p.offer else S.BTN_CANCEL
        self.set_buttons([("apply", apply_label, True), ("cancel", cancel_label, False)],
                         blocked_while_busy=("apply",))

    # ── 줄들 ──────────────────────────────────────────────────────────

    def _row_box(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(4)
        self.body_box.addLayout(row)
        return row

    def _edit_row(self) -> None:
        """쉰 길이·튀는 정도 [−] 값 [+] (다시 계산, AI를 부르지 않음)."""
        p = self.proposal
        key = MAIN_PARAM[p.kind]
        row = self._row_box()
        name = _label(S.EDIT_MIN_S if key == "min_s" else S.EDIT_ABOVE, "secondary", wrap=False)
        name.setSizePolicy(QSizePolicy.Minimum, QSizePolicy.Preferred)
        row.addWidget(name)
        row.addWidget(self.add_extra(f"dec:{key}", _step_button(S.BTN_MINUS, S.TIP_MINUS_VALUE)))
        value = (S.EDIT_VALUE_S if key == "min_s" else S.EDIT_VALUE_DB).format(v=fmt.num(p.params.get(key)))
        row.addWidget(_label(tagged(value, (p.provenance or {}).get(key), self.slot_name)), 1)
        row.addWidget(self.add_extra(f"inc:{key}", _step_button(S.BTN_PLUS, S.TIP_PLUS_VALUE)))

    def _db_row(self) -> None:
        """권하는 노란 표시의 이름에 적을 크기 [−] 6dB [+] (소리는 바꾸지 않는다)."""
        p = self.proposal
        db = (p.params.get("offer") or {}).get("db")
        if db is None:
            return
        row = self._row_box()
        name = _label(S.EDIT_DB, "secondary", wrap=False)
        name.setSizePolicy(QSizePolicy.Minimum, QSizePolicy.Preferred)
        row.addWidget(name)
        row.addWidget(self.add_extra("dec:db", _step_button(S.BTN_MINUS, S.TIP_MINUS_VALUE)))
        row.addWidget(_label(tagged(S.EDIT_VALUE_DB.format(v=fmt.num(db)), (p.provenance or {}).get("db"))), 1)
        row.addWidget(self.add_extra("inc:db", _step_button(S.BTN_PLUS, S.TIP_PLUS_VALUE)))

    def _items(self) -> None:
        p = self.proposal
        self.item_labels = []
        n = len(p.rows)
        if not n:
            return
        head = QWidget()
        head_box = QVBoxLayout(head)
        head_box.setContentsMargins(0, 0, 0, 0)
        head_box.setSpacing(2)
        self.body_box.addWidget(head)
        rest_box = None
        if n > INLINE_ITEMS:
            self.more_btn = QToolButton()
            self.more_btn.setText(S.COUNT_MORE.format(n=n - INLINE_ITEMS))
            self.more_btn.setCheckable(True)
            self.more_list = QWidget()
            rest_box = QVBoxLayout(self.more_list)
            rest_box.setContentsMargins(0, 0, 0, 0)
            rest_box.setSpacing(2)
            self.more_list.setVisible(False)
            self.more_btn.toggled.connect(self._toggle_more)
            self.body_box.addWidget(self.more_btn, 0, Qt.AlignLeft)
            self.body_box.addWidget(self.more_list)
        for i in range(n):
            box = head_box if i < INLINE_ITEMS else rest_box
            self._item_row(box, i)

    def _item_row(self, box: QVBoxLayout, i: int) -> None:
        p = self.proposal
        row = QHBoxLayout()
        row.setSpacing(4)
        line = ClickLabel(_item_line(p, i))
        line.setToolTip(S.TIP_VIEW)
        line.clicked.connect(lambda k=f"view:{i}": self._press(k))
        self.item_labels.append(line)
        row.addWidget(line, 1)
        if self.editable and p.kind == "mark":
            row.addWidget(self.add_extra(f"dec:at:{i}", _step_button(S.BTN_MINUS, S.TIP_MINUS_TIME)))
            row.addWidget(self.add_extra(f"inc:at:{i}", _step_button(S.BTN_PLUS, S.TIP_PLUS_TIME)))
        view = _step_button(S.BTN_VIEW_SHORT, S.TIP_VIEW)
        row.addWidget(self.add_extra(f"view:{i}", view, blocked_while_busy=False))
        box.addLayout(row)
        if p.from_chat and p.kind == "mark":
            src = _item_source(p, i)
            if src:
                box.addWidget(_label(src, "secondary"))

    def _toggle_more(self, on: bool) -> None:
        rest = len(self.proposal.rows) - INLINE_ITEMS
        self.more_btn.setText(S.COUNT_LESS if on else S.COUNT_MORE.format(n=rest))
        self.more_list.setVisible(on)

    def _notes(self) -> None:
        p = self.proposal
        for line in note_lines(getattr(p, "notes", None) or [], p.fps):
            self.add_line(line, "secondary")
        kept = (p.debug or {}).get("count_kept") if hasattr(p, "debug") else None
        if kept:
            self.add_line(_fill(S.CARD_NOTES["count_kept"], kept), "secondary")

    def _guard(self) -> None:
        for text, role in guard_lines(self.guard):
            self.add_line(text, role)
        if self.guard is not None and any(g.code == "leftover" for g in self.guard.lines):
            btn = QToolButton()
            btn.setText(S.CHIP_LEFTOVER)
            btn.setProperty("role", "chip")
            self.body_box.addWidget(self.add_extra("leftover", btn, blocked_while_busy=False), 0, Qt.AlignLeft)

    def _save_row(self) -> None:
        """이대로 자동화 버튼에 저장 ▸ [자동화 n] [저장] (대화에서 온 쉬는 곳·튀는 소리 카드)."""
        p = self.proposal
        if not self.save_slots or p.kind not in MAIN_PARAM:
            return
        row = self._row_box()
        row.addWidget(_label(S.SAVE_ROW, "secondary"), 1)
        self.save_combo = QComboBox()
        for number, label in self.save_slots:
            self.save_combo.addItem(label, number)
        self.save_combo.setAccessibleName(S.SAVE_CHOOSE)
        self.save_combo.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        row.addWidget(self.save_combo)
        save = QPushButton(S.BTN_SAVE)
        save.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)
        save.clicked.connect(self._press_save)
        self.extra["save"] = save
        self._apply_enabled()
        row.addWidget(save)

    def _press_save(self) -> None:
        if self.save_combo is not None:
            self._press(f"save:{self.save_combo.currentData()}")

    def choose_save_slot(self, number: int) -> None:
        if self.save_combo is not None:
            i = self.save_combo.findData(number)
            if i >= 0:
                self.save_combo.setCurrentIndex(i)

    def _voice_row(self) -> None:
        v = getattr(self.proposal, "voice", None)
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
        if "apply" in self.buttons and self.blocked:
            self.buttons["apply"].setEnabled(False)  # 요청하신 범위 밖이 바뀜: 넣을 수 없다
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
        self.voice_btn = self.save_combo = None
        self.more_btn = self.more_list = None
        p = self.proposal
        word = colors_word(p)
        self.title.setText(receipt_text(outcome, p.color, word))
        lines: List[Tuple[str, Optional[str]]] = []
        if outcome.status == "partial":
            lines.append((S.RECEIPT_OK.format(at=outcome.at, color_word=word, n=outcome.placed), None))
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
        if outcome.placed:
            self._items()  # 영수증의 줄을 누르면 재생 위치가 그곳으로 (설계 부록 A 5장 12번)
        if outcome.status in ("applied", "partial"):
            self._save_row()
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
        self.title.setText(S.RECEIPT_UNDONE.format(at=at, color_word=colors_word(self.proposal), n=undo.deleted))
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

    def cancel(self, note: Optional[str] = None) -> None:
        self.state = "closed"
        self.close_card(note or S.CARD_CANCELLED)

    def replaced(self) -> None:
        """다시 눌러 [바꾸기]로 이 영수증의 표시가 새것으로 바뀜."""
        if self.state == "receipt":
            self.state = "undone"
            self.close_card(S.CARD_REPLACED)

    def reopened(self, note: str) -> None:
        """지우기를 되돌려 표시가 다시 들어옴: 영수증으로 돌아가지는 않고 한 줄만 적는다."""
        self.add_line(note, "secondary")


# ── 도우미 표시 지우기 카드 ────────────────────────────────────────────

def clear_rows(plan, compact: bool = False) -> List[Tuple[str, str]]:
    prov = plan.provenance or {}
    params = plan.params or {}
    parts = []
    if params.get("colors"):
        parts.append(S.CLEAR_COLORS.format(colors=S.COLOR_JOIN.join(fmt.color_word(c) for c in params["colors"])))
    for k in params.get("kinds") or []:
        parts.append(S.CLEAR_KINDS.get(k, k))
    what = S.CLEAR_WHAT_FILTER.format(what=" · ".join(parts)) if parts else S.CLEAR_WHAT_ALL
    src = prov.get("colors") or prov.get("kinds")
    what = tagged(what, src)
    fps = plan.fps or 1.0
    if params.get("lo") is not None:
        a, b = fmt.clock((params["lo"] - plan.tl_start) / fps), fmt.clock((params["hi"] - plan.tl_start) / fps)
        when = tagged(S.WHEN_RANGE.format(a=a, b=b), "said")
    else:
        when = tagged(S.WHEN_WHOLE.format(length=fmt.length((plan.tl_end - plan.tl_start) / fps)), "default")
    rows = [(S.ROW_WHAT, what), (S.ROW_WHEN, when), (S.ROW_TRACK, S.TRACK_NONE),
            (S.ROW_COUNT, tagged(S.COUNT_LINE.format(n=plan.count), "found"))]
    resolve = S.CLEAR_RESOLVE.format(n=plan.count)
    if compact:
        rows.append((S.ROW_RESOLVE, f"{resolve} · {S.CLEAR_KEEP}"))
    else:
        rows += [(S.ROW_RESOLVE, resolve), (S.ROW_KEEP, S.CLEAR_KEEP)]
    return rows


class ClearCard(Card):
    """도우미 표시 지우기: 계획 → 확인 카드 → 영수증 → 되돌리기 (다시 넣기).
    clicked: apply, cancel, undo, view:<i>, leftover."""

    def __init__(self, plan, *, guard=None, compact: bool = False, parent: Optional[QWidget] = None) -> None:
        super().__init__("", parent)
        self.proposal = plan  # 창이 제안 카드와 같은 이름으로 다룬다
        self.guard = guard
        self.compact = compact
        self.state = "proposal"
        self.outcome = None
        self.more_btn: Optional[QToolButton] = None
        self.more_list: Optional[QWidget] = None
        self.show_plan()

    @property
    def blocked(self) -> bool:
        return bool(self.guard is not None and self.guard.blocked)

    def _line(self, i: int) -> str:
        p = self.proposal
        t = p.targets[i]
        at = fmt.clock((t["frame"] - p.tl_start) / (p.fps or 1.0))
        return S.ITEM_LINE.format(at=at, color_word=fmt.color_word(t.get("color")), name=t.get("name") or "",
                                  tc=_tc(p, t["frame"])).rstrip()

    def show_plan(self) -> None:
        p = self.proposal
        self.clear_body()
        self.state = "proposal"
        if p.count == 0:
            self.title.setText(S.CARD_TITLE_CLEAR_NONE)
            self.add_line(S.CLEAR_NONE.format(n=p.total_ours))
            if p.straddling:
                self.add_line(S.CLEAR_STRADDLE.format(n=p.straddling), "secondary")
            for text, role in guard_lines(self.guard):
                self.add_line(text, role)
            self.set_buttons([("cancel", S.BTN_CLOSE, False)])
            return
        self.title.setText(S.CARD_TITLE_CLEAR)
        self.add_rows(clear_rows(p, self.compact))
        self._targets()
        if p.straddling:
            self.add_line(S.CLEAR_STRADDLE.format(n=p.straddling), "secondary")
        for line in note_lines(p.notes, p.fps):
            self.add_line(line, "secondary")
        for text, role in guard_lines(self.guard):
            self.add_line(text, role)
        if self.guard is not None and any(g.code == "leftover" for g in self.guard.lines):
            btn = QToolButton()
            btn.setText(S.CHIP_LEFTOVER)
            btn.setProperty("role", "chip")
            self.body_box.addWidget(self.add_extra("leftover", btn, blocked_while_busy=False), 0, Qt.AlignLeft)
        self.set_buttons([("apply", S.BTN_APPLY, True), ("cancel", S.BTN_CANCEL, False)],
                         blocked_while_busy=("apply",))

    def _targets(self) -> None:
        n = self.proposal.count
        head = QWidget()
        head_box = QVBoxLayout(head)
        head_box.setContentsMargins(0, 0, 0, 0)
        head_box.setSpacing(2)
        self.body_box.addWidget(head)
        rest_box = None
        if n > INLINE_ITEMS:
            self.more_btn = QToolButton()
            self.more_btn.setText(S.COUNT_MORE.format(n=n - INLINE_ITEMS))
            self.more_btn.setCheckable(True)
            self.more_list = QWidget()
            rest_box = QVBoxLayout(self.more_list)
            rest_box.setContentsMargins(0, 0, 0, 0)
            self.more_list.setVisible(False)
            self.more_btn.toggled.connect(self._toggle_more)
            self.body_box.addWidget(self.more_btn, 0, Qt.AlignLeft)
            self.body_box.addWidget(self.more_list)
        for i in range(n):
            row = QHBoxLayout()
            row.setSpacing(4)
            line = ClickLabel(self._line(i))
            line.setToolTip(S.TIP_VIEW)
            line.clicked.connect(lambda k=f"view:{i}": self._press(k))
            row.addWidget(line, 1)
            row.addWidget(self.add_extra(f"view:{i}", _step_button(S.BTN_VIEW_SHORT, S.TIP_VIEW),
                                         blocked_while_busy=False))
            (head_box if i < INLINE_ITEMS else rest_box).addLayout(row)

    def _toggle_more(self, on: bool) -> None:
        rest = self.proposal.count - INLINE_ITEMS
        self.more_btn.setText(S.COUNT_LESS if on else S.COUNT_MORE.format(n=rest))
        self.more_list.setVisible(on)

    def _apply_enabled(self) -> None:
        super()._apply_enabled()
        if "apply" in self.buttons and self.blocked:
            self.buttons["apply"].setEnabled(False)

    def show_receipt(self, outcome) -> None:
        self.outcome = outcome
        self.state = "receipt"
        self.clear_body()
        if outcome.status == "applied":
            self.title.setText(S.CLEAR_RECEIPT.format(at=outcome.at, n=outcome.deleted))
        elif outcome.status == "partial":
            self.title.setText(S.CLEAR_RECEIPT_PARTIAL.format(expected=outcome.expected, n=outcome.deleted))
        else:
            self.title.setText(S.CLEAR_RECEIPT_NONE)
        if outcome.closed:
            self.add_line(S.CLEAR_CLOSED.format(n=len(outcome.closed)), "secondary")
        if outcome.error and outcome.status != "applied":
            self.add_line(S.RECEIPT_ERROR.format(reason=outcome.error), "secondary")
        if outcome.deleted:
            self.set_buttons([("undo", S.BTN_UNDO_ONE, False)], blocked_while_busy=("undo",))
        else:
            self.set_buttons([])

    def show_undone(self, undo, at: str) -> None:
        self.state = "undone"
        self.clear_body()
        self.title.setText(S.CLEAR_UNDONE.format(at=at, n=undo.deleted))
        if undo.already_gone:
            self.add_line(S.CLEAR_UNDO_SKIPPED.format(n=undo.already_gone), "secondary")
        if undo.remaining:
            self.add_line(S.CLEAR_UNDO_LEFT.format(n=undo.remaining), "warning")
        self.set_buttons([])

    def show_message(self, text: str, role: Optional[str] = "warning") -> None:
        self.add_line(text, role)
        self.unlock()

    def cancel(self, note: Optional[str] = None) -> None:
        self.state = "closed"
        self.close_card(note or S.CARD_CANCELLED)

    def supersede(self) -> None:
        if self.state == "proposal":
            self.state = "closed"
            self.close_card(S.CARD_SUPERSEDED)
            self.setEnabled(False)
