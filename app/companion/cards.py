"""대화 칸에 뜨는 카드 (설계 B4.5, B2.5 4~7).

- Card: 제목, 표(할 일/언제/얼마나/트랙/몇 곳/리졸브/그대로), 알림 줄, 단추 줄. 단추를 누르면
  clicked(열쇠)를 보낸다. 단추는 누른 뒤 창이 새 모양으로 바꿀 때까지 한 번만 눌린다.
- ProposalCard: 제안(engine.edits.proposal.Proposal) → 확인 카드 → 영수증 (같은 자리에서 바뀐다).
  영수증은 계획이 아니라 리졸브에서 다시 읽은 결과(ApplyOutcome)로 쓴다.
- QuestionCard: 짧은 질문과 답 단추 (다시 누를 때 [바꾸기] [더하기], 확인 질문 M2/M3 등).

글은 모두 strings_ko.py에서 온다. 리졸브에는 아무것도 묻지 않는다 (단추를 누르면 창이 일을 시작한다).

표시 줄마다 [이동](리졸브에서 보기: 재생 위치만 옮김, jump_to)이 있고 줄을 눌러도 된다 (2.1c).
리졸브 타임코드는 줄의 풍선 도움말에 둔다 (좁은 창에서 줄이 두 줄로 접히지 않게).
대화에서 온 카드는 도우미가 정한 값에만 출처(기본값 / 설정값 / 재생 위치)를 붙이고 (적으신 값은 그대로),
범위 지킴이의 막음(빨강, [리졸브에 넣기]를 누를 수 없음)과 경고(주황)를 보이고, 숫자를 [−][+]로 고칠 수 있다.
단추 줄은 창이 좁으면 다음 줄로 넘어간다 (FlowLayout): 380px 창에서도 오른쪽이 잘리지 않는다.
쉬는 곳·튀는 소리 카드에는 단추 줄 아래(foot)에 "이대로 자동화 버튼에 저장"이 붙는다 (구간은 저장하지 않는다).
주된 단추([리졸브에 넣기])가 저장 줄보다 위에 있어, 카드가 대화 칸보다 조금 커도 맨 위부터 보면 단추가 보인다.
표시 목록은 세 줄까지 바로 보이고 나머지는 글자 단추 "▸ N곳 더 보기" 뒤에 (숨길 줄이 하나뿐이면 다 보인다).
영수증의 [되돌리기]는 묻지 않고 바로 뺀다 (그 카드의 것만, 되돌리기 목록 쪽은 먼저 묻는다).
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from PySide6.QtCore import QPoint, QRect, QSize, Qt, Signal
from PySide6.QtWidgets import (
    QAbstractButton,
    QComboBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLayout,
    QPushButton,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from . import fmt
from . import strings_ko as S

INLINE_ITEMS = 3  # 몇 곳: 처음 세 곳은 바로 보이고 나머지는 ▸
TAGGED_SOURCES = ("default", "setting", "playhead")  # 도우미가 정한 값에만 출처를 붙인다 (적으신 값은 그대로)


class FlowLayout(QLayout):
    """단추를 왼쪽부터 늘어놓고, 자리가 모자라면 다음 줄로 넘긴다 (좁은 창에서 잘리지 않게).

    최소 너비는 가장 넓은 단추 하나의 너비다. 높이는 너비에 따라 바뀐다 (heightForWidth).
    """

    def __init__(self, parent: Optional[QWidget] = None, spacing: int = 6) -> None:
        super().__init__(parent)
        self._items: List[Any] = []
        self._spacing = spacing
        self.setContentsMargins(0, 0, 0, 0)

    def addItem(self, item) -> None:  # noqa: N802 - Qt 이름
        self._items.append(item)

    def count(self) -> int:
        return len(self._items)

    def itemAt(self, index: int):  # noqa: N802
        return self._items[index] if 0 <= index < len(self._items) else None

    def takeAt(self, index: int):  # noqa: N802
        if 0 <= index < len(self._items):
            item = self._items.pop(index)
            self.invalidate()
            return item
        return None

    def spacing(self) -> int:
        return self._spacing

    def expandingDirections(self):  # noqa: N802
        return Qt.Orientation(0)

    def hasHeightForWidth(self) -> bool:  # noqa: N802
        return True

    def heightForWidth(self, width: int) -> int:  # noqa: N802
        return self._do_layout(QRect(0, 0, width, 0), apply=False)

    def setGeometry(self, rect: QRect) -> None:  # noqa: N802
        super().setGeometry(rect)
        self._do_layout(rect, apply=True)

    def sizeHint(self) -> QSize:  # noqa: N802
        w = h = 0
        shown = [i for i in self._items if not i.isEmpty()]
        for item in shown:
            hint = item.sizeHint()
            w += hint.width()
            h = max(h, hint.height())
        w += self._spacing * max(0, len(shown) - 1)
        m = self.contentsMargins()
        return QSize(w + m.left() + m.right(), h + m.top() + m.bottom())

    def minimumSize(self) -> QSize:  # noqa: N802
        size = QSize(0, 0)
        for item in self._items:
            if not item.isEmpty():
                size = size.expandedTo(item.minimumSize())
        m = self.contentsMargins()
        return size + QSize(m.left() + m.right(), m.top() + m.bottom())

    def _do_layout(self, rect: QRect, apply: bool) -> int:
        m = self.contentsMargins()
        area = rect.adjusted(m.left(), m.top(), -m.right(), -m.bottom())
        x, y, line_h = area.x(), area.y(), 0
        shown = [i for i in self._items if not i.isEmpty()]
        for item in shown:
            hint = item.sizeHint()
            w = min(hint.width(), max(item.minimumSize().width(), area.width()))
            if x > area.x() and x + w > area.x() + area.width():
                x = area.x()
                y += line_h + self._spacing
                line_h = 0
            if apply:
                item.setGeometry(QRect(QPoint(x, y), QSize(w, hint.height())))
            x += w + self._spacing
            line_h = max(line_h, hint.height())
        return (y + line_h - area.y() if shown else 0) + m.top() + m.bottom()


def _label(text: str, role: Optional[str] = None, wrap: bool = True) -> QLabel:
    lab = QLabel(text)
    lab.setWordWrap(wrap)
    lab.setTextInteractionFlags(Qt.TextSelectableByMouse)
    lab.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
    if role:
        lab.setProperty("role", role)
    return lab


def _value_label(text: str) -> QLabel:
    """[−] 값 [+]의 값: 글 너비만큼 (줄을 바꾸지 않고, [+]가 값 바로 옆에 오게)."""
    lab = QLabel(text)
    lab.setTextInteractionFlags(Qt.TextSelectableByMouse)
    lab.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Preferred)
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
    """카드 한 장. 열쇠로 단추를 찾고, plain_text()는 결과 파일과 시험에 쓴다.

    closed(남긴 줄): 더 할 것이 없게 됨 (답함, 취소, 바뀜). 창은 이 카드가 띄운 머리말 알림을 이것으로 고친다.
    """

    clicked = Signal(str)
    closed = Signal(str)

    def __init__(self, title: str = "", parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("card")
        self.setFrameShape(QFrame.StyledPanel)
        # 세로는 Preferred: Maximum이면 한 줄짜리 sizeHint가 최대 높이가 되어, 좁은 창에서 버튼 줄이
        # 두 줄로 쌓일 때 카드 아래가 잘린다. 남는 자리는 대화 목록 맨 위의 빈칸(stretch)이 가져간다.
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
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
        self.button_row = FlowLayout(spacing=6)
        self.box.addLayout(self.button_row)
        # 단추 줄 아래 (자동화 버튼에 저장처럼 주된 단추보다 덜 급한 줄). 비어 있으면 숨긴다
        self.foot = QWidget()
        self.foot_box = QVBoxLayout(self.foot)
        self.foot_box.setContentsMargins(0, 4, 0, 0)
        self.foot_box.setSpacing(4)
        self.foot.setVisible(False)
        self.box.addWidget(self.foot)
        self.buttons: Dict[str, QPushButton] = {}
        self.extra: Dict[str, QAbstractButton] = {}  # 줄 안의 단추 ([−][+], 보기, 저장): 열쇠 → 단추
        self._extra_busy: List[str] = []  # 다른 일을 하는 동안 꺼 둘 줄 안 단추
        self._closing_line: Optional[QLabel] = None  # 닫을 때 남긴 줄 (빼는 중이에요 → 결과로 바뀐다)
        self.more_btn: Optional[QToolButton] = None  # 표시 목록의 "▸ N곳 더 보기"
        self.more_list: Optional[QWidget] = None
        self._inline_items = INLINE_ITEMS
        self.busy = False
        self.locked = False  # 눌러서 일을 시작했음 (끝나면 창이 새 모양으로 바꾼다)
        self._primary: List[str] = []  # 작업 중에 꺼 둘 단추 (리졸브에 넣기, 되돌리기 ...)

    # ── 내용 ──────────────────────────────────────────────────────────

    def clear_body(self) -> None:
        _drop_layout(self.body_box)
        _drop_layout(self.foot_box)
        self.foot.setVisible(False)
        self.drop_extras()

    def add_line(self, text: str, role: Optional[str] = None) -> QLabel:
        lab = _label(text, role)
        self.body_box.addWidget(lab)
        return lab

    def add_rows(self, rows: Sequence[Tuple[str, str]], tips: Optional[Dict[str, str]] = None) -> QGridLayout:
        """(열쇠, 값) 표. tips: 열쇠 → 값 줄의 풍선 도움말 (좁은 모양에서 줄 대신 보이는 설명)."""
        grid = QGridLayout()
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(2)
        for r, (key, value) in enumerate(rows):
            k = _label(key, "secondary", wrap=False)
            k.setSizePolicy(QSizePolicy.Minimum, QSizePolicy.Preferred)
            grid.addWidget(k, r, 0, Qt.AlignTop)
            v = _label(value)
            tip = (tips or {}).get(key)
            if tip:
                v.setToolTip(tip)
            grid.addWidget(v, r, 1)
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
            btn.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
            btn.clicked.connect(lambda _=False, k=key: self._press(k))
            self.button_row.addWidget(btn)
            self.buttons[key] = btn
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

    def close_card(self, note: Optional[str] = None, *, announce: Optional[str] = None) -> None:
        """더 할 것이 없는 카드: 단추를 없애고 (있으면) 한 줄을 남긴다. 줄의 [이동]만 남긴다.

        announce: closed로 알릴 글 (없으면 남긴 줄)."""
        self.set_buttons([])
        for key in [k for k in self.extra if not k.startswith("view:")]:
            btn = self.extra.pop(key)
            try:
                btn.hide()
            except RuntimeError:
                pass
        self.foot.setVisible(False)  # 저장 줄 같은 뒤따르는 줄도 닫는다
        self._closing_line = self.add_line(note, "secondary") if note else None
        self.closed.emit(announce if announce is not None else (note or ""))

    def set_note(self, text: str, role: Optional[str] = "secondary") -> QLabel:
        """닫힌 카드의 결과 한 줄: 닫을 때 남긴 "하는 중" 줄을 결과로 바꾼다 (이걸 뺄까요? → 빼는 중이에요 → 뺐어요).
        그 줄이 없으면 한 줄 더한다."""
        line, self._closing_line = self._closing_line, None
        if line is not None:
            try:
                line.setText(text)
                line.setProperty("role", role)
                line.style().unpolish(line)
                line.style().polish(line)
                return line
            except RuntimeError:
                pass
        return self.add_line(text, role)

    def plain_text(self) -> str:
        parts = [self.title.text()]
        parts += _texts(self.body)
        parts += [f"[{b.text()}]" for b in self.buttons.values()]
        if not self.foot.isHidden():
            parts += _texts(self.foot)
        # 줄 바꿈을 막으려고 넣은 빈칸(NBSP)은 여느 빈칸으로 (결과 파일과 시험은 글만 본다)
        return "\n".join(p for p in parts if p).replace("\u00a0", " ")

    # ── 표시 목록 (▸ 더 보기) ─────────────────────────────────────────

    def add_item_list(self, n: int, add_row) -> None:
        """표시 목록 n줄: 처음 몇 줄은 바로, 나머지는 "▸ N곳 더 보기"(글자 단추) 뒤에.

        숨길 줄이 하나뿐이면 다 보인다: 단추가 그 한 줄보다 커서 자리도 못 줄이고 한 번 더 누르게만 한다.
        add_row(상자, i)가 i번째 줄을 그 상자에 넣는다."""
        self.more_btn = self.more_list = None
        head = QWidget()
        head_box = QVBoxLayout(head)
        head_box.setContentsMargins(0, 0, 0, 0)
        head_box.setSpacing(2)
        self.body_box.addWidget(head)
        inline = n if n <= INLINE_ITEMS + 1 else INLINE_ITEMS
        self._inline_items = inline
        rest_box = None
        if n > inline:
            self.more_btn = QToolButton()
            self.more_btn.setText(S.COUNT_MORE.format(n=n - inline))
            self.more_btn.setCheckable(True)
            self.more_btn.setProperty("role", "link")  # "자세히 ▸"와 같은 글자 단추 (카드의 주된 단추처럼 보이지 않게)
            self.more_list = QWidget()
            rest_box = QVBoxLayout(self.more_list)
            rest_box.setContentsMargins(0, 0, 0, 0)
            rest_box.setSpacing(2)
            self.more_list.setVisible(False)
            self.more_btn.toggled.connect(lambda on, total=n: self._toggle_more_items(on, total))
            self.body_box.addWidget(self.more_btn, 0, Qt.AlignLeft)
            self.body_box.addWidget(self.more_list)
        for i in range(n):
            add_row(head_box if i < inline else rest_box, i)

    def _toggle_more_items(self, on: bool, total: int) -> None:
        if self.more_btn is None or self.more_list is None:
            return
        self.more_btn.setText(S.COUNT_LESS if on else S.COUNT_MORE.format(n=total - self._inline_items))
        self.more_list.setVisible(on)


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
    """도우미가 정한 값 뒤에 출처 (설계 B4.4): "초록 표시 1개 (기본값)". 적으신 값·찾은 값은 그대로."""
    if not src or src not in TAGGED_SOURCES or src not in S.PROVENANCE:
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
    """목록 한 줄: "3:20.0  빨간 '표시'" (리졸브 타임코드는 _item_tip의 풍선 도움말에)."""
    row = p.rows[i]
    at = fmt.clock(_secs(p, row.start))
    if p.kind == "mark":
        spec = p.specs[i]
        return S.ITEM_LINE.format(at=at, color_word=fmt.color_word(spec.color), name=row.name).rstrip()
    return f"{at}  {row.name}".rstrip()


def _item_tip(p, frame: int) -> str:
    return S.ITEM_TIP.format(tc=_tc(p, frame), tip=S.TIP_VIEW)


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
    """틀에 값을 넣고 조사를 받침에 맞춘다 (값이 모자라면 틀의 앞부분만)."""
    try:
        return S.josa(tmpl.format(**data))
    except (KeyError, IndexError, ValueError):
        return S.josa(tmpl.split("{")[0].strip())


def guard_lines(guard, skip_whole: bool = False) -> List[Tuple[str, str]]:
    """범위 지킴이 줄: (글, 역할). 막음은 error, 경고는 warning.

    skip_whole: 언제 줄이 이미 "전체 …"라고 말할 때 같은 말("전체 …에 적용돼요")을 되풀이하지 않는다.
    """
    out: List[Tuple[str, str]] = []
    if guard is None:
        return out
    for g in guard.lines:
        if skip_whole and g.code == "whole":
            continue
        d = dict(g.data)
        if g.code == "outside_tracks":
            d["tracks"] = ", ".join(f"A{t}" for t in d.get("tracks") or [])
        if g.code in ("whole", "half"):
            d["length"] = fmt.length(d.get("length_s"))
        text = _fill(S.GUARD.get(g.code, g.code), d)
        out.append((text, "error" if g.level == "block" else "warning"))
    return out


def voice_row_text(voice, tracks: Sequence[int] = ()) -> str:
    """목소리 줄: "소리 2 · A2 (지난번 선택)". tracks: 그 소리가 놓인 타임라인 트랙 (없으면 소리 번호만)."""
    why = S.VOICE_REMEMBERED if voice.remembered else S.VOICE_JUST_PICKED
    if tracks:
        words = S.COLOR_JOIN.join(S.TRACK_WORD.format(t=t) for t in tracks)
        return S.VOICE_ROW_TRACKS.format(n=voice.stream + 1, tracks=words, why=why)
    return S.VOICE_ROW.format(n=voice.stream + 1, why=why)


def _whole_scope(p) -> bool:
    return (p.scope or {}).get("kind") not in ("in_out", "range") or (p.scope or {}).get("lo") is None


def _mark_rows(p, compact: bool) -> List[Tuple[str, str]]:
    items = p.params.get("items") or []
    prov = p.provenance or {}
    n = p.count
    if n == 1:
        spec = p.specs[0]
        # 할 일에는 이름의 출처를 붙이지 않는다 (색·시각은 아래 줄에)
        what = S.WHAT_MARK.format(color_word=fmt.color_word(spec.color), name=spec.name)
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
        total = fmt.length(sum(r.end - r.start for r in p.rows) / (p.fps or 1.0))
        how = (S.HOW_SPAN if n == 1 else S.HOW_SPANS).format(length=total)
    else:
        how = S.HOW_POINT
    color_src = prov.get("color") if prov.get("color") in S.PROVENANCE else None
    resolve = tagged(S.RESOLVE_MARKS.format(colors=colors_word(p), n=n), color_src)
    return [(S.ROW_WHAT, what), (S.ROW_WHEN, when), (S.ROW_HOW, how), (S.ROW_RESOLVE, resolve)]


def proposal_rows(p, replace_count: int = 0, compact: bool = False,
                  slot_name: Optional[str] = None) -> List[Tuple[str, str]]:
    """카드의 고정 줄 (설계 B4.5): 할 일 · 언제 · 얼마나 · 리졸브에 넣을 것.

    몇 곳은 리졸브 줄의 "파란 표시 12개"가, 트랙은 목소리 줄이 말한다. "안 바뀌는 것"은 카드가 줄 아래에
    작은 글로(좁은 모양에서는 리졸브 줄의 풍선 도움말로) 붙인다. 대화 카드는 도우미가 정한 값에만 출처.
    """
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
    resolve = (S.RESOLVE_POINT if p.point_only else S.RESOLVE_RANGE).format(color_word=color, n=p.count)
    resolve = tagged(resolve, prov.get("color"), slot_name)
    if replace_count:
        resolve += "\n" + S.RESOLVE_REPLACE.format(color_word=color, n=replace_count)
    return [(S.ROW_WHAT, what), (S.ROW_WHEN, when), (S.ROW_HOW, how), (S.ROW_RESOLVE, resolve)]


def receipt_text(outcome, color: Optional[str], word: Optional[str] = None) -> str:
    """영수증 첫 줄 (다시 읽은 수로). word: 여러 색이면 "빨간·파란"."""
    word = word or fmt.color_word(color)
    if outcome.status == "applied":
        return S.RECEIPT_OK.format(at=fmt.clock_time(outcome.at), color_word=word, n=outcome.placed)
    if outcome.status == "partial":
        return S.RECEIPT_PARTIAL.format(expected=outcome.expected, placed=outcome.placed)
    return S.RECEIPT_NONE


def _step_button(text: str, tip: str) -> QToolButton:
    """카드 안의 작은 단추 ([−][+], 이동): 높이 32 (카드가 대화 칸보다 커지지 않게)."""
    btn = QToolButton()
    btn.setText(text)
    btn.setToolTip(tip)
    btn.setAccessibleName(tip)
    btn.setProperty("role", "step")
    btn.setProperty("box", "small")  # "size"는 QWidget의 크기 속성이라 쓰지 않는다
    return btn


def _keep_line(card: "Card", text: str, compact: bool, grid: Optional[QGridLayout]) -> None:
    """안 바뀌는 것: 줄 아래 작은 글 (좁은 모양이면 리졸브 줄의 풍선 도움말)."""
    if compact:
        if grid is not None and grid.rowCount():
            item = grid.itemAtPosition(grid.rowCount() - 1, 1)
            if item is not None and item.widget() is not None:
                item.widget().setToolTip(text)
        return
    card.add_line(text, "secondary")


class ProposalCard(Card):
    """제안 → 확인 카드 → 영수증. clicked: apply, cancel, voice, undo, resume, remove_placed, replan, force,
    view:<i>(그 줄로 재생 위치), dec:/inc:<값>(대화 카드의 [−][+]), save:<n>(자동화 n에 저장), leftover."""

    def __init__(self, proposal, *, replace_count: int = 0, compact: bool = False, guard=None,
                 save_slots: Optional[Sequence[Tuple[int, str]]] = None, slot_name: Optional[str] = None,
                 doubled_note: Optional[str] = None, add_note: Optional[str] = None,
                 parent: Optional[QWidget] = None) -> None:
        super().__init__("", parent)
        self.proposal = proposal
        self.replace_count = replace_count
        self.compact = compact
        self.guard = guard
        self.save_slots = list(save_slots) if save_slots else None
        self.slot_name = slot_name
        self.state = "proposal"  # proposal / receipt / checking(답이 끊겨 모름) / undone / closed
        self.outcome = None
        self.more_btn: Optional[QToolButton] = None
        self.more_list: Optional[QWidget] = None
        self.voice_btn: Optional[QPushButton] = None
        self.save_combo: Optional[QComboBox] = None
        self.item_labels: List[ClickLabel] = []
        self.doubled_note = doubled_note  # 목소리가 두 번 들릴 수 있음 (창이 세션에 한 번만 붙인다)
        self.add_note = add_note  # [더하기]: 이미 표시가 있는 곳은 뺐어요
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
        grid = self.add_rows(proposal_rows(p, self.replace_count, self.compact, self.slot_name))
        _keep_line(self, S.KEEP_MARKERS, self.compact, grid)
        if self.add_note:
            self.add_line(self.add_note, "secondary")
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
        row.addWidget(_value_label(tagged(value, (p.provenance or {}).get(key), self.slot_name)))
        row.addWidget(self.add_extra(f"inc:{key}", _step_button(S.BTN_PLUS, S.TIP_PLUS_VALUE)))
        row.addStretch(1)

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
        row.addWidget(_value_label(tagged(S.EDIT_VALUE_DB.format(v=fmt.num(db)), (p.provenance or {}).get("db"))))
        row.addWidget(self.add_extra("inc:db", _step_button(S.BTN_PLUS, S.TIP_PLUS_VALUE)))
        row.addStretch(1)

    def _items(self) -> None:
        p = self.proposal
        self.item_labels = []
        if p.rows:
            self.add_item_list(len(p.rows), self._item_row)

    def _item_row(self, box: QVBoxLayout, i: int) -> None:
        p = self.proposal
        row = QHBoxLayout()
        row.setSpacing(4)
        line = ClickLabel(_item_line(p, i))
        tip = _item_tip(p, p.rows[i].start)
        line.setToolTip(tip)
        line.clicked.connect(lambda k=f"view:{i}": self._press(k))
        self.item_labels.append(line)
        row.addWidget(line, 1)
        if self.editable and p.kind == "mark":
            row.addWidget(self.add_extra(f"dec:at:{i}", _step_button(S.BTN_MINUS, S.TIP_MINUS_TIME)))
            row.addWidget(self.add_extra(f"inc:at:{i}", _step_button(S.BTN_PLUS, S.TIP_PLUS_TIME)))
        view = _step_button(S.BTN_VIEW_SHORT, tip)
        row.addWidget(self.add_extra(f"view:{i}", view, blocked_while_busy=False))
        box.addLayout(row)
        # 여러 곳을 한 번에 넣는 대화 카드만 줄마다 출처를 적는다 (한 곳이면 위 표가 이미 말한다. 영수증은 뺀다)
        if p.from_chat and p.kind == "mark" and len(p.rows) > 1 and self.state == "proposal":
            src = _item_source(p, i)
            if src:
                box.addWidget(_label(src, "secondary"))

    def _notes(self) -> None:
        p = self.proposal
        for line in note_lines(getattr(p, "notes", None) or [], p.fps):
            self.add_line(line, "secondary")
        kept = (p.debug or {}).get("count_kept") if hasattr(p, "debug") else None
        if kept:
            self.add_line(_fill(S.CARD_NOTES["count_kept"], kept), "secondary")

    def _guard(self) -> None:
        for text, role in guard_lines(self.guard, skip_whole=_whole_scope(self.proposal)):
            self.add_line(text, role)
        if self.guard is not None and any(g.code == "leftover" for g in self.guard.lines):
            btn = QToolButton()
            btn.setText(S.CHIP_LEFTOVER)
            btn.setProperty("role", "chip")
            self.body_box.addWidget(self.add_extra("leftover", btn, blocked_while_busy=False), 0, Qt.AlignLeft)

    def _save_row(self) -> None:
        """이대로 자동화 버튼에 저장 [자동화 n] [저장] (대화에서 온 쉬는 곳·튀는 소리 카드). 단추 줄 아래(foot)."""
        p = self.proposal
        if not self.save_slots or p.kind not in MAIN_PARAM:
            return
        # 글은 한 줄 위에, 고르기 칸과 [저장]은 그 아래 한 줄 (좁은 창에서도 [저장]이 보이게)
        self.foot_box.addWidget(_label(S.SAVE_ROW, "secondary"))
        row = QHBoxLayout()
        row.setSpacing(4)
        self.foot_box.addLayout(row)
        self.foot.setVisible(True)
        self.save_combo = QComboBox()
        self.save_combo.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.save_combo.setMinimumContentsLength(6)
        self._fill_save_combo(self.save_slots)
        self.save_combo.setAccessibleName(S.SAVE_CHOOSE)
        self.save_combo.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        row.addWidget(self.save_combo, 1)
        save = QPushButton(S.BTN_SAVE)
        save.setProperty("role", "small")
        save.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)
        save.clicked.connect(self._press_save)
        self.extra["save"] = save
        self._apply_enabled()
        row.addWidget(save)

    def _fill_save_combo(self, slots: Sequence[Tuple[int, str]]) -> None:
        keep = self.save_combo.currentData()
        self.save_combo.blockSignals(True)
        self.save_combo.clear()
        for number, label in slots:
            self.save_combo.addItem(label, number)
            self.save_combo.setItemData(self.save_combo.count() - 1, label, Qt.ToolTipRole)
        i = self.save_combo.findData(keep) if keep is not None else -1
        self.save_combo.setCurrentIndex(max(0, i))
        self.save_combo.blockSignals(False)

    def refresh_save_slots(self, slots: Optional[Sequence[Tuple[int, str]]]) -> None:
        """⚙에서 버튼 이름·순서가 바뀜: 저장 줄의 고르기 칸을 새 이름으로 (고른 버튼은 그대로)."""
        if slots is None:
            return
        self.save_slots = list(slots)
        if self.save_combo is not None:
            try:
                self._fill_save_combo(self.save_slots)
            except RuntimeError:
                self.save_combo = None  # 지운 줄

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
        row.addWidget(_label(voice_row_text(v, getattr(self.proposal, "tracks", None) or ())), 1)
        self.voice_btn = QPushButton(S.BTN_CHANGE_VOICE)
        self.voice_btn.setToolTip(S.TIP_CHANGE_VOICE)
        self.voice_btn.setProperty("role", "small")
        self.voice_btn.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)
        self.voice_btn.clicked.connect(lambda: self._press("voice"))
        row.addWidget(self.voice_btn)
        self.body_box.addLayout(row)
        if self.doubled_note:
            self.add_line(self.doubled_note, "warning")  # 창이 이 세션에 처음 한 번만 붙인다

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
            lines.append((S.RECEIPT_OK.format(at=fmt.clock_time(outcome.at), color_word=word, n=outcome.placed), None))
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

    def show_unknown(self, outcome) -> None:
        """답이 끊겨 들어갔는지 모름 (일지는 "넣는 중"): [다시 확인]. 연결 확인이 꼬리표로 맞춰 보면 영수증이 된다."""
        self.outcome = outcome
        self.state = "checking"
        self.clear_body()
        self.voice_btn = self.save_combo = None
        self.more_btn = self.more_list = None
        _unknown_body(self, S.RECEIPT_UNKNOWN, outcome)

    def show_undone(self, undo, at: str) -> None:
        self.state = "undone"
        self.clear_body()
        self.title.setText(S.RECEIPT_UNDONE.format(at=fmt.clock_time(at), color_word=colors_word(self.proposal),
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


def _unknown_body(card: Card, title: str, outcome) -> None:
    card.title.setText(title)
    card.add_line(S.RECEIPT_UNKNOWN_DETAIL, "warning")
    if getattr(outcome, "error", None):
        card.add_line(S.RECEIPT_ERROR.format(reason=outcome.error), "secondary")
    card.set_buttons([("recheck", S.BTN_RECHECK, False)], blocked_while_busy=("recheck",))


# ── 도우미 표시 지우기 카드 ────────────────────────────────────────────

def clear_colors(plan) -> str:
    """지울 표시의 색 낱말 ("파란", "빨간·파란"). 모르면 "도우미"."""
    words = [fmt.color_word(c) for c in (plan.colors or []) if c]
    return S.COLOR_JOIN.join(w for w in words if w) or S.CLEAR_COLOR_FALLBACK


def clear_keep(plan) -> str:
    """지우기 카드의 "안 바뀌는 것": 직접 찍은 표시, 그리고 이번에 지우지 않는 나머지 도우미 표시가 몇 개인지."""
    others = max(0, int(plan.total_ours or 0) - plan.count - int(plan.straddling or 0))
    return S.CLEAR_KEEP_OTHERS.format(n=others) if others else S.CLEAR_KEEP


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
        when = S.WHEN_RANGE.format(a=a, b=b)
    else:
        when = S.WHEN_WHOLE.format(length=fmt.length((plan.tl_end - plan.tl_start) / fps))
    # 몇 곳은 "파란 표시 9개"(넣을 때의 말과 같게)가 말한다. 안 바뀌는 것은 카드가 줄 아래에 붙인다
    return [(S.ROW_WHAT, what), (S.ROW_WHEN, when),
            (S.ROW_CLEAR, S.CLEAR_RESOLVE.format(colors=clear_colors(plan), n=plan.count))]


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
        self.show_plan()

    @property
    def blocked(self) -> bool:
        return bool(self.guard is not None and self.guard.blocked)

    def _line(self, i: int) -> str:
        p = self.proposal
        t = p.targets[i]
        at = fmt.clock((t["frame"] - p.tl_start) / (p.fps or 1.0))
        return S.ITEM_LINE.format(at=at, color_word=fmt.color_word(t.get("color")), name=t.get("name") or "").rstrip()

    def show_plan(self) -> None:
        p = self.proposal
        self.clear_body()
        self.state = "proposal"
        if p.count == 0:
            self.title.setText(S.CARD_TITLE_CLEAR_NONE)
            self.add_line(S.CLEAR_NONE.format(n=p.total_ours))
            if p.straddling:
                self.add_line(S.CLEAR_STRADDLE.format(n=p.straddling), "secondary")
            for text, role in guard_lines(self.guard, skip_whole=(p.params or {}).get("lo") is None):
                self.add_line(text, role)
            self.set_buttons([("cancel", S.BTN_CLOSE, False)])
            return
        self.title.setText(S.CARD_TITLE_CLEAR)
        grid = self.add_rows(clear_rows(p, self.compact))
        _keep_line(self, clear_keep(p), self.compact, grid)
        self._targets()
        if p.straddling:
            self.add_line(S.CLEAR_STRADDLE.format(n=p.straddling), "secondary")
        for line in note_lines(p.notes, p.fps):
            self.add_line(line, "secondary")
        for text, role in guard_lines(self.guard, skip_whole=(p.params or {}).get("lo") is None):
            self.add_line(text, role)
        if self.guard is not None and any(g.code == "leftover" for g in self.guard.lines):
            btn = QToolButton()
            btn.setText(S.CHIP_LEFTOVER)
            btn.setProperty("role", "chip")
            self.body_box.addWidget(self.add_extra("leftover", btn, blocked_while_busy=False), 0, Qt.AlignLeft)
        self.set_buttons([("apply", S.BTN_CLEAR_APPLY, True), ("cancel", S.BTN_CANCEL, False)],
                         blocked_while_busy=("apply",))

    def _targets(self) -> None:
        self.add_item_list(self.proposal.count, self._target_row)

    def _target_row(self, box: QVBoxLayout, i: int) -> None:
        row = QHBoxLayout()
        row.setSpacing(4)
        line = ClickLabel(self._line(i))
        tip = _item_tip(self.proposal, self.proposal.targets[i]["frame"])
        line.setToolTip(tip)
        line.clicked.connect(lambda k=f"view:{i}": self._press(k))
        row.addWidget(line, 1)
        row.addWidget(self.add_extra(f"view:{i}", _step_button(S.BTN_VIEW_SHORT, tip), blocked_while_busy=False))
        box.addLayout(row)

    def _apply_enabled(self) -> None:
        super()._apply_enabled()
        if "apply" in self.buttons and self.blocked:
            self.buttons["apply"].setEnabled(False)

    def show_receipt(self, outcome) -> None:
        self.outcome = outcome
        self.state = "receipt"
        self.clear_body()
        if outcome.status == "applied":
            self.title.setText(S.CLEAR_RECEIPT.format(at=fmt.clock_time(outcome.at), colors=clear_colors(self.proposal),
                                                      n=outcome.deleted))
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

    def show_unknown(self, outcome) -> None:
        """답이 끊겨 지워졌는지 모름 (일지는 "지우는 중"): [다시 확인]."""
        self.outcome = outcome
        self.state = "checking"
        self.clear_body()
        self.more_btn = self.more_list = None
        _unknown_body(self, S.CLEAR_RECEIPT_UNKNOWN, outcome)

    def show_undone(self, undo, at: str) -> None:
        self.state = "undone"
        self.clear_body()
        self.title.setText(S.CLEAR_UNDONE.format(at=fmt.clock_time(at), n=undo.deleted))
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
