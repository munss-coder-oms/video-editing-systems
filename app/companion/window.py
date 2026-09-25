"""AI 도우미 창 (리졸브 연결 시험판).

화면 오른쪽 끝에 좁게 붙는 창이다. 리졸브에서 Workspace → Scripts → AI_Helper_Connect를 누르면
초록불이 켜지고, 버튼으로 지금 리졸브에 열린 타임라인에 표시와 소리를 넣어 본다.

리졸브의 답을 기다리는 일(최대 수십 초)은 모두 작업 스레드(BridgeWorker)에서 한다.
창은 결과를 신호로 받기만 해서, 리졸브가 없거나 대답이 늦어도 멈추지 않는다.
"""

from __future__ import annotations

import datetime
import os
import re
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from PySide6.QtCore import QObject, QStandardPaths, Qt, QThread, QTimer, QUrl, Signal, Slot
from PySide6.QtGui import QDesktopServices, QFont, QGuiApplication
from PySide6.QtWidgets import (
    QCheckBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from engine.resolve_link import SCRIPT_NAME, SCRIPT_VERSION
from engine.resolve_link.bridge import LuaBridge, prefs_backups
from engine.resolve_link.paths import (
    installed_scripts,
    long_path,
    resolve_exe_versions,
    resolve_utility_dirs,
    state_dir,
)
from engine.resolve_link.protocol import REQUEST_FILENAME, parse_responses

from . import steps
from . import report as report_mod
from .report import STEPS, TestSession, build_report, find_fatal_responses, save_report

WINDOW_TITLE = "AI 도우미 - 리졸브 연결 시험"
WINDOW_WIDTH = 420
AUTO_PING_MS = 2000  # 연결 전에는 2초마다 리졸브에 물어본다
AUTO_PING_SLOW_MS = 5000  # 오래 연결되지 않으면 이 간격으로 늦춘다 (리졸브 설정 파일을 덜 건드리게)
AUTO_SLOW_AFTER = 30  # 답 없는 자동 확인이 이만큼(약 1분) 이어지면 늦춘다
UNREAD_LIMIT = 3  # Fusion.prefs는 바뀌었는데 답을 못 읽은 일이 이만큼 이어지면 자동 확인을 멈춘다

WAITING_TEXT = f"리졸브에서 Workspace(워크스페이스) → Scripts(스크립트) → {SCRIPT_NAME}를 눌러 주세요"
CONNECTED_TEXT = "연결됨"
HINT_TEXT = "시험은 새 프로젝트에서 해 주세요. 영상 하나를 타임라인에 올려 두면 됩니다."
UNREAD_TEXT = (
    "리졸브가 답을 적은 것 같은데 이 창이 그 답을 읽지 못했습니다. 리졸브 설정 파일을 자꾸 건드리지 않도록 "
    "자동 확인을 멈췄습니다. [결과 저장]을 눌러 바탕 화면에 생긴 파일을 보내 주세요."
)
SLOW_ANSWER_TEXT = "리졸브의 답이 늦게 옵니다. 자동 확인에서 답을 더 오래 기다립니다."

GREY = "#9e9e9e"
GREEN = "#2e9d3a"

STEP_TITLES = dict(STEPS)


def desktop_dir() -> Path:
    """바탕 화면 폴더 (원드라이브로 옮겨진 경우도 Qt가 찾아 준다). 없으면 홈 폴더."""
    loc = QStandardPaths.writableLocation(QStandardPaths.DesktopLocation)
    if loc and Path(loc).is_dir():
        return Path(loc)
    return Path.home()


def file_name(path: Any) -> str:
    """리졸브가 준 경로에서 파일 이름만 (윈도우 경로를 다른 OS에서 받아도)."""
    return re.split(r"[\\/]", path)[-1] if isinstance(path, str) and path else ""


_MAILBOX_HEX_RE = re.compile(r'AIH\.MAILBOX_HEX\s*=\s*"([0-9A-Fa-f]*)"')
_VERSION_RE = re.compile(r'AIH\.VERSION\s*=\s*"([^"\r\n]*)"')
_CLAIM_RE = re.compile(r'\bClaim\s*=\s*"?(\d+)"?')


def _stamp(seconds: float) -> str:
    return datetime.datetime.fromtimestamp(seconds).strftime("%Y-%m-%d %H:%M:%S")


def script_details(paths, mailbox) -> List[Dict[str, Any]]:
    """설치된 스크립트마다 안에 적힌 판과 우체통 (예전 스크립트나 다른 우체통을 누른 것인지 알아보려고)."""
    out = []
    for path in paths:
        info: Dict[str, Any] = {"file": str(path)}
        try:
            text = Path(path).read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            info["error"] = str(exc)
            out.append(info)
            continue
        m, v = _MAILBOX_HEX_RE.search(text), _VERSION_RE.search(text)
        info["version"] = v.group(1) if v else None
        info["mailbox"] = None
        if m and len(m.group(1)) % 2 == 0:
            # 설치 프로그램과 같은 규칙: 윈도우는 ANSI 코드 페이지, 그 밖은 UTF-8
            encoding = "mbcs" if sys.platform == "win32" else "utf-8"
            info["mailbox"] = bytes.fromhex(m.group(1)).decode(encoding, "replace")
        info["same_mailbox"] = info["mailbox"] is not None and (
            os.path.normcase(info["mailbox"]) == os.path.normcase(str(mailbox)))
        out.append(info)
    return out


def prefs_details(bridge, paths) -> List[Dict[str, Any]]:
    """Fusion.prefs 후보마다 바뀐 시각, 크기, Claim, 들어 있는 답 (답을 기다리다 그만둔 요청의 답이면 late)."""
    timed_out = getattr(bridge, "timed_out", None) or {}
    note_late = getattr(bridge, "note_late_answers", None)
    out = []
    for path in paths:
        info: Dict[str, Any] = {"file": str(path)}
        try:
            st = Path(path).stat()
            text = Path(path).read_bytes().decode("latin-1")
        except OSError as exc:
            info["error"] = str(exc)
            out.append(info)
            continue
        info["mtime"], info["size"] = _stamp(st.st_mtime), st.st_size
        claims = _CLAIM_RE.findall(text)
        info["claim"] = claims[-1] if claims else None
        responses = parse_responses(text)
        if note_late is not None:
            note_late(Path(path), responses)
        info["responses"] = [
            {"id": r.id, "owner": r.owner, "ok": r.data.get("ok"), "error": r.data.get("error"),
             "late": r.id in timed_out}
            for r in responses
        ]
        out.append(info)
    return out


def request_file_info(mailbox) -> Dict[str, Any]:
    """우체통에 요청 파일이 남아 있는지 (창이 답을 기다리는 중이 아니면 보통 없다)."""
    path = Path(mailbox) / REQUEST_FILENAME
    try:
        st = path.stat()
        text = path.read_text(encoding="ascii", errors="replace")
    except OSError:
        return {"file": str(path), "exists": False}
    return {"file": str(path), "exists": True, "age_seconds": round(time.time() - st.st_mtime, 1),
            "size": st.st_size, "text": text[:300]}


def collect_env(bridge, out: Dict[str, Any]) -> None:
    """결과 파일에 넣을 설치·파일 상태 (Fusion.prefs 찾기가 느릴 수 있어 작업 스레드에서)."""
    # 윈도우 판을 읽을 때 파이썬이 'ver' 명령을 따로 실행한다 (윈도우 자동 검사에서 창이 1.2초 멈춤, 2026-09-25).
    # 그래서 이 PC 정보도 창이 아니라 작업 스레드에서 모은다.
    out["system"] = report_mod.system_lines()
    scripts = installed_scripts()
    out["installed_scripts"] = [str(p) for p in scripts]
    out["script_details"] = script_details(scripts, bridge.mailbox)
    out["utility_dirs"] = [str(p) for p in resolve_utility_dirs()]
    out["resolve_exe"] = resolve_exe_versions()
    candidates = list(bridge.prefs_candidates())
    out["prefs_candidates"] = [str(p) for p in candidates]
    out["prefs_details"] = prefs_details(bridge, candidates)
    out["fatal"] = find_fatal_responses(candidates)
    out["backups"] = [str(p) for p in prefs_backups(bridge.backup_dir())]
    out["request"] = request_file_info(bridge.mailbox)


class BridgeWorker(QObject):
    """작업 스레드에서 단계를 하나씩 차례로 돌린다."""

    done = Signal(str, object)  # 단계 이름, 리졸브의 답(사전)
    failed = Signal(str, object)  # 단계 이름, steps.StepFailed

    def __init__(self, bridge) -> None:
        super().__init__()
        self.bridge = bridge

    @Slot(str, object)
    def run(self, name: str, step) -> None:
        try:
            out = steps.run_step(step, self.bridge)
        except steps.StepFailed as exc:
            self.failed.emit(name, exc)
        else:
            self.done.emit(name, out)


class HelperWindow(QMainWindow):
    submit = Signal(str, object)

    def __init__(
        self,
        bridge=None,
        interactive: bool = True,
        install: Optional[Dict[str, Any]] = None,
        report_dir: Optional[Path] = None,
        auto_ping_ms: int = AUTO_PING_MS,
    ) -> None:
        super().__init__()
        # interactive=False(자동 검사)이면 결과 폴더를 열지 않는다.
        self.interactive = interactive
        self.bridge = bridge if bridge is not None else LuaBridge()
        self.report_dir = Path(report_dir) if report_dir is not None else None
        self.session = TestSession()
        if install:
            self.session.install = dict(install)
        self.connected = False
        self.action: Optional[str] = None  # 지금 하는 버튼 작업
        self.pending = 0  # 작업 스레드에 보낸 일 (자동 확인 포함)
        self.report_pending = False
        self.closing = False
        self.last_report: Optional[Path] = None
        self._last_auto_error = ""
        self._placed = False
        # 연결 전 자동 확인: 간격, 이어서 답이 없던 횟수, 답을 못 읽은 것 같은 횟수
        self._auto_ms = auto_ping_ms
        self._auto_misses = 0
        self._unread = 0
        self.auto_stopped = False  # 답을 못 읽는 것 같아 자동 확인을 멈춤 (① 버튼은 그대로 쓸 수 있음)
        self._auto_step = steps.auto_connect_step
        self._auto_note = ""

        self.setWindowTitle(WINDOW_TITLE)
        self.setMinimumWidth(340)
        self.setWindowFlag(Qt.WindowStaysOnTopHint, True)
        self._build()
        self.place_at_right_edge()
        mailbox = str(getattr(self.bridge, "mailbox", "") or "")
        if not mailbox.isascii():
            # 영문 폴더를 하나도 만들 수 없던 PC: 창은 띄우되 이유를 보이게 한다 (결과 파일에도 남는다)
            self.log(f"우체통 폴더 경로에 영문이 아닌 글자가 있어 리졸브가 이 폴더를 못 읽을 수 있습니다: {mailbox}")

        self.thread = QThread(self)
        self.worker = BridgeWorker(self.bridge)
        self.worker.moveToThread(self.thread)
        self.submit.connect(self.worker.run)
        self.worker.done.connect(self.on_done)
        self.worker.failed.connect(self.on_failed)
        self.thread.finished.connect(self.worker.deleteLater)
        self.thread.start()

        self.timer = QTimer(self)
        self.timer.setInterval(auto_ping_ms)
        self.timer.timeout.connect(self.auto_ping)
        self.set_connected(False)
        QTimer.singleShot(0, self.auto_ping)

    # ── 화면 ───────────────────────────────────────────────────────────

    def _build(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)

        status_row = QHBoxLayout()
        self.dot = QLabel("●")
        dot_font = QFont(self.font())
        dot_font.setPointSize(max(12, dot_font.pointSize() + 6))
        self.dot.setFont(dot_font)
        self.status = QLabel()
        self.status.setWordWrap(True)
        status_font = QFont(self.font())
        status_font.setBold(True)
        self.status.setFont(status_font)
        status_row.addWidget(self.dot, 0, Qt.AlignTop)
        status_row.addWidget(self.status, 1)
        root.addLayout(status_row)

        self.message = QLabel("")
        self.message.setWordWrap(True)
        self.message.setTextInteractionFlags(Qt.TextSelectableByMouse)
        root.addWidget(self.message)

        box = QGroupBox("리졸브에서 열려 있는 것")
        form = QFormLayout(box)
        self.info: Dict[str, QLabel] = {}
        for key, label in (
            ("version", "리졸브 버전"),
            ("project", "프로젝트"),
            ("timeline", "타임라인"),
            ("fps", "프레임 속도"),
            ("clips", "클립 수"),
            ("first_clip", "첫 클립 파일"),
        ):
            value = QLabel("-")
            value.setWordWrap(True)
            value.setTextInteractionFlags(Qt.TextSelectableByMouse)
            form.addRow(label, value)
            self.info[key] = value
        root.addWidget(box)

        self.connect_btn = self._button(root, "① 연결 확인", self.on_connect)
        self.marker_btn = self._button(root, "② 표시 찍기 시험", self.on_marker)
        self.audio_btn = self._button(root, "③ 소리 넣기 시험", self.on_audio)
        self.cleanup_btn = self._button(root, "시험 흔적 지우기", self.on_cleanup)
        self.report_btn = self._button(root, "결과 저장", self.on_report)
        self.marker_btn.setToolTip("재생 위치(빨간 세로줄)에 노란 표시를 하나 넣습니다.")
        self.audio_btn.setToolTip("새 오디오 트랙을 만들고 재생 위치에 3초짜리 '삐' 소리를 넣습니다.")
        self.cleanup_btn.setToolTip("이 창이 넣은 시험 표시와 시험 오디오 트랙만 지웁니다.")
        self.report_btn.setToolTip("시험 결과를 바탕 화면에 저장하고 폴더를 엽니다. 그 파일을 보내 주세요.")

        hint = QLabel(HINT_TEXT)
        hint.setWordWrap(True)
        hint.setStyleSheet(f"color: {GREY};")
        root.addWidget(hint)

        self.on_top = QCheckBox("항상 위에 두기 (리졸브 창에 가려지지 않게)")
        self.on_top.setChecked(True)
        self.on_top.toggled.connect(self.set_on_top)
        root.addWidget(self.on_top)

        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setPlaceholderText("한 일과 결과가 여기에 나옵니다.")
        root.addWidget(self.log_view, 1)

    def _button(self, layout: QVBoxLayout, text: str, slot) -> QPushButton:
        btn = QPushButton(text)
        btn.setMinimumHeight(36)
        btn.clicked.connect(slot)
        layout.addWidget(btn)
        return btn

    def place_at_right_edge(self) -> None:
        """주 화면 오른쪽 끝에 화면 높이만큼 (작업 표시줄 제외)."""
        screen = QGuiApplication.primaryScreen()
        if screen is None:
            return
        avail = screen.availableGeometry()
        extra_w = max(0, self.frameGeometry().width() - self.width())
        extra_h = max(0, self.frameGeometry().height() - self.height())
        width = min(WINDOW_WIDTH, avail.width() - extra_w)
        self.resize(width, max(300, avail.height() - extra_h))
        self.move(avail.x() + avail.width() - width - extra_w, avail.y())

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if not self._placed:
            # 창 테두리 크기는 화면에 띄운 뒤에야 알 수 있어 한 번 더 맞춘다.
            self._placed = True
            QTimer.singleShot(0, self.place_at_right_edge)

    @Slot(bool)
    def set_on_top(self, on: bool) -> None:
        visible = self.isVisible()
        self.setWindowFlag(Qt.WindowStaysOnTopHint, on)
        if visible:
            self.show()  # 창 속성을 바꾸면 창이 숨으므로 다시 띄운다

    def set_connected(self, connected: bool) -> None:
        self.connected = connected
        self.dot.setStyleSheet(f"color: {GREEN if connected else GREY};")
        self.status.setText(CONNECTED_TEXT if connected else WAITING_TEXT)
        if connected:
            # 다음에 끊기면 처음처럼 2초마다 확인한다
            self._auto_misses = self._unread = 0
            self.auto_stopped = False
            self.timer.setInterval(self._auto_ms)
        if connected or self.closing or self.auto_stopped:
            self.timer.stop()  # 연결된 뒤에는 버튼을 누를 때만 확인한다
        elif not self.timer.isActive():
            self.timer.start()
        self.update_buttons()

    def update_buttons(self) -> None:
        idle = self.action is None and not self.closing
        self.connect_btn.setEnabled(idle)
        for btn in (self.marker_btn, self.audio_btn, self.cleanup_btn):
            btn.setEnabled(idle and self.connected)
        # 연결이 안 될 때 보내 주는 결과 파일이 가장 중요하므로 결과 저장은 늘 누를 수 있다.
        self.report_btn.setEnabled(not self.closing and not self.report_pending)

    def show_message(self, text: str) -> None:
        self.message.setText(text)

    def log(self, text: str) -> None:
        self.log_view.appendPlainText(text)

    def show_state(self, out: Dict[str, Any]) -> None:
        ping, state = out.get("ping"), out.get("state")
        if ping:
            self.info["version"].setText(
                f"{ping.get('product') or '리졸브'} {ping.get('resolve_version') or '(판 모름)'}"
            )
        if not state:
            return
        project, timeline = state.get("project"), state.get("timeline")
        if project is None:
            self.info["project"].setText("열린 프로젝트 없음 → 리졸브에서 프로젝트를 열어 주세요")
            timeline_text = "-"
        else:
            self.info["project"].setText(project or "(이름 없음)")
            timeline_text = "열린 타임라인 없음 → 리졸브에서 타임라인을 열어 주세요" if timeline is None else (
                timeline or "(이름 없음)")
        self.info["timeline"].setText(timeline_text)
        fps = state.get("fps")
        self.info["fps"].setText(f"{fps}{' (드롭 프레임)' if state.get('drop_frame') else ''}" if fps else "-")
        items = state.get("items") or {}
        video, audio = items.get("video") or [], items.get("audio") or []
        clips = f"영상 {len(video)}개, 소리 {len(audio)}개" if timeline is not None and project is not None else "-"
        if state.get("truncated"):
            clips += " (많아서 일부만 셈)"
        self.info["clips"].setText(clips)
        first = next((file_name(i.get("path")) for i in [*video, *audio] if file_name(i.get("path"))), "")
        self.info["first_clip"].setText(first or "-")

    # ── 작업 보내기 ─────────────────────────────────────────────────────

    def run_task(self, name: str, step) -> None:
        self.pending += 1
        self.submit.emit(name, step)

    @Slot()
    def auto_ping(self) -> None:
        # 이미 무언가 기다리는 중이면 건너뛴다 (우체통은 한 칸이라 쌓아 둘 필요가 없다).
        if self.connected or self.closing or self.pending or self.auto_stopped:
            return
        self.session.auto_attempts += 1
        self.run_task("auto", self._auto_step)

    def start_action(self, name: str, step) -> None:
        if self.action is not None or self.closing:
            return
        self.action = name
        self.show_message(f"{STEP_TITLES.get(name, name)}: 리졸브에 요청하는 중...")
        self.update_buttons()
        self.run_task(name, step)

    @Slot()
    def on_connect(self) -> None:
        self.start_action("connect", steps.connect_step)

    @Slot()
    def on_marker(self) -> None:
        self.start_action("marker", steps.marker_step)

    @Slot()
    def on_audio(self) -> None:
        self.start_action("audio", steps.audio_step)

    @Slot()
    def on_cleanup(self) -> None:
        self.start_action("cleanup", steps.cleanup_step)

    @Slot()
    def on_report(self) -> None:
        if self.report_pending or self.closing:
            return
        self.report_pending = True
        self.update_buttons()
        self.show_message("결과를 모으는 중...")
        self.run_task("report", collect_env)

    # ── 결과 받기 ──────────────────────────────────────────────────────

    def _finish(self, name: str, ok: bool, summary: str) -> None:
        title = STEP_TITLES.get("connect" if name == "auto" else name, name)
        line = f"{title}: {'됨' if ok else '안 됨'} - {summary}"
        self.log(line)
        self.show_message(line)
        if name == self.action:
            self.action = None
        self.update_buttons()

    def _check_script_version(self) -> None:
        sv = getattr(self.bridge, "script_version", None)
        if sv and sv != SCRIPT_VERSION:
            self.log(f"리졸브에서 예전 스크립트({sv})가 돌고 있습니다. "
                     f"Workspace → Scripts → {SCRIPT_NAME}를 한 번 더 눌러 주세요.")

    @Slot(str, object)
    def on_done(self, name: str, out: Dict[str, Any]) -> None:
        self.pending -= 1
        if name == "report":
            self.write_report(out)
            return
        if self.closing:
            return
        was_connected = self.connected
        # 모든 단계는 ping부터 하므로 답을 받았다면 연결된 것이다.
        self.set_connected(True)
        self.show_state(out)
        ok, summary = steps.summarize(name, out)
        self.session.record("connect" if name == "auto" else name, ok, summary, out)
        self._finish(name, ok, summary)
        if not was_connected:
            self._check_script_version()

    def _auto_missed(self, cause) -> None:
        """자동 확인에 답이 없었다. 답을 못 읽는 것 같으면 멈추고, 오래 이어지면 간격을 늘린다."""
        self._auto_misses += 1
        # Fusion.prefs가 바뀌었는데 우리 답을 못 찾음: 스크립트는 답을 쓰는데 이 창이 못 읽는 것일 수 있다.
        # 그대로 두면 2초마다 리졸브가 설정 파일을 다시 저장하게 된다.
        self._unread = self._unread + 1 if getattr(cause, "prefs_changed", None) else 0
        if self._unread >= UNREAD_LIMIT:
            self.auto_stopped = True
            self.timer.stop()
            self._auto_note = "Fusion.prefs가 바뀌었는데 답을 읽지 못해 자동 확인을 멈춤"
            self.session.record("connect", False, UNREAD_TEXT, {}, steps.error_info(cause))
            self.log(UNREAD_TEXT)
            self.show_message(UNREAD_TEXT)
            return
        if getattr(self.bridge, "late_answers", None) and self._auto_step is steps.auto_connect_step:
            # 스크립트는 도는데 답이 1.5초보다 늦게 온다: 버튼을 누를 때처럼 더 오래 기다린다
            self._auto_step = steps.connect_step
            self._auto_note = SLOW_ANSWER_TEXT
            self.log(SLOW_ANSWER_TEXT)
        if self._auto_misses == AUTO_SLOW_AFTER:
            self.timer.setInterval(max(self._auto_ms, AUTO_PING_SLOW_MS))

    @Slot(str, object)
    def on_failed(self, name: str, exc: steps.StepFailed) -> None:
        self.pending -= 1
        cause, partial = exc.cause, exc.partial
        if name == "report":
            partial["error"] = steps.explain(cause)
            self.write_report(partial)
            return
        if self.closing:
            return
        # 이 단계에서 ping에 답했다면 연결은 된 것이다 (다음 작업이 늦거나 실패했을 뿐).
        answered = "ping" in partial
        message = steps.explain(cause, answered)
        if name == "auto" and not answered:
            if steps.is_disconnect(cause):
                # 아직 스크립트를 누르지 않은 것: 조용히 다음 확인을 기다린다
                self._auto_missed(cause)
            elif message != self._last_auto_error:
                # 다른 문제는 한 번만 알린다
                self._last_auto_error = message
                self.session.record("connect", False, message, partial, steps.error_info(cause))
                self.log(f"자동 연결 확인 중 문제: {message}")
            return
        self.session.record("connect" if name == "auto" else name, False, message, partial,
                            steps.error_info(cause, answered))
        if answered:
            self.set_connected(True)
        elif steps.is_disconnect(cause):
            self.set_connected(False)
        self.show_state(partial)
        self._finish(name, False, message)

    # ── 결과 파일 ──────────────────────────────────────────────────────

    def link_info(self) -> Dict[str, Any]:
        b = self.bridge
        prefs = getattr(b, "prefs_path", None)
        mailbox = getattr(b, "mailbox", None)
        return {
            "connected": self.connected,
            "mailbox": str(mailbox or ""),
            "mailbox_long": long_path(Path(mailbox)) if mailbox else None,
            "prefs_path": str(prefs) if prefs else None,
            "script_version": getattr(b, "script_version", None),
            "owner": getattr(b, "owner", None),
            "last_response": getattr(b, "last_response", None),
            "late_answers": list(getattr(b, "late_answers", None) or []),
            "auto_note": self._auto_note,
        }

    def report_text(self, env: Optional[Dict[str, Any]] = None) -> str:
        return build_report(self.session, self.link_info(), env or {})

    def write_report(self, env: Dict[str, Any]) -> None:
        self.report_pending = False
        self.update_buttons()
        text = self.report_text(env)
        folder = self.report_dir if self.report_dir is not None else desktop_dir()
        try:
            path = save_report(text, folder)
        except OSError:
            # 바탕 화면에 쓸 수 없으면 앱 기록 폴더에라도 남긴다.
            try:
                path = save_report(text, state_dir())
            except OSError as exc:
                self.show_message(f"결과를 저장하지 못했습니다: {exc}")
                return
        self.last_report = path
        self.log(f"결과 저장: {path}")
        self.show_message(f"결과를 저장했습니다. 이 파일을 보내 주세요.\n{path}")
        if self.interactive:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(path.parent)))

    # ── 닫기 ─────────────────────────────────────────────────────────

    def shutdown(self) -> None:
        """기다리던 요청을 바로 끝내고 작업 스레드를 멈춘다."""
        if self.closing:
            return
        self.closing = True
        self.timer.stop()
        try:
            self.bridge.close()
        except Exception:
            pass
        self.thread.quit()
        self.thread.wait(5000)
        self.update_buttons()

    def closeEvent(self, event) -> None:
        self.shutdown()
        event.accept()
