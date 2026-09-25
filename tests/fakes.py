"""시험용 가짜: 1.1.0 스크립트처럼 답하는 브리지, 시간을 손으로 돌리는 타이머, 바로 도는 작업 줄.

test_connection.py(연결 상태)와 test_panel.py(창)가 쓴다. 리졸브도 Qt 타이머도 없이 몇 분을 흉내 낸다.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from app.companion import steps
from engine.resolve_link import SCRIPT_VERSION
from engine.resolve_link.bridge import BridgeCancelled, BridgeTimeout, OldScript
from engine.resolve_link.protocol import LEGACY_OPS, OPS


def timeline_info(**changes) -> Dict[str, Any]:
    info = {
        "page": "edit", "project": "시험 프로젝트", "project_uid": "proj-1", "timeline_count": 1,
        "timeline": "Timeline 1", "timeline_uid": "tl-1", "start_frame": 216000, "end_frame": 216000 + 67570,
        "start_tc": "01:00:00:00", "current_tc": "01:00:10:00", "fps": "60", "drop_frame": False,
        "tracks": {
            "video": [{"index": 1, "name": "Video 1", "enabled": True, "locked": False, "count": 1}],
            "audio": [{"index": i, "name": f"Audio {i}", "enabled": True, "locked": False, "subtype": "stereo",
                       "count": 1} for i in range(1, 5)],
            "subtitle": [],
        },
        "is_probe_copy": False, "probe": None, "calls": {},
    }
    info.update(changes)
    return info


class FakeLuaBridge:
    """LuaBridge의 request() 흉내. 요청마다 op 이름을 requests에 남긴다 (한가할 때 0개인지 세려고).

    online=False: 리졸브가 답하지 않음 (BridgeTimeout). old_script=True: 1.0.0 스크립트 (ops 목록 없음).
    busy_ops: 이 op들은 ping에는 답해도 늦는다. handlers: op별로 답을 바꾼다.
    """

    def __init__(self, tmp_path: Optional[Path] = None) -> None:
        base = Path(tmp_path) if tmp_path is not None else Path(".")
        self.mailbox = base / "bridge"
        self.prefs = base / "Fusion.prefs"
        self.online = True
        self.old_script = False
        self.busy_ops: set = set()
        self.prefs_changed: list = []
        self.requests: List[str] = []
        self.args: List[Any] = []
        self.known_ops: Optional[frozenset] = None
        self.info = timeline_info()
        self.items: List[Dict[str, Any]] = [
            {"track": 1, "kind": "audio", "uid": "item-1", "name": "녹화.mp4", "start": 216000, "end": 283570,
             "path": "D:\\녹화\\녹화.mp4", "enabled": True},
        ]
        self.markers: List[Dict[str, Any]] = []
        self.handlers: Dict[str, Callable[[Dict[str, Any]], Dict[str, Any]]] = {}
        self.late_answers: list = []
        self.closed = False
        self.delay = 0.0
        self.prefs_path = None
        self.owner = None
        self.script_version = None
        self.last_response = None
        self.request_count = 0
        self.read_retries = 0
        self.cleanup_pings = 0
        self._lock = threading.Lock()

    # LuaBridge와 같은 이름
    def supports(self, op: str) -> Optional[bool]:
        return None if self.known_ops is None else op in self.known_ops

    def ping(self, timeout=None):
        return self.request("ping", timeout=timeout)

    def state(self, timeout=None):
        return self.request("state", timeout=timeout)

    def get_markers(self, timeout=None):
        return self.request("get_markers", timeout=timeout)

    def prefs_candidates(self):
        return [self.prefs]

    def backup_dir(self):
        return self.mailbox.parent / "backup"

    def close(self) -> None:
        self.closed = True

    def request(self, op: str, args=None, timeout=None, cancel=None, retry=None) -> Dict[str, Any]:
        with self._lock:
            self.requests.append(op)
            self.args.append(args)
            self.request_count += 1
        if self.delay:
            threading.Event().wait(self.delay)
        if self.closed:
            raise BridgeCancelled(op)
        if not self.online:
            raise BridgeTimeout(op, prefs_changed=self.prefs_changed)
        if op in self.busy_ops:
            raise BridgeTimeout(op)
        if op not in ("ping", "stop") and self.known_ops is not None and op not in self.known_ops:
            raise OldScript(op)
        self.owner = "o1759000000_7"
        self.prefs_path = self.prefs
        if op in self.handlers:
            return self.handlers[op](args or {})
        if op == "ping":
            if self.old_script:
                self.known_ops, self.script_version = frozenset(LEGACY_OPS), "1.0.0"
                return {"script_version": "1.0.0", "owner": self.owner, "product": "DaVinci Resolve",
                        "resolve_version": "21.1.0.0", "calls": {}}
            self.known_ops, self.script_version = frozenset(OPS), SCRIPT_VERSION
            return {"script_version": SCRIPT_VERSION, "owner": self.owner, "product": "DaVinci Resolve",
                    "resolve_version": "21.1.0.0", "product_version": "21.1.0.0", "page": "edit",
                    "ops": sorted(OPS), "calls": {}}
        if op == "timeline_info":
            return dict(self.info)
        if op == "state":
            state = {k: v for k, v in self.info.items() if k != "tracks"}
            state["tracks"] = {k: len(v) for k, v in self.info["tracks"].items()}
            state["items"] = {"video": [], "audio": list(self.items)}
            return state
        if op == "timeline_items":
            return {"kind": "audio", "items": list(self.items), "offset": 0, "next": None, "calls": {}}
        if op == "probe_read":
            return {"exists": {"Timeline.GetMarkInOut": True}, "existence_reliable": True,
                    "project_uid": "proj-1", "timeline_uid": "tl-1", "calls": {}}
        if op == "get_markers":
            return {"markers": list(self.markers), "calls": {}}
        return {"calls": {}}


class FakeTimer:
    def __init__(self, sched: "FakeScheduler", callback: Callable[[], None], interval_ms: int) -> None:
        self.sched = sched
        self.callback = callback
        self._interval = interval_ms
        self.active = False
        self.due = 0

    def setInterval(self, ms: int) -> None:
        self._interval = ms
        if self.active:
            self.due = self.sched.now_ms + ms

    def interval(self) -> int:
        return self._interval

    def start(self) -> None:
        self.active = True
        self.due = self.sched.now_ms + self._interval

    def stop(self) -> None:
        self.active = False

    def isActive(self) -> bool:
        return self.active


class FakeScheduler:
    """QTimer 대신: advance(ms)로 시간을 돌린다. clock()은 같은 시간(초)."""

    def __init__(self) -> None:
        self.now_ms = 0
        self.timers: List[FakeTimer] = []
        self.shots: List[tuple] = []

    def clock(self) -> float:
        return self.now_ms / 1000.0

    def timer(self, callback: Callable[[], None], interval_ms: int) -> FakeTimer:
        t = FakeTimer(self, callback, interval_ms)
        self.timers.append(t)
        return t

    def single_shot(self, ms: int, callback: Callable[[], None]) -> None:
        self.shots.append((self.now_ms + ms, callback))

    def advance(self, ms: int) -> None:
        end = self.now_ms + ms
        while True:
            dues = [t.due for t in self.timers if t.active] + [s[0] for s in self.shots]
            nxt = min(dues, default=None)
            if nxt is None or nxt > end:
                self.now_ms = end
                return
            self.now_ms = max(self.now_ms, nxt)
            ready = [s for s in self.shots if s[0] <= self.now_ms]
            self.shots = [s for s in self.shots if s[0] > self.now_ms]
            for _, cb in ready:
                cb()
            for t in list(self.timers):
                if t.active and t.due <= self.now_ms:
                    t.due = self.now_ms + max(1, t.interval())
                    t.callback()


class SyncQueue:
    """TaskQueue 대신: 보내자마자 같은 스레드에서 돌린다."""

    def __init__(self, bridge) -> None:
        self.bridge = bridge
        self.pending = 0
        self.names: List[str] = []

    @property
    def late_answers(self):
        return getattr(self.bridge, "late_answers", None)

    def submit(self, name: str, step, on_done, on_failed) -> None:
        self.names.append(name)
        self.pending += 1
        try:
            out = steps.run_step(step, self.bridge)
        except steps.StepFailed as exc:
            self.pending -= 1
            on_failed(exc)
        else:
            self.pending -= 1
            on_done(out)
