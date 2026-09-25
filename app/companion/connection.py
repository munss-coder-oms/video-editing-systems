"""ConnectionController: 리졸브와 이어져 있는지 알고, 언제 물어볼지 정한다 (설계 B7.5, S13 개정).

한가할 때는 리졸브에 아무것도 묻지 않는다. 리졸브에 요청이 갈 때마다 Lua가 Fusion 설정 파일을
다시 저장하는데, 리졸브를 끄는 중에 설정을 쓰다 리졸브가 꺼진 일이 있어서다.

- 연결 전: 2초마다 ping (스크립트를 누르기 전에는 아무도 답하지 않으니 리졸브에 쓰이는 것이 없다).
  첫 답을 받으면 멈춘다. 답을 못 읽는 것 같으면(UNREAD_LIMIT) 멈추고, 오래 이어지면 5초로 늦춘다.
- 연결 뒤: 리졸브에 묻지 않고 5초마다 윈도우 작업 목록에서 Resolve.exe만 본다. 없으면 "리졸브가 꺼졌어요".
- ping은 사용자가 무엇을 누를 때, 창을 다시 볼 때(마지막으로 물은 지 30초가 넘었을 때만), 작업 직전에만.
- 스크립트가 예전 판이면(ops 목록에 없음) "스크립트를 한 번 더 눌러 주세요".
"""

from __future__ import annotations

import functools
import sys
import threading
import time
from typing import Any, Callable, Dict, List, Optional

from PySide6.QtCore import QObject, QTimer, Signal, Slot

from engine.resolve_link.bridge import BridgeCancelled, BridgeError, BridgeTimeout, OldScript
from engine.resolve_link.ops import PROBE_PREFIX
from engine.resolve_link.process import resolve_running

from . import steps
from . import strings_ko as S

AUTO_PING_MS = 2000  # 연결 전에는 2초마다 리졸브에 물어본다
AUTO_PING_SLOW_MS = 5000  # 오래 연결되지 않으면 이 간격으로 늦춘다 (리졸브 설정 파일을 덜 건드리게)
AUTO_SLOW_AFTER = 30  # 답 없는 자동 확인이 이만큼(약 1분) 이어지면 늦춘다
UNREAD_LIMIT = 3  # Fusion.prefs는 바뀌었는데 답을 못 읽은 일이 이만큼 이어지면 자동 확인을 멈춘다
PROCESS_MS = 5000  # 연결 뒤 Resolve.exe가 있는지 보는 간격 (리졸브에는 묻지 않음)
FOCUS_DEBOUNCE_S = 30.0  # 창을 다시 볼 때 ping: 마지막으로 물은 지 이만큼 지났을 때만

NOT_CONNECTED = "not_connected"
CHECKING = "checking"
CONNECTED = "connected"
RESOLVE_QUIT = "resolve_quit"
OLD_SCRIPT = "old_script"
BUSY = "busy"
ANSWERED_STATES = (CONNECTED, OLD_SCRIPT, BUSY)


# ── 작업 스레드에서 도는 단계 (Qt 없음) ──────────────────────────────

def panel_connect_step(bridge, out: Dict[str, Any], ping_timeout: float = steps.PING_TIMEOUT,
                       extras: Optional[List[Callable]] = None) -> None:
    """연결 확인: ping → 타임라인 정보 (1.1.0이면 timeline_info, 예전 스크립트면 state).

    ping에 답했으면 연결은 된 것이다. 다음 읽기가 늦거나 실패해도 out에 이유만 남긴다.
    """
    out["ping"] = bridge.ping(timeout=ping_timeout)
    supports = getattr(bridge, "supports", None)
    known = supports("timeline_info") if callable(supports) else None
    # 2.1a 때 켠 스크립트(같은 1.1.0이지만 표시를 한꺼번에 넣는 add_markers가 없음)도 한 번 더 눌러 달라고 한다
    if known is False or (callable(supports) and supports("add_markers") is False):
        out["old_script"] = True
    try:
        if known:
            out["state"] = bridge.request("timeline_info")
            out["state_kind"] = "timeline_info"
        else:
            out["state"] = bridge.state()
            out["state_kind"] = "state"
    except OldScript:
        out["old_script"] = True
        try:
            out["state"] = bridge.state()
            out["state_kind"] = "state"
        except (BridgeError, BridgeTimeout) as exc:
            out["state_error"] = steps.error_info(exc, answered=True)
    except (BridgeError, BridgeTimeout) as exc:
        out["state_error"] = steps.error_info(exc, answered=True)
    _run_extras(extras, bridge, out)


def auto_step(bridge, out: Dict[str, Any]) -> None:
    """연결 전 2초마다: 짧게 기다리는 ping (+ 답하면 타임라인 정보)."""
    panel_connect_step(bridge, out, ping_timeout=steps.AUTO_PING_TIMEOUT)


# ── 시계와 타이머 (시험에서는 가짜로 바꾼다) ─────────────────────────

class QtScheduler:
    def __init__(self, parent: QObject) -> None:
        self.parent = parent

    def timer(self, callback: Callable[[], None], interval_ms: int) -> QTimer:
        t = QTimer(self.parent)
        t.setInterval(interval_ms)
        t.timeout.connect(callback)
        return t

    def single_shot(self, ms: int, callback: Callable[[], None]) -> None:
        QTimer.singleShot(ms, callback)


class _AsyncRunner(QObject):
    """짧은 확인(tasklist)을 따로 스레드에서 돌리고 결과는 창 스레드에서 받는다."""

    finished = Signal(object, object)

    def __init__(self, parent: QObject) -> None:
        super().__init__(parent)
        self.finished.connect(self._deliver)

    def run(self, fn: Callable[[], Any], callback: Callable[[Any], None]) -> None:
        def work() -> None:
            try:
                result = fn()
            except Exception:  # noqa: BLE001 - 확인을 못 하면 "모름"
                result = None
            self.finished.emit(callback, result)

        threading.Thread(target=work, name="resolve-process-check", daemon=True).start()

    @Slot(object, object)
    def _deliver(self, callback, result) -> None:
        callback(result)


def default_process_check() -> Optional[Callable[[], Optional[bool]]]:
    return resolve_running if sys.platform == "win32" else None


class ConnectionController(QObject):
    """연결 상태와 자동 확인. 요청은 queue(작업 스레드 한 칸)로만 보낸다."""

    changed = Signal()
    answered = Signal(str, object)  # 단계 이름, out
    failed = Signal(str, object)  # 단계 이름, steps.StepFailed

    def __init__(
        self,
        queue,
        *,
        session=None,
        scheduler=None,
        clock: Callable[[], float] = time.monotonic,
        process_check: Any = "default",
        run_async: Optional[Callable[[Callable[[], Any], Callable[[Any], None]], None]] = None,
        auto_ping_ms: int = AUTO_PING_MS,
        process_ms: int = PROCESS_MS,
        focus_debounce: float = FOCUS_DEBOUNCE_S,
        extras: Optional[Callable[[], List[Callable]]] = None,
        parent: Optional[QObject] = None,
    ) -> None:
        super().__init__(parent)
        self.queue = queue
        self.session = session
        self.scheduler = scheduler if scheduler is not None else QtScheduler(self)
        self.clock = clock
        self.process_check = default_process_check() if process_check == "default" else process_check
        if run_async is None:
            runner = _AsyncRunner(self)
            run_async = runner.run
        self.run_async = run_async
        self.focus_debounce = focus_debounce
        self.extras = extras  # 연결할 때 같이 할 읽기 (기능 점검 기록, 일지 맞춰 보기 ...)

        self.status = NOT_CONNECTED
        self.closing = False
        self.info: Dict[str, Any] = {}  # 마지막으로 받은 ping + 타임라인 정보
        self.last_contact: Optional[float] = None
        self.checking = False
        self.auto_attempts = 0
        self.focus_pings = 0
        self.process_checks = 0
        self._auto_ms = auto_ping_ms
        self._auto_misses = 0
        self._unread = 0
        self.auto_stopped = False
        # 자동화 일(JobRunner)이 리졸브 줄을 쓰는 중인지 (창이 넣어 준다). 그동안은 연결 전 자동 확인도 쉰다
        self.job_busy: Callable[[], bool] = lambda: False
        self.auto_step: Callable = auto_step
        self.auto_note = ""
        self._last_auto_error = ""
        self.timer = self.scheduler.timer(self.auto_ping, auto_ping_ms)
        self.process_timer = self.scheduler.timer(self._process_tick, process_ms)

    # ── 상태 ─────────────────────────────────────────────────────────

    @property
    def connected(self) -> bool:
        return self.status in ANSWERED_STATES

    @property
    def usable(self) -> bool:
        """자동화 버튼을 쓸 수 있는 상태: 새 스크립트와 연결됐고 점검용 복사본이 아님."""
        return self.status == CONNECTED and not self.probe_copy_name

    @property
    def timeline(self) -> Dict[str, Any]:
        state = self.info.get("state")
        return state if isinstance(state, dict) else {}

    @property
    def probe_copy_name(self) -> Optional[str]:
        name = self.timeline.get("timeline")
        return name if isinstance(name, str) and name.startswith(PROBE_PREFIX) else None

    def _set_status(self, status: str) -> None:
        self.status = status
        if status == CONNECTED or status == OLD_SCRIPT or status == BUSY:
            # 다음에 끊기면 처음처럼 2초마다 확인한다
            self._auto_misses = self._unread = 0
            self.auto_stopped = False
            self.timer.setInterval(self._auto_ms)
        self._sync_timers()
        self.changed.emit()

    def _sync_timers(self) -> None:
        want_auto = self.status == NOT_CONNECTED and not self.closing and not self.auto_stopped
        if want_auto and not self.timer.isActive():
            self.timer.start()
        elif not want_auto:
            self.timer.stop()  # 연결된 뒤에는 누를 때만 확인한다
        want_process = (self.process_check is not None and not self.closing
                        and self.status in ANSWERED_STATES + (RESOLVE_QUIT,))
        if want_process and not self.process_timer.isActive():
            self.process_timer.start()
        elif not want_process:
            self.process_timer.stop()

    def start(self) -> None:
        self._sync_timers()
        self.changed.emit()
        self.scheduler.single_shot(0, self.auto_ping)

    def shutdown(self) -> None:
        self.closing = True
        self.timer.stop()
        self.process_timer.stop()

    # ── 보내기 ───────────────────────────────────────────────────────

    def _extras(self) -> List[Callable]:
        return list(self.extras()) if self.extras is not None else []

    def _submit(self, name: str, step: Callable, on_done, on_failed) -> None:
        self.last_contact = self.clock()
        self.queue.submit(name, step, on_done, on_failed)

    def submit(self, name: str, step: Callable, on_done, on_failed) -> None:
        """창의 다른 작업(시험 도구, 기능 점검)도 같은 줄로 보낸다. 마지막으로 물은 시각을 적는다."""
        self._submit(name, step, on_done, on_failed)

    @Slot()
    def auto_ping(self) -> None:
        # 이미 무언가 기다리는 중이면 건너뛴다 (우체통은 한 칸이라 쌓아 둘 필요가 없다).
        if (self.status != NOT_CONNECTED or self.closing or self.queue.pending or self.auto_stopped
                or self.job_busy()):
            return
        self.auto_attempts += 1
        if self.session is not None:
            self.session.auto_attempts += 1
        step = functools.partial(_with_extras, self.auto_step, self._extras())
        self._submit("auto", step, self._on_auto_done, self._on_auto_failed)

    def check_now(self, name: str = "connect", then: Optional[Callable[[Optional[Dict[str, Any]]], None]] = None) -> bool:
        """[연결 확인], 버튼을 누른 직후, 작업 직전: ping + 타임라인 정보. then(out 또는 None)."""
        if self.closing:
            return False
        self.checking = True
        self.changed.emit()
        step = functools.partial(panel_connect_step, extras=self._extras())

        def done(out):
            self.checking = False
            self._answer(name, out)
            if then is not None:
                then(out)

        def failed(exc):
            self.checking = False
            self._failure(name, exc)
            if then is not None:
                then(None)

        self._submit(name, step, done, failed)
        return True

    def before_action(self, name: str, then: Callable[[Optional[Dict[str, Any]]], None]) -> bool:
        """사용자 동작 전에 먼저 ping (연결됐는지, 스크립트 판, 점검용 복사본인지)."""
        return self.check_now(name, then)

    def on_focus_in(self) -> bool:
        """창을 다시 볼 때: 연결돼 있고 마지막으로 물은 지 30초가 넘었을 때만 한 번."""
        if self.closing or self.status not in ANSWERED_STATES or self.queue.pending:
            return False
        if self.last_contact is not None and self.clock() - self.last_contact < self.focus_debounce:
            return False
        self.focus_pings += 1
        return self.check_now("focus")

    # ── 받기 ─────────────────────────────────────────────────────────

    def _answer(self, name: str, out: Dict[str, Any]) -> None:
        self.last_contact = self.clock()
        self.note_answer(out)
        self.answered.emit(name, out)

    def _failure(self, name: str, exc) -> None:
        self.last_contact = self.clock()
        answered = "ping" in exc.partial
        self.note_failure(exc.cause, answered, exc.partial)
        self.failed.emit(name, exc)

    def note_answer(self, out: Dict[str, Any]) -> None:
        """ping에 답이 온 모든 단계 (시험 도구 포함). 받은 정보로 상태를 고친다."""
        if self.closing:
            return
        keep = dict(self.info)
        for key in ("ping", "state", "state_kind", "old_script", "state_error"):
            if key in out:
                keep[key] = out[key]
            elif key in ("old_script", "state_error") and "ping" in out:
                keep.pop(key, None)
        self.info = keep
        if out.get("old_script"):
            self._set_status(OLD_SCRIPT)
        elif _busy(out):
            self._set_status(BUSY)
        else:
            self._set_status(CONNECTED)

    def note_failure(self, cause: BaseException, answered: bool, partial: Optional[Dict[str, Any]] = None) -> None:
        if self.closing:
            return
        if answered:
            self.note_answer(partial or {})
        elif isinstance(cause, OldScript):
            self._set_status(OLD_SCRIPT)
        elif steps.is_disconnect(cause):
            self._set_status(NOT_CONNECTED)
        else:
            self.changed.emit()

    def _on_auto_done(self, out: Dict[str, Any]) -> None:
        self._answer("auto", out)

    def _on_auto_failed(self, exc) -> None:
        cause, partial = exc.cause, exc.partial
        if self.closing:
            return
        answered = "ping" in partial
        if answered:
            self._failure("auto", exc)
            return
        message = steps.explain(cause, answered)
        if steps.is_disconnect(cause):
            self._auto_missed(cause)
        elif message != self._last_auto_error:
            # 다른 문제는 한 번만 알린다
            self._last_auto_error = message
            self.failed.emit("auto", exc)

    def _auto_missed(self, cause) -> None:
        """자동 확인에 답이 없었다. 답을 못 읽는 것 같으면 멈추고, 오래 이어지면 간격을 늘린다."""
        self._auto_misses += 1
        # Fusion.prefs가 바뀌었는데 우리 답을 못 찾음: 스크립트는 답을 쓰는데 이 창이 못 읽는 것일 수 있다.
        self._unread = self._unread + 1 if getattr(cause, "prefs_changed", None) else 0
        if self._unread >= UNREAD_LIMIT:
            self.auto_stopped = True
            self.timer.stop()
            self.auto_note = S.AUTO_NOTE_UNREAD
            self.failed.emit("auto_stopped", steps.StepFailed(cause, {}))
            self.changed.emit()
            return
        bridge_late = getattr(self.queue, "late_answers", None)
        if bridge_late and self.auto_step is auto_step:
            # 스크립트는 도는데 답이 1.5초보다 늦게 온다: 버튼을 누를 때처럼 더 오래 기다린다
            self.auto_step = panel_connect_step
            self.auto_note = S.SLOW_ANSWER_TEXT
            self.failed.emit("auto_slow", steps.StepFailed(cause, {}))
        if self._auto_misses == AUTO_SLOW_AFTER:
            self.timer.setInterval(max(self._auto_ms, AUTO_PING_SLOW_MS))

    # ── Resolve.exe 보기 (리졸브에 묻지 않음) ─────────────────────────

    def _process_tick(self) -> None:
        if self.process_check is None or self.closing:
            return
        self.process_checks += 1
        self.run_async(self.process_check, self._process_result)

    def _process_result(self, running: Optional[bool]) -> None:
        if self.closing or running is None:
            return
        if running is False and self.status != RESOLVE_QUIT:
            self._set_status(RESOLVE_QUIT)
        elif running is True and self.status == RESOLVE_QUIT:
            # 리졸브가 다시 켜졌다: 연결 전처럼 2초마다 물어본다 (스크립트를 다시 눌러야 답한다)
            self.info = {}
            self._set_status(NOT_CONNECTED)
            self.scheduler.single_shot(0, self.auto_ping)


def _run_extras(extras: Optional[List[Callable]], bridge, out: Dict[str, Any]) -> None:
    """연결 확인에 붙여 하는 읽기. 하나가 실패해도 연결 확인은 된 것이다 (이유만 남긴다)."""
    for extra in extras or []:
        try:
            extra(bridge, out)
        except BridgeCancelled:
            raise
        except Exception as exc:  # noqa: BLE001 - 덧붙인 읽기의 실패로 연결 확인을 버리지 않는다
            out.setdefault("extra_errors", []).append(steps.error_info(exc, answered=True))


def _with_extras(step: Callable, extras: List[Callable], bridge, out: Dict[str, Any]) -> None:
    step(bridge, out)
    _run_extras(extras, bridge, out)


# ── 연결할 때 같이 하는 읽기 (작업 스레드). 필요할 때만 리졸브에 묻는다 ─────────

def _timeline_state(out: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    state = out.get("state")
    if out.get("state_kind") != "timeline_info" or not isinstance(state, dict) or state.get("timeline") is None:
        return None
    return state


def extra_audio_items(bridge, out: Dict[str, Any], known: Optional[str] = None) -> None:
    """타임라인이 바뀌었을 때만: 소리 클립 첫 쪽(100개까지). 결과 파일의 스트림 정보(ffprobe)에 쓴다."""
    state = _timeline_state(out)
    if state is None:
        return
    ident = state.get("timeline_uid") or f"name:{state.get('timeline')}"
    if ident == known:
        return
    r = bridge.request("timeline_items", {"kind": "audio", "offset": 0, "limit": 100})
    items = r.get("items") if isinstance(r.get("items"), list) else []
    out["audio_items"] = {"ident": ident, "items": [i for i in items if isinstance(i, dict)],
                          "next": r.get("next"), "track_count": r.get("track_count")}


def extra_probe_read(bridge, out: Dict[str, Any], store=None) -> None:
    """이 리졸브 판(또는 스크립트 판)에서 처음이면: 읽기만 하는 기능 점검(probe_read)을 한 번."""
    supports = getattr(bridge, "supports", None)
    ping = out.get("ping") if isinstance(out.get("ping"), dict) else {}
    if store is None or not callable(supports) or not supports("probe_read") or "state_error" in out:
        return  # 타임라인 읽기가 늦었으면 (대화 상자 등) 더 묻지 않는다
    product = ping.get("product")
    version = ping.get("product_version") or ping.get("resolve_version")
    script_version = ping.get("script_version")
    caps = store.load(product, version)
    if caps.needs_probe_read(script_version):
        result = bridge.request("probe_read")
        out["probe_read"] = result
        caps = store.record_read(product, version, script_version, result)
    out["caps"] = caps


def extra_reconcile(bridge, out: Dict[str, Any], root=None) -> None:
    """일지에 "넣는 중"으로 남은 것이 있을 때만: 표시를 읽어 들어갔는지 맞춰 본다 (설계 B6.2)."""
    from engine.edits.journal import Journal
    from engine.resolve_link.ops import TimelineInfo

    state = _timeline_state(out)
    if state is None:
        return
    journal = Journal.for_timeline(TimelineInfo.from_result(state), root)
    if not journal.pending():
        return
    listing = bridge.get_markers()
    markers = listing.get("markers") if isinstance(listing, dict) else None
    results = journal.reconcile(markers if isinstance(markers, list) else [])
    out["reconciled"] = [r.__dict__ for r in results]


def _busy(out: Dict[str, Any]) -> bool:
    """ping에는 답했는데 읽기가 (한 번 더 보내도) 늦음: 리졸브에 대화 상자가 열려 있을 수 있다."""
    err = out.get("state_error")
    return isinstance(err, dict) and err.get("type") == "BridgeTimeout"
