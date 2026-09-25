"""작업 스레드 (설계 B10).

- TaskQueue: 리졸브에 보내는 일은 모두 작업 스레드 하나(BridgeWorker)에서 차례로 한다. 우체통은 한 칸이라
  두 요청이 동시에 나가면 안 된다. 창은 결과를 신호로 받기만 해서, 리졸브가 늦어도 멈추지 않는다.
- ShortTasks: 결과 저장처럼 파일만 읽는 짧은 일. 리졸브 줄을 기다리지 않는다 (작업 중에도 저장할 수 있게).
"""

from __future__ import annotations

import threading
from typing import Any, Callable, Dict, Optional, Tuple

from PySide6.QtCore import QObject, QThread, Signal, Slot

from . import steps


class BridgeWorker(QObject):
    """작업 스레드에서 단계를 하나씩 차례로 돌린다."""

    done = Signal(int, object)  # 일 번호, 리졸브의 답(사전)
    failed = Signal(int, object)  # 일 번호, steps.StepFailed

    def __init__(self, bridge) -> None:
        super().__init__()
        self.bridge = bridge

    @Slot(int, object)
    def run(self, task_id: int, step) -> None:
        try:
            out = steps.run_step(step, self.bridge)
        except steps.StepFailed as exc:
            self.failed.emit(task_id, exc)
        else:
            self.done.emit(task_id, out)


class TaskQueue(QObject):
    """리졸브에 보내는 일의 줄. submit(이름, 단계, 됐을 때, 실패했을 때)."""

    _run = Signal(int, object)
    changed = Signal()

    def __init__(self, bridge, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self.bridge = bridge
        self.pending = 0  # 작업 스레드에 보낸 일 (자동 확인 포함)
        self.names: Dict[int, str] = {}
        self._next = 0
        self._callbacks: Dict[int, Tuple[str, Callable, Callable]] = {}
        self.thread = QThread(self)
        self.worker = BridgeWorker(bridge)
        self.worker.moveToThread(self.thread)
        self._run.connect(self.worker.run)
        self.worker.done.connect(self._on_done)
        self.worker.failed.connect(self._on_failed)
        self.thread.finished.connect(self.worker.deleteLater)
        self.thread.start()

    @property
    def late_answers(self):
        return getattr(self.bridge, "late_answers", None)

    def submit(self, name: str, step, on_done: Callable[[Dict[str, Any]], None],
               on_failed: Callable[[steps.StepFailed], None]) -> int:
        self._next += 1
        task_id = self._next
        self._callbacks[task_id] = (name, on_done, on_failed)
        self.names[task_id] = name
        self.pending += 1
        self._run.emit(task_id, step)
        self.changed.emit()
        return task_id

    def _take(self, task_id: int):
        self.pending -= 1
        self.names.pop(task_id, None)
        return self._callbacks.pop(task_id, (None, None, None))

    @Slot(int, object)
    def _on_done(self, task_id: int, out) -> None:
        _, on_done, _ = self._take(task_id)
        try:
            if on_done is not None:
                on_done(out)
        finally:
            self.changed.emit()

    @Slot(int, object)
    def _on_failed(self, task_id: int, exc) -> None:
        _, _, on_failed = self._take(task_id)
        try:
            if on_failed is not None:
                on_failed(exc)
        finally:
            self.changed.emit()

    def shutdown(self) -> None:
        """기다리던 요청을 바로 끝내고 작업 스레드를 멈춘다."""
        try:
            self.bridge.close()
        except Exception:  # noqa: BLE001 - 닫기는 어떤 경우에도 끝까지
            pass
        self.thread.quit()
        self.thread.wait(5000)


class ShortTasks(QObject):
    """짧은 일을 따로 스레드에서 돌리고 결과는 창 스레드에서 받는다: callback(결과, 오류)."""

    _finished = Signal(object, object, object)

    def __init__(self, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self.running = 0
        self._finished.connect(self._deliver)

    def run(self, fn: Callable[[], Any], callback: Callable[[Any, Optional[BaseException]], None],
            name: str = "short-task") -> None:
        self.running += 1

        def work() -> None:
            try:
                result, error = fn(), None
            except Exception as exc:  # noqa: BLE001 - 창에 알린다
                result, error = None, exc
            self._finished.emit(callback, result, error)

        threading.Thread(target=work, name=name, daemon=True).start()

    @Slot(object, object, object)
    def _deliver(self, callback, result, error) -> None:
        self.running -= 1
        callback(result, error)


class Relay(QObject):
    """작업 스레드에서 창으로 보내는 중간 알림 (기능 점검 단계 등)."""

    stage = Signal(str, object)
