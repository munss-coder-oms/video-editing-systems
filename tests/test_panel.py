"""새 도우미 창의 모양과 동작 (설계 B1, B11 UI). 화면 없이 offscreen, 리졸브 대신 tests/fakes.py.

- 420×900과 380×640(좁은 화면)에서 머리말·버튼·입력 칸이 잘리지 않는다.
- 화면에 보이는 한국어는 모두 strings_ko.py에서 온다.
- 한글 입력 중 Enter는 보내지 않는다.
- 점검용 복사본이 열려 있으면 경고와 [원래 타임라인으로].
- 작업 중에는 버튼이 꺼지고 [결과 저장]은 켜져 있다.
"""

from __future__ import annotations

import ast
import json
import os
import re
import threading
import time
from pathlib import Path

import pytest

pytest.importorskip("PySide6")

from app.companion import strings_ko as S  # noqa: E402
from tests.fakes import FakeLuaBridge, timeline_info  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
UI_MODULES = ("window", "header_view", "automation_view", "chat_view", "undo_view", "check_page", "connection",
              "cards", "voice_picker", "slot_settings", "runs", "fmt", "jobs", "listen", "chat_flow")
HANGUL = re.compile(r"[ㄱ-ㆎ가-힣]")


@pytest.fixture
def qapp():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


@pytest.fixture
def fake(tmp_path) -> FakeLuaBridge:
    return FakeLuaBridge(tmp_path)


@pytest.fixture
def make_window(qapp, tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path / "Roaming"))
    monkeypatch.setenv("PROGRAMDATA", str(tmp_path / "ProgramData"))
    made = []

    def make(bridge, **kw):
        from app.companion.window import HelperWindow

        w = HelperWindow(bridge=bridge, interactive=False, report_dir=tmp_path / "desktop", auto_ping_ms=50,
                         state_root=tmp_path / "state", process_check=None, **kw)
        w.show()
        qapp.processEvents()
        made.append(w)
        return w

    yield make
    for w in made:
        w.close()
    qapp.processEvents()


def wait_until(qapp, cond, timeout: float = 10.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        qapp.processEvents()
        if cond():
            return
        time.sleep(0.005)
    raise AssertionError("정해진 시간 안에 끝나지 않음")


def settle(qapp, seconds: float = 0.05) -> None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        qapp.processEvents()
        time.sleep(0.005)


def connected_window(qapp, make_window, fake):
    w = make_window(fake)
    wait_until(qapp, lambda: w.connected and w.pending == 0)
    return w


def resize(qapp, w, width, height):
    w._placed = True
    w.resize(width, height)
    settle(qapp)


# ---------------------------------------------------------------------------
# 크기
# ---------------------------------------------------------------------------

def test_full_layout_at_420x900(qapp, make_window, fake):
    w = connected_window(qapp, make_window, fake)
    resize(qapp, w, 420, 900)
    assert w.layout_name == "full"
    assert w.smoke_check() == []
    assert all(g.isVisible() for g in w.automation.gears)  # ⚙ 40×40
    assert all(b.height() >= 60 for b in w.automation.buttons)
    assert w.header.summary_row.isVisible()
    assert "Timeline 1" in w.header.summary.text() and "소리 트랙 4개" in w.header.summary.text()
    assert w.chat.height() >= 220


def test_compact_layout_at_380x640(qapp, make_window, fake):
    fake.online = False
    w = make_window(fake)
    resize(qapp, w, 380, 640)
    assert w.layout_name == "tight"
    assert w.smoke_check() == []
    # 연결 전: 머리말은 상태 한 줄 + 스크립트를 누르라는 안내
    assert w.header.hint.isVisible() and not w.header.summary_row.isVisible()
    tops = [b.mapTo(w, b.rect().topLeft()).y() for b in w.automation.buttons]
    assert len(set(tops)) == 1  # 세 칸 타일
    assert all(b.height() >= 68 for b in w.automation.buttons)
    assert w.chat.input.isVisible() and w.chat.input.height() >= 44
    fake.online = True
    wait_until(qapp, lambda: w.connected and w.pending == 0)
    assert not w.header.hint.isVisible()
    assert w.smoke_check() == []


@pytest.mark.parametrize("width", [360, 460])
@pytest.mark.parametrize("height", [640, 760, 900])
def test_any_width_from_360_to_460_fits(qapp, make_window, fake, width, height):
    w = connected_window(qapp, make_window, fake)
    resize(qapp, w, width, height)
    assert w.width() == width
    assert w.smoke_check() == []
    assert w.status.width() > 40  # 상태 낱말이 버튼에 밀려 사라지지 않는다


def test_text_scale_130_goes_one_step_down(qapp, make_window, fake, tmp_path):
    w = connected_window(qapp, make_window, fake)
    resize(qapp, w, 420, 900)
    w.apply_text_scale(130)
    settle(qapp)
    assert w.layout_name == "tiles"
    saved = json.loads((tmp_path / "state" / "settings.json").read_text(encoding="utf-8"))
    assert saved["ui"]["text_scale"] == 130


def test_widths(qapp):
    from app.companion import window as win

    assert win.default_width(1920) == 420 and win.default_width(1536) == 380
    assert win.layout_mode(900) == "full" and win.layout_mode(700) == "tiles" and win.layout_mode(640) == "tight"
    assert win.layout_mode(900, 130) == "tiles" and win.layout_mode(640, 130) == "tight"


# ---------------------------------------------------------------------------
# 글
# ---------------------------------------------------------------------------

def _strings_outside_docstrings(tree: ast.AST):
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = getattr(node, "body", [])
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                docstrings.add(id(body[0].value))
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docstrings:
            yield node


@pytest.mark.parametrize("module", UI_MODULES)
def test_ui_strings_come_from_strings_ko(module):
    path = ROOT / "app" / "companion" / f"{module}.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    bad = [(n.lineno, n.value) for n in _strings_outside_docstrings(tree) if HANGUL.search(n.value)]
    assert bad == [], f"{module}.py: 한국어 글은 strings_ko.py에 두세요: {bad}"


def test_status_words_are_never_colour_only(qapp, make_window, fake):
    fake.online = False
    w = make_window(fake)
    assert w.header.dot.text() == S.DOT_OFF and w.status.text() == S.STATUS_NOT_CONNECTED
    fake.online = True
    wait_until(qapp, lambda: w.connected and w.pending == 0)
    assert w.header.dot.text() == S.DOT_CONNECTED and w.status.text() == S.STATUS_CONNECTED


# ---------------------------------------------------------------------------
# 대화 칸
# ---------------------------------------------------------------------------

def _key(widget, key, modifiers=None):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    QTest.keyClick(widget, key, modifiers if modifiers is not None else Qt.NoModifier)


def test_ime_preedit_enter_does_not_send(qapp, make_window, fake):
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QInputMethodEvent
    from PySide6.QtWidgets import QApplication

    w = connected_window(qapp, make_window, fake)
    sent = []
    w.chat.sent.connect(sent.append)
    box = w.chat.input
    box.setFocus()
    QApplication.sendEvent(box, QInputMethodEvent("하", []))
    assert box.preedit == "하"
    _key(box, Qt.Key_Return)
    assert sent == []  # 조합 중인 글자만 확정하고 보내지 않는다
    commit = QInputMethodEvent("", [])
    commit.setCommitString("한")
    QApplication.sendEvent(box, commit)
    assert box.preedit == "" and box.toPlainText().strip() == "한"
    _key(box, Qt.Key_Return)
    assert sent == ["한"] and box.toPlainText() == ""


def test_enter_shift_enter_and_escape(qapp, make_window, fake):
    from PySide6.QtCore import Qt

    w = connected_window(qapp, make_window, fake)
    sent = []
    w.chat.sent.connect(sent.append)
    box = w.chat.input
    box.setPlainText("첫 줄")
    box.moveCursor(box.textCursor().MoveOperation.End)
    _key(box, Qt.Key_Return, Qt.ShiftModifier)
    assert sent == [] and "\n" in box.toPlainText()
    _key(box, Qt.Key_Escape)
    assert box.toPlainText() == "" and sent == []
    box.setPlainText("3분 20초에 표시해줘")
    _key(box, Qt.Key_Enter)
    assert sent == ["3분 20초에 표시해줘"]


def test_chat_asks_resolve_only_when_it_needs_the_timeline(qapp, make_window, fake):
    """도움말·못 하는 부탁·못 알아들은 말은 리졸브에 묻지 않는다. 시간을 풀 때만 ping + 타임라인 읽기 (바꾸지 않음)."""
    w = connected_window(qapp, make_window, fake)
    before = len(fake.requests)
    for text in ("뭐 할 수 있어?", "자동 자막 만들어줘", "오늘 날씨 어때"):
        w.chat.input.setPlainText(text)
        w.chat.send_btn.click()
        settle(qapp, 0.1)
    assert len(fake.requests) == before
    log = w.chat.log.toPlainText()
    assert S.CHAT_HELP in log and S.CHAT_REPLIES["studio"] in log and S.CHAT_NO_MATCH in log
    assert w.chat.brain.text() == S.CHAT_BRAIN_LINE
    w.chat.input.setPlainText("3분 20초에 표시해줘")
    w.chat.send_btn.click()
    wait_until(qapp, lambda: not w.busy and w.pending == 0 and not w.chat_flow.active)
    assert fake.requests[before:] == ["ping", "timeline_info"]
    assert S.CARD_TITLE_PROPOSE in w.chat.log.toPlainText()


def test_chat_collapses_but_input_stays(qapp, make_window, fake, tmp_path):
    w = connected_window(qapp, make_window, fake)
    w.chat.toggle.click()
    settle(qapp)
    assert not w.chat.log.isVisible() and w.chat.input.isVisible()
    saved = json.loads((tmp_path / "state" / "settings.json").read_text(encoding="utf-8"))
    assert saved["ui"]["chat_collapsed"] is True


# ---------------------------------------------------------------------------
# 자동화 버튼, 메뉴, 아래쪽
# ---------------------------------------------------------------------------

def test_slots_before_and_after_connect(qapp, make_window, fake):
    fake.online = False
    w = make_window(fake)
    names = [b.name for b in w.automation.buttons]
    assert names == ["쉬는 곳 표시", "튀는 소리 표시", "소리 고르게"]
    assert [b.receipt.text() for b in w.automation.buttons[:2]] == [S.SLOT_DISABLED_NOT_CONNECTED] * 2
    assert not any(b.isEnabled() for b in w.automation.buttons)
    assert all(g.isEnabled() for g in w.automation.gears)  # ⚙는 연결 전에도 누를 수 있다
    fake.online = True
    wait_until(qapp, lambda: w.connected and w.pending == 0)
    one, two, three = w.automation.buttons
    assert one.isEnabled() and two.isEnabled()
    assert not three.isEnabled() and three.receipt.text() == S.SLOT_DISABLED_NOT_READY
    assert "1.5초" in one.summary.text() and "파란" in one.summary.text()


def test_slot_press_pings_first_and_changes_nothing(qapp, make_window, fake):
    """누르면 먼저 연결 확인(ping), 그다음 읽기만 한다. 원본 파일이 이 PC에 없으면 그렇다고 말한다."""
    w = connected_window(qapp, make_window, fake)
    before = len(fake.requests)
    w.automation.buttons[0].click()
    wait_until(qapp, lambda: not w.busy and w.pending == 0)
    assert fake.requests[before] == "ping"
    assert set(fake.requests[before:]) <= {"ping", "timeline_info", "timeline_items"}  # 리졸브에서 바뀌는 것은 없다
    assert S.PLAN_REFUSED["files_missing"] in w.chat.log.toPlainText()
    assert w.automation.buttons[0].isEnabled()


def test_more_menu_and_check_page(qapp, make_window, fake):
    w = connected_window(qapp, make_window, fake)
    assert w.menu_entries() == [S.MENU_CHECK_PAGE, S.MENU_REPORT, S.MENU_SETTINGS, S.MENU_HELP]
    w.more_menu.actions()[0].trigger()
    assert w.stack.currentWidget() is w.check_page
    for btn in (w.probe_btn, w.marker_btn, w.audio_btn, w.cleanup_btn):
        assert btn.isVisible() and btn.isEnabled()
    assert not w.check_page.leftover_btn.isVisible()
    w.check_page.back_btn.click()
    assert w.stack.currentWidget() is w.panel
    assert w.footer.entry_texts() == [S.UNDO_NOTHING]


def test_idle_window_sends_nothing(qapp, make_window, fake):
    w = connected_window(qapp, make_window, fake)
    count = len(fake.requests)
    settle(qapp, 0.5)  # 자동 확인 간격(50ms)의 열 배
    assert len(fake.requests) == count


# ---------------------------------------------------------------------------
# 점검용 복사본
# ---------------------------------------------------------------------------

def test_probe_copy_warning_and_switch_back(qapp, make_window, fake):
    original = fake.info
    fake.info = timeline_info(timeline="AI 도우미 점검용 101500", timeline_uid="tl-2", is_probe_copy=True,
                              probe={"original_uid": "tl-1", "original_name": "Timeline 1",
                                     "copy_uid": "tl-2", "copy_name": "AI 도우미 점검용 101500"})

    def switch(args):
        fake.info = original
        return {"switched": True, "readback_uid": "tl-1", "readback_name": "Timeline 1", "recorded": True}

    fake.handlers["switch_timeline"] = switch
    w = connected_window(qapp, make_window, fake)
    assert w.header.warning_row.isVisible() and w.header.warning.text() == S.PROBE_COPY_WARNING
    assert w.header.back_btn.isVisible() and w.header.back_btn.isEnabled()
    assert not any(b.isEnabled() for b in w.automation.buttons)
    assert w.automation.buttons[0].receipt.text() == S.SLOT_DISABLED_PROBE_COPY
    w.header.back_btn.click()
    wait_until(qapp, lambda: w.action is None and w.pending == 0)
    i = fake.requests.index("switch_timeline")
    assert fake.args[i] == {"uid": "tl-1", "name": "Timeline 1"}
    assert not w.header.warning_row.isVisible()
    assert S.SWITCHED_BACK.format(name="Timeline 1") in w.message.text()
    assert w.automation.buttons[0].isEnabled()


def test_probe_copy_without_record_asks_to_switch_by_hand(qapp, make_window, fake):
    fake.info = timeline_info(timeline="AI 도우미 점검용 101500", timeline_uid="tl-2")
    w = connected_window(qapp, make_window, fake)
    assert w.header.warning_row.isVisible() and not w.header.back_btn.isVisible()
    assert S.PROBE_COPY_NO_RECORD in w.header.warning.text()


# ---------------------------------------------------------------------------
# 작업 중
# ---------------------------------------------------------------------------

def test_buttons_disabled_during_job_but_report_stays_enabled(qapp, make_window, fake):
    gate, blocking = threading.Event(), threading.Event()

    def probe_read(args):
        if blocking.is_set():  # 연결할 때 하는 읽기 점검은 막지 않는다
            gate.wait(10)
        return {"exists": {}, "existence_reliable": True, "calls": {}}

    def probe_copy(args):
        if args.get("stage") == "C8":
            return {"ok": True, "stage": "C8", "detail": {"copy_found": False}, "calls": {}}
        return {"ok": False, "stage": args.get("stage"), "detail": {"reason": "name_taken"}, "calls": {}}

    fake.handlers.update(probe_read=probe_read, probe_copy=probe_copy)
    w = connected_window(qapp, make_window, fake)
    asked = []
    w.confirm = lambda *a: asked.append(a) or True
    w.show_check_page()
    before = len(fake.requests)
    blocking.set()
    w.probe_btn.click()
    assert asked and asked[0][1] == S.PROBE_CONFIRM
    wait_until(qapp, lambda: "probe_read" in fake.requests[before:])
    assert w.action == "probe"
    for btn in (w.probe_btn, w.marker_btn, w.audio_btn, w.cleanup_btn, w.footer.undo_btn):
        assert not btn.isEnabled()
    assert not any(b.isEnabled() for b in w.automation.buttons)
    assert w.automation.buttons[0].receipt.text() == S.SLOT_DISABLED_BUSY
    assert w.report_btn.isEnabled() and w.connect_btn.isEnabled()
    first = w.last_report  # 점검 전에 먼저 저장한 결과 파일
    assert first is not None and first.is_file()
    w.last_report = None
    w.report_btn.click()
    wait_until(qapp, lambda: w.last_report is not None)  # 리졸브 줄을 기다리지 않는다
    assert w.action == "probe"
    gate.set()
    wait_until(qapp, lambda: w.action is None and w.pending == 0)
    run = w.session.probe_runs[-1]
    assert run["stages"]["C1"]["ok"] is False and run["cleanup"]["detail"]["copy_found"] is False
    assert w.session.steps["probe"].ok is False
    text = w.report_text({})
    assert "[기능 점검]" in text and "C1: 안 됨" in text


def test_probe_needs_confirm(qapp, make_window, fake):
    w = connected_window(qapp, make_window, fake)
    w.confirm = lambda *a: False
    before = len(fake.requests)
    w.show_check_page()
    w.probe_btn.click()
    settle(qapp, 0.2)
    assert len(fake.requests) == before and w.action is None


def test_leftover_copy_is_deleted_only_after_confirm(qapp, make_window, fake, tmp_path):
    from engine.resolve_link.probe import ProbeState, ProbeStateStore

    store = ProbeStateStore(tmp_path / "state" / "caps" / "probe_state.json")
    store.save(ProbeState(original_uid="tl-1", original_name="Timeline 1", copy_uid="tl-2",
                          copy_name="AI 도우미 점검용 101500", last_fp="3:1:2", last_stage="C3"))
    sent = []

    def probe_copy(args):
        sent.append(dict(args))
        return {"ok": True, "stage": "C8", "detail": {"deleted": True, "copy_found": True}, "calls": {}}

    fake.handlers["probe_copy"] = probe_copy
    w = connected_window(qapp, make_window, fake)
    w.show_check_page()
    assert w.check_page.leftover_btn.isVisible() and w.check_page.leftover_btn.isEnabled()
    asked = []
    w.confirm = lambda *a: asked.append(a) or False
    w.check_page.leftover_btn.click()
    settle(qapp, 0.2)
    assert sent == [] and asked[0][1] == S.LEFTOVER_CONFIRM.format(name="AI 도우미 점검용 101500")
    w.confirm = lambda *a: True
    w.check_page.leftover_btn.click()
    wait_until(qapp, lambda: w.action is None and w.pending == 0)
    assert sent == [{"stage": "C8", "expect_fingerprint": "3:1:2", "original_uid": "tl-1",
                     "original_name": "Timeline 1", "copy_uid": "tl-2", "copy_name": "AI 도우미 점검용 101500"}]
    assert w.message.text().endswith(S.LEFTOVER_DELETED)
    assert store.load() is None and not w.check_page.leftover_btn.isVisible()


def test_old_script_is_shown_and_blocks_slots(qapp, make_window, fake):
    fake.old_script = True
    w = connected_window(qapp, make_window, fake)
    assert w.status.text() == S.STATUS_OLD_SCRIPT and w.header.hint.text() == S.OLD_SCRIPT_HINT
    assert w.automation.buttons[0].receipt.text() == S.SLOT_DISABLED_OLD_SCRIPT
    assert not w.probe_btn.isEnabled()
    assert "1.0.0" in w.log_view.toPlainText()


def test_script_without_add_markers_asks_for_one_more_click(qapp, make_window, fake):
    """2.1a 때 켠 스크립트(1.1.0이지만 add_markers가 없음): 버튼을 꺼 두고 스크립트를 한 번 더 누르라고 한다."""
    from engine.resolve_link.protocol import OPS

    real = fake.request

    def request(op, args=None, timeout=None, cancel=None, retry=None):
        out = real(op, args, timeout=timeout, cancel=cancel, retry=retry)
        if op == "ping":
            fake.known_ops = frozenset(o for o in OPS if o != "add_markers")
        return out

    fake.request = request
    w = connected_window(qapp, make_window, fake)
    assert w.status.text() == S.STATUS_OLD_SCRIPT
    assert w.automation.buttons[0].receipt.text() == S.SLOT_DISABLED_OLD_SCRIPT
    assert not w.automation.buttons[0].isEnabled()
