"""JobRunner: 오래 걸리는 계산과 넣기 (설계 B10).

한 번에 한 가지 일만 한다 (작업 스레드 하나). 창은 신호로 진행과 결과를 받기만 한다.
- 계산(plan)은 [멈추기]로 멈출 수 있다: JobContext.cancel(threading.Event)이 켜지면
  FFmpeg를 끝내고 리졸브 요청을 기다리지 않는다. 리졸브에는 아직 아무것도 하지 않았으니 묻지 않고 멈춘다.
- 넣기(apply)와 되돌리기(undo)는 멈추지 않는다 (반쯤 넣고 멈추면 더 헷갈리므로). 화면은 "취소할 수 없어요".

리졸브 요청은 브리지 안의 잠금으로 한 번에 하나씩 나간다 (연결 확인 줄과 겹쳐도 차례로).
"""

from __future__ import annotations

import threading
from typing import Any, Callable, Dict, Optional

from PySide6.QtCore import QObject, QThread, Signal, Slot


class JobContext:
    """일 하나에 넘기는 것: 멈춤 신호와 진행 알림."""

    def __init__(self, runner: "JobRunner", job_id: int, cancellable: bool) -> None:
        self.cancel = threading.Event()
        self.cancellable = cancellable
        self._runner = runner
        self._id = job_id

    def cancelled(self) -> bool:
        return self.cancel.is_set()

    def progress(self, stage: int, frac: float, info: Optional[Dict[str, Any]] = None) -> None:
        self._runner._progress.emit(self._id, int(stage), float(frac), dict(info or {}))


class _Worker(QObject):
    done = Signal(int, object)
    failed = Signal(int, object)

    @Slot(int, object, object)
    def run(self, job_id: int, fn, ctx) -> None:
        try:
            result = fn(ctx)
        except BaseException as exc:  # noqa: BLE001 - 창에 알린다 (작업 스레드를 죽이지 않는다)
            self.failed.emit(job_id, exc)
        else:
            self.done.emit(job_id, result)


class JobRunner(QObject):
    """start(이름, fn(ctx), 됐을 때, 실패했을 때, 진행) → 이미 하는 일이 있으면 False."""

    _run = Signal(int, object, object)
    _progress = Signal(int, int, float, object)
    changed = Signal()

    def __init__(self, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self.name: Optional[str] = None
        self.ctx: Optional[JobContext] = None
        self._id = 0
        self._callbacks: Dict[int, tuple] = {}
        self.thread = QThread(self)
        self.worker = _Worker()
        self.worker.moveToThread(self.thread)
        self._run.connect(self.worker.run)
        self.worker.done.connect(self._on_done)
        self.worker.failed.connect(self._on_failed)
        self._progress.connect(self._on_progress)
        self.thread.finished.connect(self.worker.deleteLater)
        self.thread.start()

    @property
    def busy(self) -> bool:
        return self.name is not None

    @property
    def cancellable(self) -> bool:
        return self.ctx is not None and self.ctx.cancellable

    def start(self, name: str, fn: Callable[[JobContext], Any], on_done: Callable[[Any], None],
              on_failed: Callable[[BaseException], None],
              on_progress: Optional[Callable[[int, float, Dict[str, Any]], None]] = None,
              cancellable: bool = True) -> bool:
        if self.busy:
            return False
        self._id += 1
        self.name = name
        self.ctx = JobContext(self, self._id, cancellable)
        self._callbacks[self._id] = (on_done, on_failed, on_progress)
        self.changed.emit()
        self._run.emit(self._id, fn, self.ctx)
        return True

    def cancel(self) -> bool:
        """[멈추기]: 멈출 수 있는 일이면 멈춤 신호를 켠다."""
        if self.ctx is not None and self.ctx.cancellable:
            self.ctx.cancel.set()
            return True
        return False

    def _finish(self, job_id: int):
        callbacks = self._callbacks.pop(job_id, (None, None, None))
        if job_id == self._id:
            self.name = None
            self.ctx = None
        return callbacks

    @Slot(int, object)
    def _on_done(self, job_id: int, result) -> None:
        on_done, _, _ = self._finish(job_id)
        try:
            if on_done is not None:
                on_done(result)
        finally:
            self.changed.emit()

    @Slot(int, object)
    def _on_failed(self, job_id: int, exc) -> None:
        _, on_failed, _ = self._finish(job_id)
        try:
            if on_failed is not None:
                on_failed(exc)
        finally:
            self.changed.emit()

    @Slot(int, int, float, object)
    def _on_progress(self, job_id: int, stage: int, frac: float, info) -> None:
        cb = self._callbacks.get(job_id)
        if cb and cb[2] is not None:
            cb[2](stage, frac, info)

    def shutdown(self) -> None:
        self.cancel()
        self.thread.quit()
        self.thread.wait(5000)
