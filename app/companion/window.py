"""AI 편집 도우미 창 (설계 B1). 화면 오른쪽 끝에 좁게 붙는다.

위에서 아래로: 머리말(연결 불빛, [연결 확인], ⋯, 타임라인 요약) · 자동화 버튼 1·2·3 · 대화 칸 ·
[↶ 되돌리기 ▾] [결과 저장]. ⋯ > 연결 점검에는 예전 시험 도구와 [기능 점검]이 있다. ⚙는 버튼 설정 쪽.

이 파일은 얇게 둔다: 화면 조각은 *_view.py / check_page.py / slot_settings.py / cards.py, 연결 상태는
connection.py, 자동화 버튼의 흐름(계산·카드·넣기·되돌리기)은 runs.py, 작업 스레드는 tasks.py와 jobs.py,
결과 파일은 report.py. 리졸브의 답을 기다리는 일은 모두 작업 스레드에서 하고, 결과 저장은 파일만 읽으므로
작업 중에도 누를 수 있다.
"""

from __future__ import annotations

import copy
import functools
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from PySide6.QtCore import QEvent, QPoint, QRect, QStandardPaths, Qt, QTimer, QUrl, Slot
from PySide6.QtGui import QActionGroup, QDesktopServices, QGuiApplication
from PySide6.QtWidgets import QMainWindow, QMenu, QMessageBox, QStackedWidget, QVBoxLayout, QWidget

from engine.resolve_link import SCRIPT_VERSION
from engine.resolve_link.bridge import BridgeError, BridgeTimeout, LuaBridge, OldScript
from engine.resolve_link.caps import CapabilityStore
from engine.resolve_link.ops import ResolveOps, TimelineInfo
from engine.resolve_link.paths import long_path, state_dir
from engine.resolve_link.probe import ProbeRunner, ProbeStateStore, delete_leftover, make_probe_wav
from engine.resolve_link.transport import LuaTransport
from engine.settings import TEXT_SCALES, Settings

from . import connection as conn
from . import report as report_mod
from . import steps
from . import strings_ko as S
from . import theme
from .automation_view import AutomationView
from .chat_flow import ChatController
from .chat_view import ChatView
from .check_page import CheckPage
from .connection import AUTO_PING_MS, AUTO_PING_SLOW_MS, AUTO_SLOW_AFTER, UNREAD_LIMIT  # noqa: F401 - 예전 이름
from .header_view import HeaderView
from .report import TestSession, build_report, save_report
from .runs import RunController
from .slot_settings import SlotSettingsPage
from .tasks import Relay, ShortTasks, TaskQueue
from .undo_view import UndoView

WINDOW_TITLE = S.WINDOW_TITLE
UNREAD_TEXT = S.UNREAD_TEXT
SLOW_ANSWER_TEXT = S.SLOW_ANSWER_TEXT
WINDOW_WIDTH = 420  # 기본 너비
NARROW_WIDTH = 380  # 화면 논리 너비가 NARROW_SCREEN보다 좁을 때
NARROW_SCREEN = 1600
MIN_WIDTH, MAX_WIDTH = 360, 460
FULL_MIN_H = 820  # 이 높이 이상이면 모두 보임
TILES_MIN_H = 680  # 이 높이 이상이면 버튼을 타일로, 그보다 낮으면 머리말 한 줄
MODES = ("full", "tiles", "tight")
CHAT_LOG_MIN = {"full": 100, "tiles": 60, "tight": 40}  # full: 대화 칸 전체가 220 이상 되게


def layout_mode(height: int, text_scale: int = 100) -> str:
    """높이(논리 픽셀)에 맞는 모양. 글자 130%이면 한 단계 더 좁게 (설계 B1.3)."""
    i = 0 if height >= FULL_MIN_H else 1 if height >= TILES_MIN_H else 2
    if text_scale >= 130:
        i = min(2, i + 1)
    return MODES[i]


def default_width(screen_width: int) -> int:
    return NARROW_WIDTH if screen_width < NARROW_SCREEN else WINDOW_WIDTH


def desktop_dir() -> Path:
    """바탕 화면 폴더 (원드라이브로 옮겨진 경우도 Qt가 찾아 준다). 없으면 홈 폴더."""
    loc = QStandardPaths.writableLocation(QStandardPaths.DesktopLocation)
    if loc and Path(loc).is_dir():
        return Path(loc)
    return Path.home()


# ── 작업 스레드에서 도는 단계 (창 버튼마다 하나) ─────────────────────────

def _read_timeline(bridge, out: Dict[str, Any]) -> None:
    """고친 뒤 머리말을 맞추려고 타임라인 정보를 다시 읽는다. 못 읽어도 단계는 된 것이다."""
    try:
        out["state"] = bridge.request("timeline_info")
        out["state_kind"] = "timeline_info"
    except (BridgeError, BridgeTimeout) as exc:
        out["state_error"] = steps.error_info(exc, answered=True)


def _need(bridge, op: str) -> None:
    supports = getattr(bridge, "supports", None)
    if not callable(supports) or supports(op) is not True:
        raise OldScript(op)


def probe_step(bridge, out: Dict[str, Any], *, store: ProbeStateStore, caps_store: CapabilityStore,
               on_stage: Optional[Callable[[str, Dict[str, Any]], None]] = None) -> None:
    """기능 점검: probe_read + 복사본에서 C1~C7, 무슨 일이 있어도 C8(정리). 결과는 점검 기록(caps)에도."""
    out["ping"] = bridge.ping(timeout=steps.PING_TIMEOUT)
    _need(bridge, "probe_copy")
    ops = ResolveOps(LuaTransport(bridge))
    folder = Path(bridge.mailbox) / "files" / "probe"  # Lua가 가져올 수 있는 곳은 우체통의 files 폴더뿐
    runner = ProbeRunner(ops, store, wav_maker=lambda: make_probe_wav(folder), on_stage=on_stage)
    run = runner.run()
    out["probe_run"] = run.to_dict()
    ping = out["ping"] if isinstance(out["ping"], dict) else {}
    key = (ping.get("product"), ping.get("product_version") or ping.get("resolve_version"), ping.get("script_version"))
    if isinstance(run.read, dict) and run.read.get("ok") is not False:
        out["caps"] = caps_store.record_read(*key, run.read)
    if run.stages:
        stages = dict(run.stages)
        if run.cleanup:
            stages["C8"] = run.cleanup
        out["caps"] = caps_store.record_copy(*key, stages)
    _read_timeline(bridge, out)


def switch_step(bridge, out: Dict[str, Any], *, uid: Optional[str], name: Optional[str]) -> None:
    """[원래 타임라인으로]: 점검 때 적어 둔 원래 타임라인으로만 옮긴다 (아무것도 고치지 않음)."""
    out["ping"] = bridge.ping(timeout=steps.PING_TIMEOUT)
    _need(bridge, "switch_timeline")
    args = {k: v for k, v in (("uid", uid), ("name", name)) if v}
    out["switch"] = bridge.request("switch_timeline", args)
    _read_timeline(bridge, out)


def leftover_step(bridge, out: Dict[str, Any], *, store: ProbeStateStore) -> None:
    """남은 점검용 복사본 지우기 (사용자가 확인한 뒤). 지문이 점검 때와 같을 때만 Lua가 지운다."""
    out["ping"] = bridge.ping(timeout=steps.PING_TIMEOUT)
    _need(bridge, "probe_copy")
    result = delete_leftover(ResolveOps(LuaTransport(bridge)), store, confirmed=True)
    out["leftover"] = {"deleted": result.deleted, "reason": result.reason, "detail": result.detail}
    _read_timeline(bridge, out)


# ── 창 ───────────────────────────────────────────────────────────────


def reconciled_line(r: Dict[str, Any]) -> str:
    """맞춰 본 결과 한 줄 (쉬운 우리말: 영어 상태 이름을 보이지 않는다)."""
    status = r.get("status")
    found, expected = int(r.get("found") or 0), int(r.get("expected") or 0)
    if r.get("op") == "clear_marks":
        summary = {"applied": S.RECONCILED_CLEAR_APPLIED, "partial": S.RECONCILED_CLEAR_PARTIAL}.get(
            status, S.RECONCILED_CLEAR_UNDONE)
        return S.RECONCILED_CLEAR.format(summary=summary.format(found=found, expected=expected))
    summary = {"applied": S.RECONCILED_APPLIED, "partial": S.RECONCILED_PARTIAL}.get(status, S.RECONCILED_UNDONE)
    return S.RECONCILED.format(summary=summary.format(found=found, expected=expected))

class HelperWindow(QMainWindow):
    def __init__(
        self,
        bridge=None,
        interactive: bool = True,
        install: Optional[Dict[str, Any]] = None,
        report_dir: Optional[Path] = None,
        auto_ping_ms: int = AUTO_PING_MS,
        *,
        state_root: Optional[Path] = None,
        process_check: Any = "default",
        scheduler=None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        super().__init__()
        # interactive=False(자동 검사)이면 결과 폴더를 열지 않는다.
        self.interactive = interactive
        self.bridge = bridge if bridge is not None else LuaBridge()
        self.report_dir = Path(report_dir) if report_dir is not None else None
        self.state_root = Path(state_root) if state_root is not None else state_dir()
        self.settings = Settings(self.state_root / "settings.json")
        self.caps_store = CapabilityStore(self.state_root / "caps")
        self.probe_store = ProbeStateStore(self.state_root / "caps" / "probe_state.json")
        self.session = TestSession()
        if install:
            self.session.install = dict(install)
        self.action: Optional[str] = None  # 지금 하는 버튼 작업 (시험 도구, 기능 점검 ...)
        self._action_t0 = 0.0
        self.report_pending = False
        self.closing = False
        self.last_report: Optional[Path] = None
        self.audio_items: List[Dict[str, Any]] = []  # 마지막으로 읽은 소리 클립 (결과 파일의 ffprobe용)
        self._items_ident: Optional[str] = None
        self._journal_key: Optional[str] = None
        self._timeline_info: Optional[TimelineInfo] = None
        self.caps = None
        self._warned_version: Optional[str] = None
        self._probe_state = self.probe_store.load()
        self.layout_name = "full"
        self._placed = False
        self.confirm: Callable[[str, str, str], bool] = self._ask  # 시험에서 바꾼다
        # 여러 답 중 하나 (모두 빼기, 되돌리기 확인): 누른 단추의 번호, 닫으면 None. 시험에서 바꾼다
        self.choose: Callable[[str, str, List[str]], Optional[int]] = self._choose

        self.setWindowTitle(WINDOW_TITLE)
        self.setMinimumWidth(MIN_WIDTH)
        self.setWindowFlag(Qt.WindowStaysOnTopHint, bool(self.settings.ui.get("always_on_top", True)))

        self.queue = TaskQueue(self.bridge, self)
        self.thread = self.queue.thread
        self.worker = self.queue.worker
        self.shorts = ShortTasks(self)
        self.relay = Relay(self)
        self.relay.stage.connect(self._on_probe_stage)
        self.controller = conn.ConnectionController(
            self.queue, session=self.session, scheduler=scheduler, clock=clock, process_check=process_check,
            auto_ping_ms=auto_ping_ms, extras=self._connect_extras, parent=self,
        )
        self._chips_shown = False  # 처음 연결됐을 때의 예문 칩을 보였는지
        self.runs = RunController(self)
        self.chat_flow = ChatController(self)
        self.controller.job_busy = lambda: self.runs.busy
        self._build()
        self.controller.changed.connect(self._refresh)
        self.controller.answered.connect(self._on_answered)
        self.controller.failed.connect(self._on_check_failed)
        self.apply_text_scale(self.text_scale, save=False)
        self.place_at_right_edge()

        mailbox = str(getattr(self.bridge, "mailbox", "") or "")
        if not mailbox.isascii():
            # 영문 폴더를 하나도 만들 수 없던 PC: 창은 띄우되 이유를 보이게 한다 (결과 파일에도 남는다)
            self.log(S.NON_ASCII_MAILBOX.format(path=mailbox))
        if self.settings.moved_bad is not None:
            self.log(S.SETTINGS_MOVED_BAD.format(path=self.settings.moved_bad))
        self._refresh()
        self.controller.start()

    # ── 화면 만들기 ────────────────────────────────────────────────────

    def _build(self) -> None:
        self.stack = QStackedWidget()
        self.setCentralWidget(self.stack)
        self.panel = QWidget()
        self.panel.setObjectName("panel")
        col = QVBoxLayout(self.panel)
        col.setContentsMargins(12, 10, 12, 8)
        col.setSpacing(8)
        self.header = HeaderView()
        self.automation = AutomationView(self.settings.slots)
        self.chat = ChatView()
        self.footer = UndoView()
        col.addWidget(self.header)
        col.addWidget(self.automation)
        col.addWidget(self.chat, 1)
        col.addWidget(self.footer)
        self.check_page = CheckPage()
        self.settings_page = SlotSettingsPage()
        self.stack.addWidget(self.panel)
        self.stack.addWidget(self.check_page)
        self.stack.addWidget(self.settings_page)

        self.header.check_clicked.connect(self.on_connect)
        self.header.back_clicked.connect(self.on_switch_back)
        self.automation.slot_clicked.connect(self.on_slot)
        self.automation.settings_clicked.connect(self.on_slot_settings)
        self.automation.stop_clicked.connect(self.runs.stop)
        self.chat.sent.connect(self.on_chat)
        self.chat.set_collapsed(bool(self.settings.ui.get("chat_collapsed")))
        self.chat.collapsed_changed.connect(lambda v: self._save_ui("chat_collapsed", v))
        self.footer.report_btn.clicked.connect(self.on_report)
        self.footer.undo_requested.connect(self.runs.undo_from_list)
        self.footer.remove_all_requested.connect(self.runs.remove_all)
        self.settings_page.back_clicked.connect(self.show_panel)
        self.settings_page.saved.connect(self._settings_saved)
        self.settings_page.move_requested.connect(self._settings_move)
        self.settings_page.restore_requested.connect(self._settings_restore)
        self.check_page.manual_btn.clicked.connect(self.on_manual_checks)
        self.check_page.back_clicked.connect(self.show_panel)
        self.check_page.probe_btn.clicked.connect(self.on_probe)
        self.check_page.marker_btn.clicked.connect(self.on_marker)
        self.check_page.audio_btn.clicked.connect(self.on_audio)
        self.check_page.cleanup_btn.clicked.connect(self.on_cleanup)
        self.check_page.leftover_btn.clicked.connect(self.on_leftover)

        self.more_menu = QMenu(self)
        self.more_menu.addAction(S.MENU_CHECK_PAGE, self.show_check_page)
        self.more_menu.addAction(S.MENU_REPORT, self.on_report)
        settings_menu = self.more_menu.addMenu(S.MENU_SETTINGS)
        self.on_top_action = settings_menu.addAction(S.MENU_ON_TOP)
        self.on_top_action.setCheckable(True)
        self.on_top_action.setChecked(bool(self.settings.ui.get("always_on_top", True)))
        self.on_top_action.toggled.connect(self.set_on_top)
        size_menu = settings_menu.addMenu(S.MENU_TEXT_SIZE)
        group = QActionGroup(self)
        self.scale_actions = {}
        for scale in TEXT_SCALES:
            act = size_menu.addAction(S.MENU_TEXT_SCALE.format(scale=scale))
            act.setCheckable(True)
            act.setChecked(scale == self.text_scale)
            act.triggered.connect(lambda _=False, v=scale: self.apply_text_scale(v))
            group.addAction(act)
            self.scale_actions[scale] = act
        self.more_menu.addAction(S.MENU_HELP, self.show_help)
        self.header.more_btn.setMenu(self.more_menu)

    # 예전 창과 같은 이름 (시험과 app/__main__.py가 쓴다)
    @property
    def connect_btn(self):
        return self.header.check_btn

    @property
    def marker_btn(self):
        return self.check_page.marker_btn

    @property
    def audio_btn(self):
        return self.check_page.audio_btn

    @property
    def cleanup_btn(self):
        return self.check_page.cleanup_btn

    @property
    def probe_btn(self):
        return self.check_page.probe_btn

    @property
    def report_btn(self):
        return self.footer.report_btn

    @property
    def status(self):
        return self.header.status

    @property
    def info(self):
        return self.header.info

    @property
    def message(self):
        return self.header.message

    @property
    def log_view(self):
        return self.check_page.log_view

    @property
    def timer(self):
        return self.controller.timer

    @property
    def auto_stopped(self) -> bool:
        return self.controller.auto_stopped

    @property
    def _auto_step(self):
        return self.controller.auto_step

    @property
    def pending(self) -> int:
        return self.queue.pending

    @property
    def connected(self) -> bool:
        return self.controller.connected

    @property
    def text_scale(self) -> int:
        value = self.settings.ui.get("text_scale", 100)
        return value if value in TEXT_SCALES else 100

    def menu_entries(self) -> List[str]:
        return [a.text() for a in self.more_menu.actions() if not a.isSeparator()]

    # ── 크기와 자리 ────────────────────────────────────────────────────

    def place_at_right_edge(self) -> None:
        """주 화면 오른쪽 끝에 화면 높이만큼 (작업 표시줄 제외). 너비 420, 좁은 화면이면 380."""
        screen = self.screen() or QGuiApplication.primaryScreen()
        if screen is None:
            return
        avail = screen.availableGeometry()
        extra_w = max(0, self.frameGeometry().width() - self.width())
        extra_h = max(0, self.frameGeometry().height() - self.height())
        wanted = self.settings.ui.get("width") or default_width(avail.width())
        width = max(MIN_WIDTH, min(MAX_WIDTH, int(wanted), avail.width() - extra_w))
        self.resize(width, max(300, avail.height() - extra_h))
        self.move(avail.x() + avail.width() - width - extra_w, avail.y())
        self._apply_layout()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if not self._placed:
            # 창 테두리 크기는 화면에 띄운 뒤에야 알 수 있어 한 번 더 맞춘다.
            self._placed = True
            QTimer.singleShot(0, self.place_at_right_edge)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._apply_layout()

    def _apply_layout(self) -> None:
        mode = layout_mode(self.height(), self.text_scale)
        self.layout_name = mode
        self.header.set_mode(mode)
        self.automation.set_mode("rows" if mode == "full" else "tiles")
        self.chat.set_min_height(CHAT_LOG_MIN[mode])

    def screen_metrics(self) -> Dict[str, Any]:
        """결과 파일용: 화면 크기(논리), 배율, 창 크기, 모양 (창 스레드에서 읽는다)."""
        screen = self.screen() or QGuiApplication.primaryScreen()
        out: Dict[str, Any] = {"window": [self.width(), self.height()], "layout": self.layout_name,
                               "text_scale": self.text_scale}
        if screen is not None:
            g, a = screen.geometry(), screen.availableGeometry()
            out.update({
                "screen": screen.name(), "geometry": [g.x(), g.y(), g.width(), g.height()],
                "available": [a.x(), a.y(), a.width(), a.height()],
                "device_pixel_ratio": screen.devicePixelRatio(), "logical_dpi": screen.logicalDotsPerInch(),
            })
        return out

    @Slot(bool)
    def set_on_top(self, on: bool) -> None:
        visible = self.isVisible()
        self.setWindowFlag(Qt.WindowStaysOnTopHint, on)
        if visible:
            self.show()  # 창 속성을 바꾸면 창이 숨으므로 다시 띄운다
        self._save_ui("always_on_top", bool(on))

    def apply_text_scale(self, scale: int, save: bool = True) -> None:
        if scale not in TEXT_SCALES:
            return
        if save:
            self._save_ui("text_scale", scale)
        act = getattr(self, "scale_actions", {}).get(scale)
        if act is not None and not act.isChecked():
            act.setChecked(True)
        self.setStyleSheet(theme.stylesheet(scale))
        self._apply_layout()

    def _save_ui(self, key: str, value: Any) -> None:
        try:
            self.settings.set_ui(key, value)
        except (OSError, ValueError):
            pass  # 설정을 못 써도 창은 그대로 쓴다

    # ── 쪽 바꾸기 ─────────────────────────────────────────────────────

    @Slot()
    def show_check_page(self) -> None:
        self._probe_state = self.probe_store.load()
        self.stack.setCurrentWidget(self.check_page)
        self._refresh()

    @Slot()
    def show_panel(self) -> None:
        self.stack.setCurrentWidget(self.panel)

    @Slot()
    def show_help(self) -> None:
        self.chat.add_helper(S.HELP_TEXT)
        self.show_panel()

    # ── 상태를 화면에 ──────────────────────────────────────────────────

    def show_message(self, text: str) -> None:
        self.header.set_message(text)

    def log(self, text: str) -> None:
        self.check_page.log(text)

    def _switch_target(self) -> Optional[Tuple[Optional[str], Optional[str]]]:
        """[원래 타임라인으로]가 갈 곳: 스크립트가 적어 둔 원래 타임라인, 없으면 창이 적어 둔 것."""
        rec = self.controller.timeline.get("probe")
        if isinstance(rec, dict) and (rec.get("original_uid") or rec.get("original_name")):
            return rec.get("original_uid"), rec.get("original_name")
        st = self._probe_state
        if st is not None and (st.original_uid or st.original_name):
            return st.original_uid, st.original_name
        return None

    def _supports(self, op: str) -> bool:
        supports = getattr(self.bridge, "supports", None)
        return callable(supports) and supports(op) is True

    def _slot_reason(self) -> Optional[str]:
        c = self.controller
        if self.closing or not c.connected:
            return S.SLOT_DISABLED_NOT_CONNECTED
        if c.status == conn.OLD_SCRIPT:
            return S.SLOT_DISABLED_OLD_SCRIPT
        if c.probe_copy_name:
            return S.SLOT_DISABLED_PROBE_COPY
        if self.action is not None or self.runs.busy:
            return S.SLOT_DISABLED_BUSY
        return None

    @property
    def busy(self) -> bool:
        """버튼 작업(시험 도구 등)이나 자동화 일(계산·넣기·되돌리기)을 하는 중."""
        return self.action is not None or self.runs.busy

    @Slot()
    def _refresh(self) -> None:
        c = self.controller
        idle = not self.busy and not self.closing
        self.header.set_status(c.status, checking=c.checking)
        self.header.set_probe_copy(c.probe_copy_name, can_switch=self._switch_target() is not None)
        self.header.back_btn.setEnabled(idle and c.connected)
        # [연결 확인]은 작업 중에도 누를 수 있다 (설계 B10). 줄 뒤에 선다.
        self.connect_btn.setEnabled(not self.closing)
        running = self.runs.running_slot if self.runs.busy else None
        self.automation.set_reason(self._slot_reason(), running)
        tools = idle and c.connected
        for btn in (self.marker_btn, self.audio_btn, self.cleanup_btn):
            btn.setEnabled(tools)
        self.probe_btn.setEnabled(tools and c.status != conn.OLD_SCRIPT and self._supports("probe_copy"))
        st = self._probe_state
        leftover = st is not None and st.has_copy
        self.check_page.leftover_btn.setVisible(leftover)
        self.check_page.leftover_btn.setEnabled(tools and leftover)
        # 되돌리기는 다른 일이 도는 동안 꺼 둔다 (설계 B10). ⚙와 대화 입력은 늘 켜 둔다.
        self.footer.undo_btn.setEnabled(idle)
        self.footer.set_connected(c.connected and c.status != conn.OLD_SCRIPT and not c.probe_copy_name)
        # 연결이 안 될 때 보내 주는 결과 파일이 가장 중요하므로 결과 저장은 작업 중에도 누를 수 있다.
        self.report_btn.setEnabled(not self.closing and not self.report_pending)
        runs = getattr(self, "runs", None)
        if runs is not None:
            runs.sync_cards()  # 일하는 중이거나 연결이 끊기면 카드의 [리졸브에 넣기]·되돌리기를 꺼 둔다
        chat_flow = getattr(self, "chat_flow", None)
        if chat_flow is not None:
            chat_flow.idle()  # 일이 끝났으면 대화에서 남은 일·기다리던 말을 잇는다

    def _show_info(self) -> None:
        info = self.controller.info
        self.header.show_info(info.get("ping"), info.get("state"), info.get("state_kind"), self.audio_items)

    def _finish(self, name: str, ok: bool, summary: str) -> None:
        title = S.STEP_TITLES.get(name, name)
        line = S.STEP_LINE.format(title=title, result=S.RESULT_OK if ok else S.RESULT_BAD, summary=summary)
        self.log(line)
        self.show_message(line)
        if name == self.action:
            self.session.timing(name, time.monotonic() - self._action_t0)
            self.action = None
        self._refresh()

    def _check_script_version(self) -> None:
        sv = getattr(self.bridge, "script_version", None)
        if sv and sv != SCRIPT_VERSION and sv != self._warned_version:
            self._warned_version = sv
            self.log(S.OLD_SCRIPT_LOG.format(version=sv))

    # ── 연결 ─────────────────────────────────────────────────────────

    def _connect_extras(self) -> List[Callable]:
        """연결 확인에 붙여 할 읽기 (필요할 때만 리졸브에 묻는다: 타임라인이 바뀜, 처음 보는 판, 넣다 멈춘 일)."""
        return [
            functools.partial(conn.extra_audio_items, known=self._items_ident),
            functools.partial(conn.extra_probe_read, store=self.caps_store),
            # 넣기·되돌리기·지우기가 도는 동안은 맞춰 보지 않는다 (그 일의 "넣는 중"은 남은 것이 아니다)
            functools.partial(conn.extra_reconcile, root=self.state_root, busy=lambda: self.runs.writing),
        ]

    @Slot()
    def on_connect(self) -> None:
        self.show_message(S.REQUESTING.format(title=S.STEP_TITLES["connect"]))
        self.controller.check_now("connect")

    def changeEvent(self, event) -> None:
        super().changeEvent(event)
        if event.type() == QEvent.ActivationChange and self.isActiveWindow() and not self.closing:
            # 창을 다시 볼 때: 연결돼 있고 마지막으로 물은 지 30초가 넘었을 때만 한 번
            self.controller.on_focus_in()

    def _remember(self, out: Dict[str, Any]) -> None:
        """답에서 창이 기억할 것: 소리 클립(결과 파일용), 점검 기록, 되돌리기 목록."""
        items = out.get("audio_items")
        state = out.get("state") if isinstance(out.get("state"), dict) else None
        if isinstance(items, dict):
            self.audio_items = list(items.get("items") or [])
            self._items_ident = items.get("ident")
        elif state is not None and out.get("state_kind") == "state":
            self.audio_items = list((state.get("items") or {}).get("audio") or [])
        if out.get("caps") is not None:
            self.caps = out["caps"]
        reconciled = out.get("reconciled") or []
        info = None
        if state is not None and out.get("state_kind") == "timeline_info" and state.get("timeline") is not None:
            info = TimelineInfo.from_result(state)
            self._update_undo_menu(info, force=bool(reconciled))
        for r in reconciled:
            self.chat.add_helper(reconciled_line(r))
        if reconciled:
            self.runs.on_reconciled(reconciled, info)

    @property
    def journal_key(self) -> Optional[str]:
        return self._journal_key

    def _ctrl_z_warning(self) -> bool:
        """Ctrl+Z 시험(M3) 답이 "다른 것이 되돌아감"이었으면 되돌리기 안내에 경고를 덧붙인다."""
        mine = self.session.manual.get("M3")
        if isinstance(mine, dict):
            return mine.get("answer") == "other"
        manual = getattr(self.caps, "manual", None)
        rec = manual.get("M3") if isinstance(manual, dict) else None
        answer = rec.get("answer") if isinstance(rec, dict) else None
        return isinstance(answer, dict) and answer.get("answer") == "other"

    def _update_undo_menu(self, info: TimelineInfo, force: bool = False) -> None:
        from engine.edits.journal import Journal, key_for

        key = key_for(info)
        self._timeline_info = info
        if key == self._journal_key and not force:
            return
        self._journal_key = key
        try:
            entries = Journal.for_timeline(info, self.state_root).summaries(20)
        except OSError:
            entries = []
        self.footer.set_entries(entries, warn_ctrl_z=self._ctrl_z_warning())

    def refresh_undo(self, force: bool = True) -> None:
        """넣기·되돌리기 뒤: 되돌리기 목록을 일지에서 다시 읽는다 (리졸브에는 묻지 않는다)."""
        if self._timeline_info is not None:
            self._update_undo_menu(self._timeline_info, force=force)

    def _connected_chips(self) -> None:
        """처음 연결됐을 때 한 번: 예문 칩 (설계 B1.5 "After connect")."""
        if self._chips_shown or self.controller.status != conn.CONNECTED or self.controller.probe_copy_name:
            return
        self._chips_shown = True
        self.chat.add_helper(S.CHAT_TRY_CONNECTED, [(t, t) for t in S.CHIPS_AFTER_CONNECT])

    @Slot(str, object)
    def _on_answered(self, name: str, out: Dict[str, Any]) -> None:
        if self.closing:
            return
        self._connected_chips()
        self._remember(out)
        self._show_info()
        ok, summary = steps.summarize("connect", out)
        self.session.record("connect", ok, summary, out)
        if name != "focus":
            self._finish("connect", ok, summary)
        self._check_script_version()
        self._refresh()

    @Slot(str, object)
    def _on_check_failed(self, name: str, exc: steps.StepFailed) -> None:
        if self.closing:
            return
        cause, partial = exc.cause, exc.partial
        answered = "ping" in partial
        if name == "auto_stopped":
            self.session.record("connect", False, S.UNREAD_TEXT, {}, steps.error_info(cause))
            self.log(S.UNREAD_TEXT)
            self.show_message(S.UNREAD_TEXT)
        elif name == "auto_slow":
            self.log(S.SLOW_ANSWER_TEXT)
        elif name == "auto" and not answered:
            message = steps.explain(cause, answered)
            self.session.record("connect", False, message, partial, steps.error_info(cause, answered))
            self.log(S.AUTO_PROBLEM.format(message=message))
        else:
            message = steps.explain(cause, answered)
            self.session.record("connect", False, message, partial, steps.error_info(cause, answered))
            self._show_info()
            if name != "focus":
                self._finish("connect", False, message)
        self._refresh()

    # ── 버튼 작업 (시험 도구, 기능 점검, 원래 타임라인으로, 남은 복사본 지우기) ────────

    def start_action(self, name: str, step, summarize: Optional[Callable[[Dict[str, Any]], Tuple[bool, str]]] = None) -> bool:
        if self.busy or self.closing:
            return False
        self.action = name
        self._action_t0 = time.monotonic()
        self.show_message(S.REQUESTING.format(title=S.STEP_TITLES.get(name, name)))
        self._refresh()
        self._submit_action(name, step, summarize)
        return True

    def _submit_action(self, name: str, step, summarize) -> None:
        self.controller.submit(name, step, lambda out: self._action_done(name, out, summarize),
                               lambda exc: self._action_failed(name, exc))

    def _action_done(self, name: str, out: Dict[str, Any], summarize) -> None:
        if self.closing:
            return
        if isinstance(out.get("state"), dict):
            out.setdefault("state_kind", "state")  # 예전 단계는 state를 읽는다
        # 모든 단계는 ping부터 하므로 답을 받았다면 연결된 것이다.
        self.controller.note_answer(out)
        self._remember(out)
        self._show_info()
        ok, summary = summarize(out) if summarize is not None else steps.summarize(name, out)
        self.session.record(name, ok, summary, out)
        self._finish(name, ok, summary)

    def _action_failed(self, name: str, exc: steps.StepFailed) -> None:
        if self.closing:
            return
        cause, partial = exc.cause, exc.partial
        # 이 단계에서 ping에 답했다면 연결은 된 것이다 (다음 작업이 늦거나 실패했을 뿐).
        answered = "ping" in partial
        message = steps.explain(cause, answered)
        if name == "probe":
            message = S.PROBE_FAILED.format(reason=message)
            self._probe_state = self.probe_store.load()
        elif name == "switch":
            message = S.SWITCH_FAILED.format(reason=message)
        self.session.record(name, False, message, partial, steps.error_info(cause, answered))
        if isinstance(partial.get("state"), dict):
            partial.setdefault("state_kind", "state")
        self.controller.note_failure(cause, answered, partial)
        self._show_info()
        self._finish(name, False, message)
        if name == "probe" and answered and "state" not in partial:
            # 기능 점검이 중간에 멈추면 지금 열린 타임라인(점검용 복사본인지)을 다시 읽는다 (설계 B7.2)
            self.controller.check_now("connect")

    @Slot()
    def on_marker(self) -> None:
        self.start_action("marker", steps.marker_step)

    @Slot()
    def on_audio(self) -> None:
        self.start_action("audio", steps.audio_step)

    @Slot()
    def on_cleanup(self) -> None:
        self.start_action("cleanup", steps.cleanup_step)

    # 기능 점검

    def _ask(self, title: str, text: str, ok_label: str) -> bool:
        if not self.interactive:
            return False  # 자동 검사에서는 묻는 창을 띄우지 않는다 (아무것도 하지 않음)
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Question)
        box.setWindowTitle(title)
        box.setText(text)
        ok = box.addButton(ok_label, QMessageBox.AcceptRole)
        cancel = box.addButton(S.BTN_CANCEL, QMessageBox.RejectRole)
        box.setDefaultButton(cancel)
        box.exec()
        return box.clickedButton() is ok

    def _choose(self, title: str, text: str, options: List[str]) -> Optional[int]:
        """단추 여러 개 중 하나. 마지막 단추는 취소(기본). 자동 검사에서는 묻지 않는다 (None)."""
        if not self.interactive or not options:
            return None
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Question)
        box.setWindowTitle(title)
        box.setText(text)
        buttons = []
        for i, label in enumerate(options):
            role = QMessageBox.RejectRole if i == len(options) - 1 else (
                QMessageBox.AcceptRole if i == 0 else QMessageBox.ActionRole)
            buttons.append(box.addButton(label, role))
        box.setDefaultButton(buttons[-1])
        box.exec()
        clicked = box.clickedButton()
        return next((i for i, b in enumerate(buttons) if b is clicked), None)

    @Slot()
    def on_probe(self) -> None:
        if self.busy or self.closing or not self.probe_btn.isEnabled():
            return
        if not self.confirm(S.PROBE_CONFIRM_TITLE, S.PROBE_CONFIRM, S.BTN_START):
            return
        self.action = "probe"
        self._action_t0 = time.monotonic()
        self.show_message(S.PROBE_SAVING)
        self._refresh()
        # 리졸브가 점검 중에 멈춰도 그 전까지의 결과가 남게 먼저 결과 파일을 저장한다
        if not self._save_report(then=lambda _path: self._probe_start(), silent=True):
            self._probe_start()

    def _probe_start(self) -> None:
        if self.closing:
            return
        step = functools.partial(probe_step, store=self.probe_store, caps_store=self.caps_store,
                                 on_stage=self.relay.stage.emit)
        self.show_message(S.PROBE_RUNNING.format(stage=""))
        self._submit_action("probe", step, self._probe_summary)

    @Slot(str, object)
    def _on_probe_stage(self, stage: str, data: Any) -> None:
        if self.closing:
            return
        title = S.PROBE_STAGE_NAMES.get(stage, stage)
        data = data if isinstance(data, dict) else {}
        ok = data.get("ok") is not False and not data.get("error")
        self.show_message(S.PROBE_RUNNING.format(stage=title))
        self.log(S.STEP_LINE.format(title=title, result=S.RESULT_OK if ok else S.RESULT_BAD,
                                    summary=data.get("error") or data.get("fingerprint") or ""))

    def _probe_summary(self, out: Dict[str, Any]) -> Tuple[bool, str]:
        run = out.get("probe_run") or {}
        self.session.probe_runs.append(run)
        self._probe_state = self.probe_store.load()
        if run.get("refused"):
            return False, S.PROBE_REFUSED.get(run["refused"], run["refused"])
        stages = [r for r in (run.get("stages") or {}).values() if isinstance(r, dict)]
        cleanup = run.get("cleanup")
        if isinstance(cleanup, dict):
            stages.append(cleanup)
        ok = sum(1 for r in stages if r.get("ok"))
        if run.get("leftover"):
            return False, S.PROBE_DONE_LEFTOVER
        return ok == len(stages), S.PROBE_DONE.format(ok=ok, bad=len(stages) - ok)

    # 원래 타임라인으로

    @Slot()
    def on_switch_back(self) -> None:
        target = self._switch_target()
        if target is None:
            self.show_message(S.PROBE_COPY_NO_RECORD)
            return
        uid, name = target
        self.start_action("switch", functools.partial(switch_step, uid=uid, name=name), self._switch_summary)

    def _switch_summary(self, out: Dict[str, Any]) -> Tuple[bool, str]:
        r = out.get("switch") or {}
        if r.get("switched"):
            return True, S.SWITCHED_BACK.format(name=r.get("readback_name") or "")
        return False, S.SWITCH_NOT_CONFIRMED.format(name=r.get("readback_name") or S.INFO_NO_NAME)

    # 남은 점검용 복사본

    @Slot()
    def on_leftover(self) -> None:
        if self.busy or self.closing:
            return
        st = self._probe_state = self.probe_store.load()
        if st is None or not st.has_copy:
            self.show_message(S.LEFTOVER_NO_STATE)
            self._refresh()
            return
        if not self.confirm(S.LEFTOVER_CONFIRM_TITLE, S.LEFTOVER_CONFIRM.format(name=st.copy_name or st.copy_uid),
                            S.BTN_DELETE):
            return
        self.start_action("leftover", functools.partial(leftover_step, store=self.probe_store), self._leftover_summary)

    def _leftover_summary(self, out: Dict[str, Any]) -> Tuple[bool, str]:
        r = out.get("leftover") or {}
        self._probe_state = self.probe_store.load()
        if r.get("deleted"):
            return True, S.LEFTOVER_DELETED
        reason = r.get("reason")
        if reason == "fingerprint_mismatch":
            return False, S.LEFTOVER_CHANGED
        if reason in ("no_state", "no_fingerprint", "not_confirmed"):
            return False, S.LEFTOVER_NO_STATE
        if reason == "other_project":
            d = (r.get("detail") or {}).get("detail") or {}
            return False, S.LEFTOVER_OTHER_PROJECT.format(project=d.get("recorded_project") or S.LEFTOVER_OPEN_PROJECT)
        if reason == "timelines_unreadable":
            return False, S.LEFTOVER_UNKNOWN
        return False, S.LEFTOVER_OTHER.format(reason=reason)

    # 자동화 버튼 (흐름은 runs.py)

    @Slot(int)
    def on_slot(self, number: int) -> None:
        if self.busy or self.closing:
            return
        self.action = "slot"
        self._action_t0 = time.monotonic()
        self._refresh()
        # 누르면 먼저 연결을 확인한다 (스크립트 판, 점검용 복사본인지, 넣다 멈춘 것 맞춰 보기)
        self.controller.before_action("slot", lambda out: self._slot_checked(number, out))

    def _slot_checked(self, number: int, out: Optional[Dict[str, Any]]) -> None:
        self.session.timing("slot", time.monotonic() - self._action_t0)
        self.action = None
        self._refresh()
        if out is None or self.closing:
            return
        reason = self._slot_reason()
        if reason is not None:
            self.show_message(reason)
            return
        self.runs.start_slot(number)

    # ⚙ 버튼 설정 (작업 중에도 열 수 있다, 설계 B10)

    def _in_out_ok(self) -> Optional[bool]:
        try:
            return self.caps.has("in_out") if self.caps is not None else None
        except (KeyError, AttributeError):
            return None

    def _slot_index(self, number: int) -> int:
        return next((i for i, s in enumerate(self.settings.slots) if s["slot"] == number), 0)

    @Slot(int)
    def on_slot_settings(self, number: int) -> None:
        try:
            slot = self.settings.slot(number)
        except KeyError:
            return
        self.settings_page.open(slot, self._slot_index(number), len(self.settings.slots), self._in_out_ok())
        self.stack.setCurrentWidget(self.settings_page)

    def _slots_changed(self) -> None:
        self.automation.set_slots(self.settings.slots)
        self._apply_layout()
        self._refresh()

    @Slot(int, str, str, dict)
    def _settings_saved(self, number: int, kind: str, name: str, params: Dict[str, Any]) -> None:
        before = copy.deepcopy(self.settings.slot(number))
        try:
            after = self.settings.configure_slot(number, kind, name, params)
        except (OSError, ValueError) as exc:
            self.show_message(S.SETTINGS_SAVE_FAILED.format(error=exc))
            return
        changed = after.get("previous") is not None and after != before
        self._slots_changed()
        self.show_panel()
        self.show_message(S.SETTINGS_SAVED.format(name=name) if changed else S.SETTINGS_UNCHANGED)

    @Slot(int, int)
    def _settings_move(self, number: int, delta: int) -> None:
        index = self._slot_index(number) + int(delta)
        if index < 0 or index >= len(self.settings.slots):
            return
        try:
            self.settings.move_slot(number, index)
        except (OSError, ValueError) as exc:
            self.show_message(S.SETTINGS_SAVE_FAILED.format(error=exc))
            return
        self._slots_changed()
        self.settings_page.set_order(self._slot_index(number), len(self.settings.slots))
        self.show_message(S.SETTINGS_MOVED)

    @Slot(int)
    def _settings_restore(self, number: int) -> None:
        try:
            done = self.settings.restore_previous(number)
        except (OSError, ValueError) as exc:
            self.show_message(S.SETTINGS_SAVE_FAILED.format(error=exc))
            done = False
        if not done:
            return
        self._slots_changed()
        self.show_panel()
        name = self.settings.slot(number).get("name") or ""
        self.show_message(S.SETTINGS_RESTORED.format(name=name))

    @Slot()
    def on_manual_checks(self) -> None:
        """연결 점검 쪽 [확인 질문 다시 보기]: Ctrl+Z 시험(M3)과 자르기 시험(M2) 질문을 다시 띄운다."""
        self.show_panel()
        self.runs.ask_manual("M3", again=True)
        self.runs.ask_manual("M2", again=True)

    @Slot(str)
    def on_chat(self, text: str) -> None:
        """대화 칸에 보냄 (흐름은 chat_flow.py). 시간을 풀어야 할 때만 먼저 ping한다."""
        self.chat_flow.send(text)

    # ── 결과 파일 ──────────────────────────────────────────────────────

    def _audio_paths(self) -> List[str]:
        paths: List[str] = []
        for item in self.audio_items:
            path = item.get("path") if isinstance(item, dict) else None
            if isinstance(path, str) and path and path not in paths:
                paths.append(path)
        return paths

    def link_info(self) -> Dict[str, Any]:
        b, c = self.bridge, self.controller
        prefs = getattr(b, "prefs_path", None)
        mailbox = getattr(b, "mailbox", None)
        known = getattr(b, "known_ops", None)
        timeline = {k: v for k, v in c.timeline.items() if k != "calls"}
        if timeline:
            timeline["kind"] = c.info.get("state_kind")
        return {
            "connected": c.connected,
            "status": c.status,
            "mailbox": str(mailbox or ""),
            "mailbox_long": long_path(Path(mailbox)) if mailbox else None,
            "prefs_path": str(prefs) if prefs else None,
            "script_version": getattr(b, "script_version", None),
            "known_ops": sorted(known) if known else None,
            "owner": getattr(b, "owner", None),
            "request_count": getattr(b, "request_count", None),
            "read_retries": getattr(b, "read_retries", None),
            "cleanup_pings": getattr(b, "cleanup_pings", None),
            "last_response": getattr(b, "last_response", None),
            "late_answers": list(getattr(b, "late_answers", None) or []),
            "auto_note": c.auto_note,
            "focus_pings": c.focus_pings,
            "process_checks": c.process_checks,
            "brain": S.CHAT_BRAIN_LINE,
            "screen": self.screen_metrics(),
            "timeline": timeline or None,
            "slots": list(self.settings.slots),
            "voice": copy.deepcopy(self.settings.voice),
            "perf": copy.deepcopy(self.settings.data.get("perf")),
            "analysis_cache": {"folder": str(self.runs.cache.folder), "hits": self.runs.cache.hits,
                               "misses": self.runs.cache.misses},
            "audio_paths": self._audio_paths(),
        }

    def report_text(self, env: Optional[Dict[str, Any]] = None) -> str:
        return build_report(self.session, self.link_info(), env or {})

    @Slot()
    def on_report(self) -> None:
        self._save_report()

    def _save_report(self, then: Optional[Callable[[Optional[Path]], None]] = None, silent: bool = False) -> bool:
        """결과를 모아 저장한다. 파일만 읽으므로 리졸브 줄을 기다리지 않는다 (작업 중에도 된다)."""
        if self.report_pending or self.closing:
            return False
        self.report_pending = True
        self._refresh()
        if not silent:
            self.show_message(S.COLLECTING)
        ping = self.controller.info.get("ping")
        caps_key = None
        if isinstance(ping, dict):
            caps_key = (ping.get("product"), ping.get("product_version") or ping.get("resolve_version"))
        bridge, root, paths = self.bridge, self.state_root, self._audio_paths()

        def job() -> Dict[str, Any]:
            env: Dict[str, Any] = {}
            try:
                report_mod.collect_env(bridge, env, audio_paths=paths, state_root=root, caps_key=caps_key)
            except Exception as exc:  # noqa: BLE001 - 모으다 실패해도 파일은 만든다
                env["error"] = steps.explain(exc)
            return env

        self.shorts.run(job, lambda env, error: self.write_report(
            env if env is not None else {"error": steps.explain(error)}, then=then, silent=silent),
            name="report")
        return True

    def write_report(self, env: Dict[str, Any], then=None, silent: bool = False) -> None:
        self.report_pending = False
        self._refresh()
        text = self.report_text(env)
        folder = self.report_dir if self.report_dir is not None else desktop_dir()
        path: Optional[Path] = None
        try:
            path = save_report(text, folder)
        except OSError:
            # 바탕 화면에 쓸 수 없으면 앱 기록 폴더에라도 남긴다.
            try:
                path = save_report(text, self.state_root)
            except OSError as exc:
                self.show_message(S.REPORT_FAILED.format(error=exc))
        if path is not None:
            self.last_report = path
            self.log(S.REPORT_SAVED_LOG.format(path=path))
            if not silent:
                self.show_message(S.REPORT_SAVED.format(path=path))
                if self.interactive:
                    QDesktopServices.openUrl(QUrl.fromLocalFile(str(path.parent)))
        if then is not None:
            then(path)

    # ── 자동 검사용 ───────────────────────────────────────────────────

    def _inside(self, widget: QWidget) -> bool:
        top_left = widget.mapTo(self, QPoint(0, 0))
        return self.rect().contains(QRect(top_left, widget.size()))

    def smoke_check(self) -> List[str]:
        """자동 검사(--helper-smoke-test): 버튼 3개와 ⚙, 대화 입력 칸, ⋯ 메뉴, 아래쪽이 잘리지 않고 보이는지."""
        problems: List[str] = []
        if len(self.automation.buttons) != 3:
            problems.append(f"slots={len(self.automation.buttons)}")
        widgets = [(b, f"slot{b.number}") for b in self.automation.buttons]
        widgets += [(g, f"gear{b.number}") for b, g in zip(self.automation.buttons, self.automation.gears)]
        widgets += [(self.chat.input, "chat_input"), (self.chat.send_btn, "send"), (self.header.more_btn, "more"),
                    (self.connect_btn, "connect"), (self.footer.undo_btn, "undo"), (self.report_btn, "report")]
        for w, label in widgets:
            if not w.isVisible():
                problems.append(f"{label}:hidden")
            elif not self._inside(w):
                problems.append(f"{label}:clipped")
        if self.chat.input.height() < theme.INPUT_H:
            problems.append(f"chat_input:height={self.chat.input.height()}")
        want = [S.MENU_CHECK_PAGE, S.MENU_REPORT, S.MENU_SETTINGS, S.MENU_HELP]
        entries = self.menu_entries()
        problems += [f"menu:{w}" for w in want if w not in entries]
        return problems

    # ── 닫기 ─────────────────────────────────────────────────────────

    def shutdown(self) -> None:
        """기다리던 요청을 바로 끝내고 작업 스레드를 멈춘다."""
        if self.closing:
            return
        self.closing = True
        # 계산은 멈추고(FFmpeg 끝냄), 리졸브 줄을 닫아 기다리던 요청을 끝낸 뒤 작업 스레드를 멈춘다
        self.runs.shutdown()
        self.controller.shutdown()
        self.queue.shutdown()
        self.runs.jobs.shutdown()
        self._refresh()

    def closeEvent(self, event) -> None:
        self.shutdown()
        event.accept()
