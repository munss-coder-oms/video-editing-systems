"""ConnectionController (설계 B7.5, S13 개정): 한가할 때는 리졸브에 아무것도 묻지 않는다.

가짜 타이머로 몇 분을 흉내 낸다. 리졸브에 요청이 갈 때마다 Lua가 Fusion 설정 파일을 다시 저장하는데,
리졸브를 끄는 중에 설정을 쓰다 리졸브가 꺼진 일이 있어서 이 시험이 중요하다.
"""

from __future__ import annotations

import os

import pytest

pytest.importorskip("PySide6")

from app.companion import connection as conn  # noqa: E402
from tests.fakes import FakeLuaBridge, FakeScheduler, SyncQueue  # noqa: E402


@pytest.fixture
def qapp():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


class Process:
    """가짜 윈도우 작업 목록: Resolve.exe가 있는지."""

    def __init__(self) -> None:
        self.running = True
        self.calls = 0

    def __call__(self):
        self.calls += 1
        return self.running


def make(bridge, process=None, **kw):
    sched = FakeScheduler()
    queue = SyncQueue(bridge)
    c = conn.ConnectionController(
        queue, scheduler=sched, clock=sched.clock, process_check=process,
        run_async=lambda fn, cb: cb(fn()), **kw,
    )
    return c, sched, queue


def connected(bridge, process=None, **kw):
    c, sched, queue = make(bridge, process, **kw)
    c.start()
    sched.advance(0)
    assert c.status == conn.CONNECTED
    return c, sched, queue


MINUTE = 60_000


def test_connected_controller_sends_nothing_while_idle(qapp):
    """연결된 뒤 사용자가 아무것도 하지 않으면 10분 동안 요청 0개 (Resolve.exe만 5초마다 본다)."""
    bridge, process = FakeLuaBridge(), Process()
    c, sched, _ = connected(bridge, process)
    assert bridge.requests == ["ping", "timeline_info"]
    before = len(bridge.requests)
    sched.advance(10 * MINUTE)
    assert len(bridge.requests) == before
    assert c.status == conn.CONNECTED
    assert process.calls >= 10 * 60 // 5 - 1  # 리졸브에는 묻지 않고 작업 목록만 봤다
    assert not c.timer.isActive()


def test_resolve_exe_gone_flips_state_without_request(qapp):
    bridge, process = FakeLuaBridge(), Process()
    c, sched, _ = connected(bridge, process)
    before = len(bridge.requests)
    process.running = False
    sched.advance(conn.PROCESS_MS)
    assert c.status == conn.RESOLVE_QUIT
    assert len(bridge.requests) == before
    # 꺼져 있는 동안에는 연결 전 자동 확인도 하지 않는다
    sched.advance(2 * MINUTE)
    assert len(bridge.requests) == before
    # 다시 켜지면 연결 전처럼 2초마다 묻는다 (스크립트를 다시 눌러야 답한다)
    bridge.online = False
    process.running = True
    sched.advance(conn.PROCESS_MS + conn.AUTO_PING_MS * 3)
    assert c.status == conn.NOT_CONNECTED
    assert bridge.requests[before:] and set(bridge.requests[before:]) == {"ping"}


def test_focus_in_pings_at_most_once_per_30_seconds(qapp):
    bridge = FakeLuaBridge()
    c, sched, _ = connected(bridge, Process())
    # 연결한 직후(30초 안)에는 창을 다시 봐도 묻지 않는다
    for _ in range(5):
        sched.advance(1000)
        assert c.on_focus_in() is False
    sched.advance(conn.FOCUS_DEBOUNCE_S * 1000)
    before = len(bridge.requests)
    sent = 0
    for _ in range(10):  # 30초 안에 10번
        sent += bool(c.on_focus_in())
        sched.advance(2900)
    pings = bridge.requests[before:].count("ping")
    assert sent == 1 and pings == 1
    assert c.focus_pings == 1


def test_focus_in_before_connect_sends_nothing(qapp):
    bridge = FakeLuaBridge()
    bridge.online = False
    c, sched, _ = make(bridge, Process(), auto_ping_ms=10 ** 9)
    assert c.on_focus_in() is False
    assert bridge.requests == []


def test_button_press_pings_first(qapp):
    bridge = FakeLuaBridge()
    c, sched, _ = connected(bridge, Process())
    sched.advance(5 * MINUTE)
    before = len(bridge.requests)
    got = []
    assert c.before_action("slot", got.append)
    assert bridge.requests[before] == "ping"
    assert bridge.requests[before:before + 2] == ["ping", "timeline_info"]
    assert got and got[0]["ping"]["script_version"]


def test_pre_connect_auto_ping_stops_at_first_answer(qapp):
    bridge = FakeLuaBridge()
    bridge.online = False
    c, sched, _ = make(bridge, Process())
    c.start()
    sched.advance(conn.AUTO_PING_MS * 5)
    assert c.status == conn.NOT_CONNECTED
    assert bridge.requests.count("ping") >= 5 and set(bridge.requests) == {"ping"}
    bridge.online = True
    sched.advance(conn.AUTO_PING_MS)
    assert c.status == conn.CONNECTED
    count = len(bridge.requests)
    sched.advance(10 * MINUTE)
    assert len(bridge.requests) == count
    assert not c.timer.isActive()


def test_old_script_is_flagged_and_falls_back_to_state(qapp):
    bridge = FakeLuaBridge()
    bridge.old_script = True
    c, sched, _ = connected_or_old(bridge)
    assert c.status == conn.OLD_SCRIPT
    assert bridge.requests == ["ping", "state"]
    assert c.timeline["timeline"] == "Timeline 1"
    assert not c.usable


def connected_or_old(bridge):
    c, sched, queue = make(bridge, Process())
    c.start()
    sched.advance(0)
    return c, sched, queue


def test_busy_read_after_ping_is_busy_not_disconnected(qapp):
    bridge = FakeLuaBridge()
    bridge.busy_ops = {"timeline_info"}
    c, sched, _ = connected_or_old(bridge)
    assert c.status == conn.BUSY and c.connected
    bridge.busy_ops = set()
    c.check_now("connect")
    assert c.status == conn.CONNECTED


def test_probe_copy_timeline_makes_panel_unusable(qapp):
    bridge = FakeLuaBridge()
    bridge.info = dict(bridge.info, timeline="AI 도우미 점검용 101500", timeline_uid="tl-2")
    c, sched, _ = connected_or_old(bridge)
    assert c.status == conn.CONNECTED
    assert c.probe_copy_name == "AI 도우미 점검용 101500" and not c.usable


def test_unreadable_answers_stop_auto_checks(qapp):
    bridge = FakeLuaBridge()
    bridge.online = False
    bridge.prefs_changed = ["C:\\p\\Fusion.prefs"]
    stopped = []
    c, sched, _ = make(bridge, Process())
    c.failed.connect(lambda name, exc: stopped.append(name))
    c.start()
    sched.advance(conn.AUTO_PING_MS * 10)
    assert c.auto_stopped and stopped == ["auto_stopped"]
    assert bridge.requests.count("ping") == conn.UNREAD_LIMIT


def test_no_process_watch_without_process_check(qapp):
    bridge = FakeLuaBridge()
    c, sched, _ = connected(bridge, None)
    sched.advance(MINUTE)
    assert c.process_checks == 0 and not c.process_timer.isActive()
    assert bridge.requests == ["ping", "timeline_info"]


def test_extras_ride_on_the_connect_check_only_when_needed(qapp, tmp_path):
    """타임라인이 바뀐 때만 소리 클립을, 처음 보는 판일 때만 probe_read를 (연결 확인 때만)."""
    from engine.resolve_link.caps import CapabilityStore

    bridge = FakeLuaBridge()
    store = CapabilityStore(tmp_path / "caps")
    known = {"ident": None}

    def extras():
        import functools

        return [functools.partial(conn.extra_audio_items, known=known["ident"]),
                functools.partial(conn.extra_probe_read, store=store),
                functools.partial(conn.extra_reconcile, root=tmp_path)]

    answers = []
    c, sched, _ = make(bridge, Process(), extras=extras)
    c.answered.connect(lambda name, out: answers.append(out))
    c.start()
    sched.advance(0)
    assert bridge.requests == ["ping", "timeline_info", "timeline_items", "probe_read"]
    known["ident"] = answers[-1]["audio_items"]["ident"]
    assert answers[-1]["audio_items"]["items"][0]["path"] == "D:\\녹화\\녹화.mp4"
    c.check_now("connect")
    assert bridge.requests[4:] == ["ping", "timeline_info"]  # 같은 타임라인, 이미 점검한 판: 더 묻지 않는다
    sched.advance(10 * MINUTE)
    assert len(bridge.requests) == 6
