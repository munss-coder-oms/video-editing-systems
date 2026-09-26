"""미리보기 촬영: 진짜 도우미 창을 사용자처럼 누르고, 끝나기를 기다리고, 찍는다.

- 누르기는 QTest로 실제 단추를 누른다. 보이고 켜져 있어야 누른다 (아니면 그 단계 이름으로 멈춘다).
- 묻는 창(QMessageBox)과 메뉴는 떠 있는 동안 exec()가 이벤트를 돌리므로, 누르기 전에 타이머를 걸어 두고
  떠 있는 것을 찍은 뒤 사용자가 고를 단추(메뉴 줄)를 누른다.
- 기다리기는 processEvents 고리 + 시간 제한. 시간이 지나면 SceneError(단계, 무엇을 기다렸는지).
- 그림은 창을 grab()한 것 그대로. 메뉴와 묻는 창은 창 그림 위 실제 자리에 겹쳐 그린다 (묻는 창의 제목 줄은
  윈도우가 그리는 테두리라 offscreen에는 없어서, 제목만 담은 줄을 그려 넣는다).
"""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from PySide6.QtCore import QPoint, QRect, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QImage, QInputMethodEvent, QPainter, QPixmap
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QAbstractButton, QApplication, QLabel, QMenu, QMessageBox, QWidget

from app.companion import theme
from app.companion.window import HelperWindow

from . import timeline_view

TITLE_BAR_H = 32  # 묻는 창 위에 그려 넣는 제목 줄 (논리 픽셀)
SHOW_DELAY_S = 0.15  # 메뉴·묻는 창이 뜬 뒤 찍기 전에 기다리는 시간 (그리기가 끝나게)
FAINT_RATIO = 3.0  # 묻는 창의 글씨와 바탕 대비가 이보다 낮으면 찾은 문제로 적는다 (읽기 어려움)

# 찍으면서 재어 본 창의 문제 (그 단계의 "찾은 문제"로 적는다). 같은 문제가 또 보이면 짧은 알림으로.
ISSUE_HIDDEN_BUTTON = "{what}: 창에서는 대화 칸 오른쪽 밖이라 보이지 않아요 (미리보기는 그대로 눌렀어요)."
NOTE_CLIPPED = ("카드 오른쪽이 잘린 것은 '{title}' 카드가 대화 칸을 넓혀서예요 (최소 {need}픽셀, 대화 칸 {view}픽셀). "
                "카드만 따로 찍은 그림도 함께 두었어요.")
ISSUE_FAINT = ("묻는 창의 글씨가 바탕과 거의 같은 색이라 잘 안 읽혀요 (글씨 {fg}, 바탕 {bg}, 대비 {ratio:.1f}:1). "
               "도우미 창의 밝은 글씨 색이 묻는 창에도 들어가는데 바탕은 기본 밝은 색이라서예요. 윈도우를 밝은 "
               "모드로 쓰면 실제 창도 이렇게 보일 수 있어요. 창에 적힌 글은 '보이는 것'에 옮겨 적었어요.")
ISSUE_FAINT_AGAIN = "이 묻는 창도 글씨가 흐려요 ({first}단계에 적은 문제와 같아요, 대비 {ratio:.1f}:1)."


class SceneError(RuntimeError):
    """한 단계를 끝내지 못함: 어느 단계에서 무엇이 안 됐는지."""

    def __init__(self, step: str, what: str) -> None:
        super().__init__(f"[{step}] {what}")
        self.step = step
        self.what = what


@dataclass
class Image:
    src: str  # index.html 기준 경로 (img/…png)
    kind: str  # window / dialog / menu / card / timeline
    w: int  # 쪽에 놓을 크기 (CSS 픽셀 = 논리 픽셀)
    h: int
    alt: str
    caption: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class Director:
    """창 하나와 가짜 리졸브 한 벌을 들고 단계마다 누르고 찍는다."""

    def __init__(self, app: QApplication, window: HelperWindow, world, out_dir: Path) -> None:
        self.app = app
        self.w = window
        self.world = world
        self.out_dir = Path(out_dir)
        self.img_dir = self.out_dir / "img"
        self.img_dir.mkdir(parents=True, exist_ok=True)
        self.begin(0, "준비")
        self._faint_first: Optional[int] = None  # 흐린 묻는 창을 처음 적은 단계
        self._timers: List[QTimer] = []
        self._armed: List[Dict[str, Any]] = []
        self.unexpected: List[str] = []
        # 창의 묻는 창: 진짜 QMessageBox를 띄운다 (창은 interactive=False라 그대로 두면 묻지 않고 "취소"로 본다).
        # 결과 폴더를 여는 일(interactive일 때만)은 막아 두려고 묻는 동안만 interactive를 켠다.
        window.confirm = self._confirm
        window.choose = self._choose

    # ── 단계 ─────────────────────────────────────────────────────────

    def begin(self, n: int, title: str) -> None:
        self.n = n
        self.step = f"{n}. {title}"
        self.prefix = f"s{n:02d}"
        self.notes: List[str] = []  # 이 단계 설명에 덧붙일 것
        self.issues: List[str] = []  # 찍으면서 재어 본 창의 문제 (보이지 않는 단추, 잘린 카드, 흐린 글씨)
        self._names: Dict[str, int] = {}
        self.last_dialog: Dict[str, Any] = {}

    def issue(self, text: str) -> None:
        if text not in self.issues:
            self.issues.append(text)

    def fail(self, what: str) -> SceneError:
        return SceneError(self.step, what)

    # ── 기다리기 ───────────────────────────────────────────────────────

    def settle(self, seconds: float = 0.12) -> None:
        deadline = time.monotonic() + seconds
        while True:
            self.app.processEvents()
            if time.monotonic() >= deadline:
                return
            time.sleep(0.005)

    def wait(self, cond: Callable[[], Any], what: str, timeout: float = 15.0) -> Any:
        deadline = time.monotonic() + timeout
        while True:
            self.app.processEvents()
            value = cond()
            if value:
                return value
            if time.monotonic() >= deadline:
                now = self.w.message.text().replace("\n", " ")
                raise self.fail(f"{timeout:g}초 안에 되지 않음: {what} (창 맨 위 알림: '{now}')")
            time.sleep(0.005)

    def idle(self, timeout: float = 60.0) -> None:
        """창이 하던 일(리졸브 요청, 계산, 대화의 남은 일, 결과 저장, 소리 꺼내기)이 모두 끝날 때까지."""
        w = self.w
        self.wait(lambda: not w.busy and w.pending == 0 and not w.chat_flow.active and not w.report_pending
                  and w.shorts.running == 0, "창이 하던 일이 끝나기", timeout)
        self.settle()

    # ── 누르기 ───────────────────────────────────────────────────────

    def show_in_chat(self, widget: QWidget) -> None:
        """대화 칸 안의 것이면 위아래로만 굴려 보이게 한다 (사용자가 휠로 굴리는 것과 같다).

        대화 칸은 옆으로 굴릴 수 없으니 옆으로는 움직이지 않는다 (ensureWidgetVisible은 옆으로도 굴린다).
        """
        log = self.w.chat.log
        if not log.inner.isAncestorOf(widget):
            return
        self.settle(0.02)
        top = widget.mapTo(log.inner, QPoint(0, 0)).y()
        bottom = top + widget.height()
        bar = log.verticalScrollBar()
        view = log.viewport().height()
        margin = 12
        if top < bar.value() + margin or bottom > bar.value() + view - margin:
            # 다 들어가면 아래를 맞춰 앞의 말도 보이게, 안 들어가면 위를 맞춘다
            value = bottom - view + margin if widget.height() + 2 * margin <= view else top - margin
            bar.setValue(max(0, min(bar.maximum(), value)))
        log.horizontalScrollBar().setValue(0)
        self.settle(0.05)

    def chat_bottom(self) -> None:
        """대화 칸을 맨 아래로 굴린다 (방금 나온 말이 보이게)."""
        self.settle(0.05)
        log = self.w.chat.log
        log.verticalScrollBar().setValue(log.verticalScrollBar().maximum())
        log.horizontalScrollBar().setValue(0)
        self.settle(0.05)

    def hidden_part(self, widget: QWidget) -> float:
        """대화 칸 안의 것이 옆으로 잘려 안 보이는 비율 (0: 다 보임, 1: 하나도 안 보임)."""
        log = self.w.chat.log
        if not log.inner.isAncestorOf(widget) or widget.width() <= 0:
            return 0.0
        view = log.viewport()
        left = widget.mapTo(view, QPoint(0, 0)).x()
        shown = max(0, min(left + widget.width(), view.width()) - max(left, 0))
        return 1.0 - shown / widget.width()

    def chat_overflow(self) -> Optional[Tuple[str, int, int]]:
        """대화 칸 내용이 칸보다 넓으면 (가장 넓은 카드의 제목, 그 카드의 최소 너비, 대화 칸 너비). 아니면 None."""
        log = self.w.chat.log
        view = log.viewport().width()
        if log.inner.width() <= view:
            return None
        cards = [c for c in self.w.runs.cards if log.inner.isAncestorOf(c)]
        if not cards:
            return None
        widest = max(cards, key=lambda c: c.minimumSizeHint().width())
        title = getattr(widest, "title", None)
        return (title.text() if isinstance(title, QLabel) else ""), widest.minimumSizeHint().width(), view

    def check_clipped(self, widget: QWidget) -> None:
        """대화 칸 안의 것이 옆으로 잘려 보이면 그 까닭(가장 넓은 카드)을 알아 둘 것에 적는다.

        문제 자체는 그 넓은 카드의 단계(scenes의 Ctrl+Z 질문 카드)에 잰 값으로 한 번 적는다.
        """
        if self.hidden_part(widget) <= 0.01:
            return
        over = self.chat_overflow()
        if over is not None:
            title, need, view = over
            text = NOTE_CLIPPED.format(title=title, need=need, view=view)
            if text not in self.notes:
                self.notes.append(text)

    def press(self, widget: Optional[QWidget], what: str) -> None:
        if widget is None:
            raise self.fail(f"누를 단추가 없음: {what}")
        self.show_in_chat(widget)
        if not widget.isVisible():
            raise self.fail(f"단추가 보이지 않음: {what}")
        if not widget.isEnabled():
            raise self.fail(f"단추가 꺼져 있음: {what}")
        hidden = self.hidden_part(widget)
        if hidden > 0.5:
            # 창에서는 대화 칸 오른쪽 밖이라 보이지 않는 단추: 그대로 누르되 그 단계 설명에 적는다
            self.issue(ISSUE_HIDDEN_BUTTON.format(what=what))
        QTest.mouseClick(widget, Qt.LeftButton, Qt.NoModifier, widget.rect().center())
        self.settle(0.05)

    def keys(self, widget: QWidget, key: Qt.Key, times: int = 1) -> None:
        widget.setFocus(Qt.MouseFocusReason)
        for _ in range(times):
            QTest.keyClick(widget, key)
        self.settle(0.05)

    def type_send(self, text: str) -> None:
        """대화 입력 칸에 적고 Enter. 한글은 입력기처럼 한 글자씩 조합을 끝내 넣고(commit), 나머지는 키로 친다."""
        box = self.w.chat.input
        self.press(box, "대화 입력 칸")
        for ch in text:
            if ch.isascii():
                QTest.keyClick(box, ch)
            else:
                event = QInputMethodEvent("", [])
                event.setCommitString(ch)
                QApplication.sendEvent(box, event)
        self.settle(0.05)
        if box.toPlainText() != text:
            raise self.fail(f"입력 칸에 '{text}'이 들어가지 않음 ({box.toPlainText()!r})")
        QTest.keyClick(box, Qt.Key_Return)
        self.settle(0.05)
        if box.toPlainText():
            raise self.fail(f"Enter로 보내지지 않음: {text}")

    # ── 그림 ─────────────────────────────────────────────────────────

    def _path(self, name: str) -> Path:
        """단계 번호가 붙은 그림 이름. 한 단계에서 같은 이름을 또 쓰면 -2, -3을 붙인다."""
        n = self._names.get(name, 0) + 1
        self._names[name] = n
        return self.img_dir / (f"{self.prefix}-{name}.png" if n == 1 else f"{self.prefix}-{name}-{n}.png")

    def _save(self, pixmap: QPixmap, name: str, kind: str, alt: str, caption: str = "") -> Image:
        path = self._path(name)
        if not pixmap.save(str(path), "PNG"):
            raise self.fail(f"그림을 저장하지 못함: {path}")
        size = pixmap.deviceIndependentSize()
        return Image(f"img/{path.name}", kind, int(round(size.width())), int(round(size.height())), alt, caption)

    def shot(self, alt: str, name: str = "window", caption: str = "") -> Image:
        """창 전체."""
        self.settle(0.1)
        return self._save(self.w.grab(), name, "window", alt, caption)

    def widget_shot(self, widget: QWidget, alt: str, name: str = "card", caption: str = "") -> Image:
        """카드 한 장만 (대화 칸에 다 들어가지 않을 때)."""
        self.settle(0.05)
        return self._save(widget.grab(), name, "card", alt, caption)

    def card_cut(self, card: QWidget) -> bool:
        """카드가 대화 칸 안에서 다 보이지 않는지 (잘렸으면 카드만 따로 찍는다)."""
        view = self.w.chat.log.viewport()
        top = card.mapTo(view, QPoint(0, 0))
        return not view.rect().contains(QRect(top, card.size()))

    def strip(self, alt: str, name: str = "timeline", caption: str = "") -> Image:
        """리졸브 타임라인 흉내 그림: 가짜 리졸브의 표시 목록 그대로."""
        path = self._path(name)
        w, h = timeline_view.render(self.world.resolve.markers, self.world.info, path)
        return Image(f"img/{path.name}", "timeline", w, h, alt, caption)

    def _window_pos(self) -> QPoint:
        return self.w.mapToGlobal(QPoint(0, 0))

    def _compose(self, top: QPixmap, offset: QPoint, *, title: Optional[str] = None) -> QPixmap:
        """창 그림 위 offset(창 기준, 논리 픽셀) 자리에 top을 겹친다. 넘치면 그림을 넓힌다."""
        base = self.w.grab()
        dpr = base.devicePixelRatio()
        bw, bh = base.deviceIndependentSize().width(), base.deviceIndependentSize().height()
        tw, th = top.deviceIndependentSize().width(), top.deviceIndependentSize().height()
        bar = TITLE_BAR_H if title else 0
        rect = QRectF(offset.x(), offset.y() - bar, tw, th + bar)
        canvas = QRectF(0, 0, bw, bh).united(rect)
        img = QImage(int(round(canvas.width() * dpr)), int(round(canvas.height() * dpr)),
                     QImage.Format_ARGB32_Premultiplied)
        img.setDevicePixelRatio(dpr)
        img.fill(Qt.transparent)
        p = QPainter(img)
        p.setRenderHint(QPainter.Antialiasing)
        p.translate(-canvas.x(), -canvas.y())
        p.drawPixmap(QPoint(0, 0), base)
        # 그림자 (떠 있는 것을 창과 떼어 보이게)
        for i, alpha in enumerate((60, 36, 18)):
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(0, 0, 0, alpha))
            p.drawRoundedRect(rect.adjusted(-i - 1, -i, i + 1, i + 2), 6 + i, 6 + i)
        if title:
            p.fillRect(QRectF(rect.x(), rect.y(), rect.width(), bar), QColor("#2b2d31"))
            font = QFont()
            font.setFamilies(list(theme.FONT_FAMILIES))
            font.setPixelSize(12)
            p.setFont(font)
            p.setPen(QColor(theme.TOKENS.text))
            p.drawText(QRectF(rect.x() + 12, rect.y(), rect.width() - 48, bar), Qt.AlignLeft | Qt.AlignVCenter,
                       title)
            p.setPen(QColor(theme.TOKENS.secondary))
            p.drawText(QRectF(rect.right() - 36, rect.y(), 28, bar), Qt.AlignCenter, "✕")
        p.drawPixmap(QPoint(int(rect.x()), int(rect.y() + bar)), top)
        p.end()
        return QPixmap.fromImage(img)

    # ── 메뉴와 묻는 창 ─────────────────────────────────────────────────

    def _arm(self, find: Callable[[], Optional[QWidget]], act: Callable[[QWidget], Any], what: str,
             timeout: float) -> Dict[str, Any]:
        """누르기 전에 걸어 두는 감시: 뜨면 조금 기다렸다가 act(찍고 누르기)."""
        state: Dict[str, Any] = {"done": False, "error": None, "result": None, "seen": None, "what": what}
        deadline = time.monotonic() + timeout
        timer = QTimer()
        timer.setInterval(20)

        def tick() -> None:
            if state["done"]:
                return
            target = find()
            now = time.monotonic()
            if target is None:
                if now > deadline:
                    state.update(done=True, error=f"{timeout:g}초 안에 뜨지 않음: {what}")
                    timer.stop()
                return
            if state["seen"] is None:
                state["seen"] = now
                return
            if now - state["seen"] < SHOW_DELAY_S:
                return
            state["done"] = True
            timer.stop()
            try:
                state["result"] = act(target)
            except Exception as exc:  # noqa: BLE001 - 멈추지 않게 닫고 알린다
                state["error"] = f"{what}: {exc}"
                target.close()

        timer.timeout.connect(tick)
        timer.start()
        self._timers.append(timer)
        self._armed.append(state)
        return state

    def _finish(self, state: Dict[str, Any], timeout: float) -> Any:
        self.wait(lambda: state["done"], state["what"], timeout + 5)
        self._armed.remove(state)
        if state["error"]:
            raise self.fail(state["error"])
        return state["result"]

    def menu(self, button: QAbstractButton, menu: QMenu, click: Optional[str], alt: str, name: str = "menu",
             timeout: float = 10.0) -> Image:
        """단추를 눌러 메뉴를 열고, 창 위에 겹쳐 찍은 뒤, click 줄을 누른다 (None이면 닫기)."""

        def act(m: QWidget) -> Image:
            offset = m.mapToGlobal(QPoint(0, 0)) - self._window_pos()
            image = self._save(self._compose(m.grab(), offset), name, "menu", alt)
            if click is None:
                m.close()
                return image
            action = next((a for a in menu.actions() if a.text() == click), None)
            if action is None:
                raise RuntimeError(f"메뉴에 '{click}' 줄이 없음 ({[a.text() for a in menu.actions()]})")
            if not action.isEnabled():
                raise RuntimeError(f"메뉴 줄이 꺼져 있음: {click}")
            QTest.mouseClick(menu, Qt.LeftButton, Qt.NoModifier, menu.actionGeometry(action).center())
            return image

        state = self._arm(lambda: menu if menu.isVisible() else None, act, f"메뉴 ({alt})", timeout)
        self.press(button, alt)
        return self._finish(state, timeout)

    def dialog(self, trigger: Callable[[], None], answer: str, alt: str, name: str = "dialog",
               timeout: float = 20.0) -> Image:
        """trigger()가 띄우는 묻는 창을 창 위 가운데에 겹쳐 찍고 answer 단추를 누른다."""

        def find() -> Optional[QWidget]:
            return next((b for b in QApplication.topLevelWidgets() if isinstance(b, QMessageBox) and b.isVisible()),
                        None)

        def act(box: QWidget) -> Image:
            assert isinstance(box, QMessageBox)
            size = box.size()
            x = (self.w.width() - size.width()) // 2
            y = max(TITLE_BAR_H + 8, (self.w.height() - size.height()) // 2)
            image = self._save(self._compose(box.grab(), QPoint(x, y), title=box.windowTitle()), name, "dialog",
                               alt)
            shown = sorted((b for b in box.buttons() if b.isVisible()), key=lambda b: b.mapTo(box, QPoint(0, 0)).x())
            self.last_dialog = {"title": box.windowTitle(), "text": box.text(), "buttons": [b.text() for b in shown]}
            self._check_faint(box)
            button = next((b for b in box.buttons() if b.text() == answer), None)
            if button is None:
                raise RuntimeError(f"묻는 창에 [{answer}]이 없음 ({[b.text() for b in box.buttons()]})")
            QTest.mouseClick(button, Qt.LeftButton, Qt.NoModifier, button.rect().center())
            return image

        state = self._arm(find, act, f"묻는 창 ({alt})", timeout)
        trigger()
        return self._finish(state, timeout)

    def _check_faint(self, box: QMessageBox) -> None:
        """묻는 창의 글씨와 바탕의 대비 (그려진 색 그대로: 글씨는 스타일시트, 바탕은 창 팔레트)."""
        label = box.findChild(QLabel, "qt_msgbox_label")
        if label is None:
            return
        fg = label.palette().color(label.foregroundRole())
        bg = box.palette().color(box.backgroundRole())
        ratio = contrast(fg, bg)
        if ratio >= FAINT_RATIO:
            return
        if self._faint_first is None or self._faint_first == self.n:
            self._faint_first = self.n
            self.issue(ISSUE_FAINT.format(fg=fg.name(), bg=bg.name(), ratio=ratio))
        else:
            self.issue(ISSUE_FAINT_AGAIN.format(first=self._faint_first, ratio=ratio))

    def _real(self, fn, *args):
        """창의 진짜 묻는 창 (HelperWindow._ask/_choose). 걸어 둔 감시가 없으면 띄우지 않는다 (멈추지 않게)."""
        if not self._armed:
            self.unexpected.append(f"[{self.step}] {args[0]}")
            return None
        self.w.interactive = True
        try:
            return fn(self.w, *args)
        finally:
            self.w.interactive = False

    def _confirm(self, title: str, text: str, ok_label: str) -> bool:
        return bool(self._real(HelperWindow._ask, title, text, ok_label))

    def _choose(self, title: str, text: str, options: List[str]) -> Optional[int]:
        return self._real(HelperWindow._choose, title, text, options)

    # ── 창 크기 ──────────────────────────────────────────────────────

    def resize(self, width: int, height: int) -> None:
        self.w._placed = True  # 처음 띄울 때 오른쪽 끝에 붙이는 일은 이미 했다
        self.w.resize(width, height)
        self.settle(0.15)
        if (self.w.width(), self.w.height()) != (width, height):
            raise self.fail(f"창 크기가 {width}×{height}이 되지 않음 ({self.w.width()}×{self.w.height()})")


def contrast(a: QColor, b: QColor) -> float:
    """두 색의 대비 (WCAG 상대 밝기 비, 1~21)."""

    def lum(c: QColor) -> float:
        def ch(v: float) -> float:
            return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4

        return 0.2126 * ch(c.redF()) + 0.7152 * ch(c.greenF()) + 0.0722 * ch(c.blueF())

    hi, lo = sorted((lum(a), lum(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)
