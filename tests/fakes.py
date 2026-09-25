"""시험용 가짜: 1.1.0 스크립트처럼 답하는 브리지, 시간을 손으로 돌리는 타이머, 바로 도는 작업 줄.

test_connection.py(연결 상태)와 test_panel.py(창)가 쓴다. 리졸브도 Qt 타이머도 없이 몇 분을 흉내 낸다.
"""

from __future__ import annotations

import re
import threading
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional

from app.companion import steps
from engine.resolve_link import SCRIPT_VERSION
from engine.resolve_link.bridge import BridgeCancelled, BridgeError, BridgeTimeout, OldScript
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


MARKER_COLORS = {"Blue", "Cyan", "Green", "Yellow", "Red", "Pink", "Purple", "Fuchsia", "Rose", "Lavender",
                 "Sky", "Mint", "Lemon", "Sand", "Cocoa", "Cream"}
TAG_RE = re.compile(r"^aih:[A-Za-z0-9:_\-]+$")
PREFIX_RE = re.compile(r"^aih:[A-Za-z0-9:_\-]*$")
RESOLVE_OPS = ("timeline_info", "timeline_items", "get_markers", "add_markers", "delete_markers", "remove_audio",
               "probe_read", "scope", "jump_to")
PLAYHEAD_PAGES = ("cut", "edit", "color", "fairlight", "deliver")


def audio_item(uid: str, track: int, start: int, length: int, path: str, *, src: int = 0, clip_fps: str = "60",
               enabled: bool = True, speed: float = 1.0, left: Optional[int] = None) -> Dict[str, Any]:
    """timeline_items 답 한 줄 (Lua item_row와 같은 모양)."""
    return {"uid": uid, "track": track, "kind": "audio", "name": Path(path).name, "start": start,
            "end": start + length, "duration": length, "left_offset": src if left is None else left,
            "source_start": src, "source_end": src + int(round(length * speed)), "enabled": enabled, "path": path,
            "clip_fps": clip_fps, "media_uid": "mpi-" + Path(path).stem, "linked_uids": []}


def obs_items(path: str, start: int, length: int, *, streams: int = 4, src: int = 0, clip_fps: str = "60",
              prefix: str = "a") -> List[Dict[str, Any]]:
    """OBS 녹화 한 개를 A1..A4에 (서로 이어진 소리 클립)."""
    rows = [audio_item(f"{prefix}{i}", i, start, length, path, src=src, clip_fps=clip_fps)
            for i in range(1, streams + 1)]
    for r in rows:
        r["linked_uids"] = [o["uid"] for o in rows if o is not r]
    return rows


class FakeResolve:
    """Lua 1.1.0 스크립트와 같은 규칙으로 답하는 가짜 리졸브 (표시·트랙). Transport 모양.

    markers: {타임라인 시작부터 센 프레임: {color, name, note, duration, custom}}.
    no_range_markers=True: 길이 있는 표시를 거절하는 판 흉내.
    """

    name = "fake"

    def __init__(self, info: Optional[Dict[str, Any]] = None, items: Optional[List[Dict[str, Any]]] = None) -> None:
        self.info = info if info is not None else timeline_info()
        self.items: List[Dict[str, Any]] = list(items or [])
        self.markers: Dict[int, Dict[str, Any]] = {}
        self.no_range_markers = False
        self.probe_read_result: Dict[str, Any] = {"exists": {}, "existence_reliable": True,
                                                  "source_audio_mapping": [], "calls": {}}
        self.in_out: Optional[Dict[str, Any]] = None
        self.requests: List[str] = []
        self.args: List[Any] = []
        self.mutations: List[tuple] = []
        self.timeout_once: set = set()  # 이 op는 한 번 (넣은 뒤) 답이 늦다
        self.legacy_track_items_ours = True
        self.opened_pages: List[str] = []
        self.jumps: List[tuple] = []  # jump_to로 옮긴 재생 위치 (프레임, 타임코드)

    # Transport
    def supports(self, op: str) -> Optional[bool]:
        return True

    @property
    def length(self) -> int:
        return self.info["end_frame"] - self.info["start_frame"]

    def add_user_marker(self, frame: int, custom: str = "", color: str = "Green", name: str = "내 표시",
                        duration: int = 1) -> None:
        self.markers[frame] = {"color": color, "name": name, "note": "", "duration": duration, "custom": custom}

    def request(self, op: str, args=None, *, timeout=None, cancel=None) -> Dict[str, Any]:
        args = dict(args or {})
        self.requests.append(op)
        self.args.append(args)
        if cancel is not None and cancel.is_set():
            raise BridgeCancelled(op)
        fn = getattr(self, "_op_" + op, None)
        if fn is None:
            return {"calls": {}}
        result = fn(args)
        if op in self.timeout_once:
            self.timeout_once.discard(op)
            raise BridgeTimeout(op)
        return result

    def _fail(self, op: str, error: str, func: str):
        raise BridgeError(error, func, op, payload={"ok": False, "error": error, "func": func})

    def _op_ping(self, a):
        return {"script_version": "1.1.0", "ops": [], "page": self.info.get("page"), "calls": {}}

    def _op_timeline_info(self, a):
        return dict(self.info)

    def _op_timeline_items(self, a):
        offset = int(a.get("offset") or 0)
        limit = int(a.get("limit") or 100)
        rows = [dict(r) for r in self.items]
        page = rows[offset:offset + limit]
        nxt = offset + limit if offset + limit < len(rows) else None
        return {"kind": "audio", "items": page, "offset": offset, "next": nxt, "calls": {}}

    def _op_probe_read(self, a):
        return dict(self.probe_read_result)

    def _op_scope(self, a):
        return {"page": self.info.get("page"), "in_out": self.in_out, "calls": {}}

    def _match(self, m, prefix, colors=None):
        c = m.get("custom")
        return isinstance(c, str) and c.startswith(prefix) and (colors is None or m.get("color") in colors)

    def _op_get_markers(self, a):
        prefix = a.get("prefix")
        limit = a.get("limit", 2000)
        frames = sorted(f for f, m in self.markers.items() if prefix is None or self._match(m, prefix))
        rows = [{"frame": f, **{k: self.markers[f][k] for k in ("color", "name", "note", "duration")},
                 "custom": self.markers[f]["custom"]} for f in frames[:limit]]
        return {"markers": rows, "total": len(frames), "calls": {}}

    def _op_add_markers(self, a):
        rows = a.get("markers")
        if not isinstance(rows, list) or not rows or len(rows) > 100:
            self._fail("add_markers", "bad_args", "markers")
        seen = set()
        for m in rows:
            if m.get("color") not in MARKER_COLORS:
                self._fail("add_markers", "bad_args", "color")
            c = m.get("custom")
            if not isinstance(c, str) or not TAG_RE.match(c) or c in seen:
                self._fail("add_markers", "bad_args", "custom")
            seen.add(c)
            if m["frame"] < 0 or m.get("dur", 1) < 1 or m["frame"] + m.get("dur", 1) > self.length:
                self._fail("add_markers", "bad_args", "frame")
        have = {m.get("custom") for m in self.markers.values()}
        placed, failed, skipped = [], [], []
        point = a.get("point_only") is True
        for i, m in enumerate(rows, start=1):
            if m["custom"] in have:
                skipped.append(i)
                continue
            done = False
            for k in range(6):
                f = m["frame"] + k
                dur = 1 if point else max(1, m.get("dur", 1) - k)
                if f + dur > self.length:
                    break
                if f in self.markers:
                    continue
                if dur > 1 and self.no_range_markers:
                    point, dur = True, 1
                self.markers[f] = {"color": m["color"], "name": m.get("name", ""), "note": m.get("note", ""),
                                   "duration": dur, "custom": m["custom"]}
                self.mutations.append(("AddMarker", f, m["custom"]))
                have.add(m["custom"])
                placed.append({"i": i, "frame": f, "dur": dur, "custom": m["custom"], "shifted": k, "found": True,
                               "dur_readback": dur})
                done = True
                break
            if not done:
                failed.append({"i": i, "err": "taken"})
        return {"placed": placed, "failed": failed, "skipped_existing": skipped, "point_fallback": point,
                "requested": len(rows), "length": self.length, "calls": {"Timeline.AddMarker": "ok"}}

    def _op_delete_markers(self, a):
        snapshot = []
        if "prefix" in a:
            prefix = a["prefix"]
            if "custom" in a or not isinstance(prefix, str) or not PREFIX_RE.match(prefix):
                self._fail("delete_markers", "bad_args", "prefix")
            colors = set(a["colors"]) if a.get("colors") is not None else None
            wanted = a.get("customs")
            if wanted is not None:
                if not isinstance(wanted, list) or not wanted or len(wanted) > 200 or any(
                        not isinstance(c, str) or not TAG_RE.match(c) or not c.startswith(prefix) for c in wanted):
                    self._fail("delete_markers", "bad_args", "customs")
                wanted = set(wanted)
            frames = [f for f, m in sorted(self.markers.items())
                      if self._match(m, prefix, colors) and (wanted is None or m.get("custom") in wanted)]
            if a.get("snapshot") is True:
                snapshot = [{"frame": f, **{k: self.markers[f][k] for k in ("color", "name", "note", "duration")},
                             "custom": self.markers[f]["custom"]} for f in frames]
        else:
            custom = a.get("custom")
            if not (isinstance(custom, str) and (custom.startswith("aih:") or custom == "aih_test")):
                self._fail("delete_markers", "bad_args", "custom")
            frames = [f for f, m in sorted(self.markers.items()) if m.get("custom") == custom]
        for f in frames:
            self.mutations.append(("DeleteMarkerAtFrame", f, self.markers[f].get("custom")))
            del self.markers[f]
        ours = sum(1 for m in self.markers.values()
                   if isinstance(m.get("custom"), str) and (m["custom"].startswith("aih:") or m["custom"] == "aih_test"))
        out = {"deleted": bool(frames), "deleted_count": len(frames), "matched": len(frames), "remaining": 0,
               "remaining_ours": ours, "calls": {}}
        if a.get("snapshot") is True and "prefix" in a:
            out["snapshot"], out["snapshot_truncated"] = snapshot, False
        return out

    def _op_jump_to(self, a):
        """Lua ops.jump_to와 같은 규칙: 화면 확인, 타임라인 안인지, 타임코드 모양."""
        frame, tc = a.get("frame"), a.get("tc")
        if not isinstance(frame, int) or isinstance(frame, bool) or frame < 0:
            self._fail("jump_to", "bad_args", "frame")
        if not isinstance(tc, str) or not re.match(r"^\d\d:\d\d:\d\d[:;]\d\d$", tc):
            self._fail("jump_to", "bad_args", "tc")
        page = self.info.get("page")
        if page is not None and page not in PLAYHEAD_PAGES:
            return {"ok": False, "reason": "page", "page": page, "calls": {}}
        if not (self.info["start_frame"] <= frame < self.info["end_frame"]):
            return {"ok": False, "reason": "outside", "start_frame": self.info["start_frame"],
                    "end_frame": self.info["end_frame"], "calls": {}}
        self.info["current_tc"] = tc
        self.jumps.append((frame, tc))
        return {"ok": True, "set_result": True, "requested_tc": tc, "readback_tc": tc, "frame": frame, "page": page,
                "calls": {"Timeline.SetCurrentTimecode": "ok"}}

    def _op_remove_audio(self, a):
        name = a.get("track_name")
        page = self.info.get("page")
        switched = False
        if page != "edit":
            if a.get("switch_page") is not True:
                self._fail("remove_audio", "need_edit_page", "Resolve.GetCurrentPage")
            self.opened_pages += ["edit", page]
            switched = True
        tracks = self.info["tracks"]["audio"]
        clips, results, removed, skipped = [], [], 0, 0
        for t in reversed(list(tracks)):
            if t.get("name") != name:
                continue
            if not self.legacy_track_items_ours:
                skipped += 1
                continue
            idx = t["index"]
            n = sum(1 for it in self.items if it["track"] == idx)
            if n:
                clips.append({"track": idx, "count": n, "result": True})
                self.items = [it for it in self.items if it["track"] != idx]
            results.append({"track": idx, "result": True})
            tracks.remove(t)
            removed += 1
            self.mutations.append(("DeleteTrack", idx, name))
        for i, t in enumerate(tracks, start=1):
            t["index"] = i
        return {"removed_tracks": removed, "skipped": skipped, "delete_clips": clips, "delete_track": results,
                "page": page, "switched_page": switched, "calls": {}}


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
        self.resolve: Optional[FakeResolve] = None  # 있으면 표시·트랙 작업은 이 가짜 리졸브가 답한다
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
        if self.resolve is not None and op in RESOLVE_OPS:
            return self.resolve.request(op, args, timeout=timeout, cancel=cancel)
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
