"""AI 도우미 창(app/companion) 시험. 리졸브 대신 가짜 브리지를 쓴다 (화면 없이 offscreen).

창은 브리지를 작업 스레드에서 부르므로, 시험은 Qt 이벤트를 돌리며 결과가 올 때까지 기다린다.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import threading
import time
import wave
from pathlib import Path

import pytest

pytest.importorskip("PySide6")

from app.companion import connection, report, steps  # noqa: E402
from app.companion import strings_ko as S  # noqa: E402
from engine.resolve_link import SCRIPT_VERSION  # noqa: E402
from engine.resolve_link.bridge import BridgeCancelled, BridgeTimeout  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent


def _state(**changes) -> dict:
    state = {
        "project": "시험 프로젝트", "timeline": "타임라인 1",
        "start_frame": 86400, "end_frame": 86400 + 2400, "start_tc": "01:00:00:00",
        "current_tc": "01:00:10:00", "fps": "24", "fps_source": "timeline_get_setting", "drop_frame": False,
        "tracks": {"video": 1, "audio": 1, "subtitle": 0},
        "items": {
            "video": [{"track": 1, "name": "1화.mp4", "start": 86400, "end": 88800, "path": "D:\\촬영\\1화.mp4"}],
            "audio": [{"track": 1, "name": "1화.mp4", "start": 86400, "end": 88800, "path": "D:\\촬영\\1화.mp4"}],
        },
        "subtitle_items": 0, "truncated": False, "calls": {"Timeline.GetName": "ok"},
    }
    state.update(changes)
    return state


class FakeBridge:
    """LuaBridge 흉내. online이 False면 리졸브가 답하지 않는 것처럼 BridgeTimeout.

    state_timeout: ping에는 답하고 state만 늦음 (긴 타임라인, 리졸브 대화 상자)
    ping_delay: ping 답이 늦음 (자동 확인과 버튼이 겹치는지 보려고)
    prefs_changed: 답이 없을 때 BridgeTimeout에 실을 값 (Fusion.prefs는 바뀌었는데 답을 못 읽음)
    stored_name: 리졸브가 표시 이름을 다르게 저장하는 경우
    delete_leaves: 시험 표시를 지운다고 하고는 남기는 경우
    track_named: 시험 트랙 이름을 바꾸지 못한 경우 False
    """

    def __init__(self, tmp_path: Path) -> None:
        self.mailbox = tmp_path / "bridge"
        self.prefs = tmp_path / "Fusion.prefs"
        self.online = False
        self.closed = False
        self.state_data = _state()
        self.calls: list = []
        self.markers: list = []
        self.placed: list = []
        self._lock = threading.Lock()
        self.active = 0
        self.max_active = 0
        self.state_timeout = False
        self.ping_delay = 0.0
        self.prefs_changed: list = []
        self.stored_name = None
        self.delete_leaves = False
        self.track_named = True
        self.late_answers: list = []
        self.prefs_path = None
        self.owner = None
        self.script_version = None
        self.last_response = None
        self.connected = False

    def _answer(self, op: str, delay: float = 0.0) -> None:
        with self._lock:
            self.calls.append(op)
            self.active += 1
            self.max_active = max(self.max_active, self.active)
        try:
            if delay:
                time.sleep(delay)
            if self.closed:
                raise BridgeCancelled(op)
            if not self.online or (op == "state" and self.state_timeout):
                time.sleep(0.01)
                if op != "state" or not self.state_timeout:
                    self.connected = False
                raise BridgeTimeout(op, prefs_changed=self.prefs_changed)
            self.connected = True
            self.prefs_path, self.owner, self.script_version = self.prefs, "o1759000000_7", SCRIPT_VERSION
            self.last_response = {"ok": True, "sv": SCRIPT_VERSION, "result": {"op": op}}
        finally:
            with self._lock:
                self.active -= 1

    def ping(self, timeout=15.0):
        self._answer("ping", self.ping_delay)
        return {"script_version": SCRIPT_VERSION, "owner": self.owner, "product": "DaVinci Resolve",
                "resolve_version": "21.1.0.0", "env": {"loadfile": True, "io": False}, "calls": {}}

    def state(self, timeout=15.0):
        self._answer("state")
        return dict(self.state_data)

    def add_marker(self, frame, color="Yellow", name="", note="", custom="", duration=1, timeout=15.0):
        self._answer("add_marker")
        self.markers.append({"frame": frame, "color": color, "name": name, "note": note, "custom": custom,
                             "duration": duration})
        return {"added": True, "frame": frame, "requested_frame": frame, "calls": {"Timeline.AddMarker": "ok"}}

    def get_markers(self, timeout=15.0):
        self._answer("get_markers")
        out = []
        for m in self.markers:
            m = dict(m)
            if self.stored_name is not None:
                m["name"] = self.stored_name
            out.append(m)
        return {"markers": out, "calls": {"Timeline.GetMarkers": "ok"}}

    def place_audio(self, path, track_name, record_frame, frames, timeout=30.0):
        self._answer("place_audio")
        self.placed.append({"path": Path(path), "track_name": track_name, "record_frame": record_frame,
                            "frames": frames})
        return {"imported": True, "track_index": 2, "appended": 1, "item_start": record_frame,
                "item_end": record_frame + frames, "track_named": self.track_named, "calls": {}}

    def delete_markers(self, custom, timeout=15.0):
        self._answer("delete_markers")
        before = len(self.markers)
        if not self.delete_leaves:
            self.markers = [m for m in self.markers if m["custom"] != custom]
        gone = before - len(self.markers)
        return {"deleted": gone > 0, "deleted_count": gone, "calls": {}}

    def remove_audio(self, track_name, timeout=15.0):
        self._answer("remove_audio")
        return {"removed_tracks": 1, "skipped": 0, "calls": {}}

    def prefs_candidates(self):
        return [self.prefs]

    def backup_dir(self):
        return self.mailbox.parent / "backup"

    def close(self) -> None:
        self.closed = True


@pytest.fixture
def qapp():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


@pytest.fixture
def fake(tmp_path) -> FakeBridge:
    return FakeBridge(tmp_path)


@pytest.fixture
def window(qapp, fake, tmp_path, monkeypatch):
    monkeypatch.setenv("AIH_PREFS_FILE", str(fake.prefs))
    monkeypatch.setenv("APPDATA", str(tmp_path / "Roaming"))
    from app.companion.window import HelperWindow

    w = HelperWindow(bridge=fake, interactive=False, report_dir=tmp_path / "desktop", auto_ping_ms=50,
                     state_root=tmp_path / "state", process_check=None)
    w.show()
    qapp.processEvents()
    yield w
    w.close()
    qapp.processEvents()
    assert w.thread.isFinished()


def wait_until(qapp, cond, timeout: float = 10.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        qapp.processEvents()
        if cond():
            return
        time.sleep(0.005)
    raise AssertionError("정해진 시간 안에 끝나지 않음")


def connect(qapp, window, fake) -> None:
    fake.online = True
    wait_until(qapp, lambda: window.connected and window.pending == 0)


def click(qapp, window, button) -> None:
    assert button.isEnabled()
    button.click()
    wait_until(qapp, lambda: window.action is None and window.pending == 0)


# ---------------------------------------------------------------------------
# 창
# ---------------------------------------------------------------------------

def test_window_builds_and_waits_for_resolve(qapp, window, fake):
    from app.companion import window as win

    assert window.windowTitle() == S.WINDOW_TITLE == "AI 편집 도우미"
    assert window.width() <= win.WINDOW_WIDTH
    assert window.status.text() == "연결 안 됨"
    assert "AI_Helper_Connect" in window.header.hint.text() and window.header.hint.isVisible()
    wait_until(qapp, lambda: window.session.auto_attempts >= 2)
    assert not window.connected
    assert window.connect_btn.isEnabled() and window.report_btn.isEnabled()
    for btn in (window.marker_btn, window.audio_btn, window.cleanup_btn):
        assert not btn.isEnabled()
    assert set(fake.calls) == {"ping"}  # 연결 전에는 ping만 (리졸브에서 아무 일도 일어나지 않음)


def test_buttons_enable_after_successful_ping(qapp, window, fake):
    wait_until(qapp, lambda: window.session.auto_attempts >= 1)
    connect(qapp, window, fake)
    assert window.status.text() == "연결됨"
    for btn in (window.connect_btn, window.marker_btn, window.audio_btn, window.cleanup_btn):
        assert btn.isEnabled()
    assert window.info["version"].text() == "DaVinci Resolve 21.1.0.0"
    assert window.info["project"].text() == "시험 프로젝트"
    assert window.info["timeline"].text() == "타임라인 1"
    assert window.info["clips"].text() == "영상 1개, 소리 1개"
    assert window.info["first_clip"].text() == "1화.mp4"
    assert window.session.steps["connect"].ok
    # 연결된 뒤에는 자동 확인을 멈춘다
    count = len(fake.calls)
    deadline = time.monotonic() + 0.3
    while time.monotonic() < deadline:
        qapp.processEvents()
        time.sleep(0.01)
    assert len(fake.calls) == count


def test_marker_button_puts_marker_at_playhead(qapp, window, fake):
    connect(qapp, window, fake)
    click(qapp, window, window.marker_btn)
    # 재생 위치 01:00:10:00 - 시작 01:00:00:00 = 10초 = 24fps에서 240프레임
    assert fake.markers == [{"frame": 240, "color": "Yellow", "name": "AI 도우미 시험", "note": steps.TEST_NOTE,
                             "custom": "aih_test", "duration": 1}]
    rec = window.session.steps["marker"]
    assert rec.ok and "240프레임" in rec.summary and "확인하지는 못했" not in rec.summary
    # 버튼마다 먼저 연결을 확인하고, 넣은 뒤에는 다시 읽어 확인한다
    assert fake.calls[-4:] == ["ping", "state", "add_marker", "get_markers"]
    assert rec.data["markers_after"]["markers"][0]["custom"] == "aih_test"


def test_marker_stored_differently_is_not_success(qapp, window, fake):
    """AddMarker가 true를 줘도 이름이 다르게 저장되었으면 '됨'이 아니다 (다음 단계가 이 값에 기댄다)."""
    fake.stored_name = "AI ??? ??"
    connect(qapp, window, fake)
    click(qapp, window, window.marker_btn)
    rec = window.session.steps["marker"]
    assert not rec.ok and "이름이 다르게 저장" in rec.summary


def test_marker_without_playhead_goes_to_start_plus_one_second(qapp, window, fake):
    fake.state_data = _state(current_tc=None)
    connect(qapp, window, fake)
    click(qapp, window, window.marker_btn)
    assert fake.markers[0]["frame"] == 24
    assert "시작 + 1초" in window.session.steps["marker"].summary


def test_audio_button_places_test_tone(qapp, window, fake):
    connect(qapp, window, fake)
    click(qapp, window, window.audio_btn)
    [placed] = fake.placed
    assert placed["track_name"] == "AI 도우미 시험"
    assert (placed["record_frame"], placed["frames"]) == (86400 + 240, 72)
    assert placed["path"].parent == fake.mailbox / "files" and placed["path"].is_file()
    assert window.session.steps["audio"].ok
    with wave.open(str(placed["path"]), "rb") as w:
        # 파일은 올리는 길이(3초)보다 길다: 끝 프레임이 파일 밖으로 나가지 않게
        assert w.getnframes() / w.getframerate() == steps.TONE_FILE_SECONDS > steps.TONE_SECONDS


@pytest.mark.parametrize("fps, frames", [("29.97", 89), ("23.976", 71), ("59.94", 179), ("24", 72), ("25", 75)])
def test_audio_frames_never_exceed_three_seconds(qapp, window, fake, fps, frames):
    """29.97에서 3초는 89.91프레임: 반올림(90)하면 3초짜리 소리 밖의 프레임까지 달라고 하게 된다."""
    fake.state_data = _state(fps=fps, current_tc=None)
    connect(qapp, window, fake)
    click(qapp, window, window.audio_btn)
    assert fake.placed[0]["frames"] == frames
    assert frames / float(fps) <= steps.TONE_SECONDS


def test_audio_track_without_name_is_flagged(qapp, window, fake):
    fake.track_named = False
    connect(qapp, window, fake)
    click(qapp, window, window.audio_btn)
    rec = window.session.steps["audio"]
    assert not rec.ok and "직접 지워 주세요" in rec.summary


def test_timeout_during_action_goes_back_to_waiting(qapp, window, fake):
    connect(qapp, window, fake)
    fake.online = False
    click(qapp, window, window.marker_btn)
    assert not window.connected
    assert window.status.text() == "연결 안 됨" and "AI_Helper_Connect" in window.header.hint.text()
    assert not window.marker_btn.isEnabled() and window.connect_btn.isEnabled()
    rec = window.session.steps["marker"]
    assert not rec.ok and rec.error["type"] == "BridgeTimeout"
    # 자동 확인이 다시 돈다
    count = window.session.auto_attempts
    wait_until(qapp, lambda: window.session.auto_attempts > count)


def test_slow_state_after_ping_keeps_connection(qapp, window, fake):
    """ping에는 답했는데 다음 작업만 늦으면 '연결 안 됨'이 아니다 (Scripts 메뉴를 다시 누르라고 하지 않는다)."""
    connect(qapp, window, fake)
    fake.state_timeout = True
    click(qapp, window, window.marker_btn)
    rec = window.session.steps["marker"]
    assert not rec.ok and rec.summary == steps.SLOW_TEXT and "Scripts" not in rec.summary
    assert window.connected and window.status.text() == "연결됨" and window.marker_btn.isEnabled()
    assert "ping" in rec.data


def test_auto_check_with_slow_state_is_connected(qapp, window, fake):
    """자동 확인에서 ping은 되고 state만 늦어도 연결된 것이다. 받은 답(리졸브 판)을 버리지 않는다."""
    fake.state_timeout = True
    connect(qapp, window, fake)
    rec = window.session.steps["connect"]
    assert rec.ok and "DaVinci Resolve 21.1.0.0" in rec.summary and "답이 늦습니다" in rec.summary
    # ping에는 답했는데 읽기가 늦다: "연결 안 됨"이 아니라 "리졸브가 바빠요" (대화 상자를 닫아 달라고)
    assert window.status.text() == "리졸브가 바빠요" and window.connect_btn.text() == "다시 시도"
    assert rec.data["ping"]["resolve_version"] == "21.1.0.0"
    assert rec.data["state_error"]["type"] == "BridgeTimeout"
    assert window.info["version"].text() == "DaVinci Resolve 21.1.0.0"
    # 연결된 뒤에는 같은 ping+state를 되풀이하지 않는다
    count = len(fake.calls)
    deadline = time.monotonic() + 0.3
    while time.monotonic() < deadline:
        qapp.processEvents()
        time.sleep(0.01)
    assert len(fake.calls) == count


def test_button_during_auto_check_never_overlaps(qapp, window, fake):
    """우체통은 한 칸이라 자동 확인과 버튼 작업이 동시에 나가면 안 된다."""
    fake.ping_delay = 0.2
    wait_until(qapp, lambda: window.pending == 1)  # 자동 확인이 도는 중
    fake.online = True
    window.connect_btn.click()
    wait_until(qapp, lambda: window.action is None and window.pending == 0 and window.connected)
    assert fake.max_active == 1


def test_unreadable_answers_stop_auto_checks(qapp, window, fake, monkeypatch):
    """Fusion.prefs는 바뀌는데 답을 못 읽으면, 리졸브가 설정 파일을 계속 저장하지 않게 자동 확인을 멈춘다."""
    from app.companion import window as win

    fake.prefs_changed = [str(fake.prefs)]
    wait_until(qapp, lambda: window.auto_stopped)
    attempts = window.session.auto_attempts
    # 창을 띄우자마자 나간 첫 확인은 prefs_changed를 바꾸기 전에 끝났을 수 있다 (정확한 수는 test_connection.py)
    assert win.UNREAD_LIMIT <= attempts <= win.UNREAD_LIMIT + 1
    assert window.message.text() == win.UNREAD_TEXT
    rec = window.session.steps["connect"]
    assert not rec.ok and rec.error["prefs_changed"] == [str(fake.prefs)]
    deadline = time.monotonic() + 0.3
    while time.monotonic() < deadline:
        qapp.processEvents()
        time.sleep(0.01)
    assert window.session.auto_attempts == attempts and not window.timer.isActive()
    # ① 버튼은 그대로 쓸 수 있다
    fake.online = True
    click(qapp, window, window.connect_btn)
    assert window.connected and not window.auto_stopped


def test_auto_checks_slow_down_after_a_while(qapp, window, fake, monkeypatch):
    from app.companion import window as win

    monkeypatch.setattr(connection, "AUTO_SLOW_AFTER", 3)
    wait_until(qapp, lambda: window.timer.interval() == win.AUTO_PING_SLOW_MS)
    assert window.session.auto_attempts >= 3
    fake.online = True
    click(qapp, window, window.connect_btn)
    assert window.timer.interval() == 50  # 연결되면 처음 간격으로 돌아간다


def test_late_answers_make_auto_check_wait_longer(qapp, window, fake):
    fake.late_answers = [{"id": 1, "op": "ping", "owner": "o1", "ok": True}]
    wait_until(qapp, lambda: window._auto_step is connection.panel_connect_step)
    assert "늦게" in window.link_info()["auto_note"]


def test_no_timeline_is_explained(qapp, window, fake):
    fake.state_data = _state(timeline=None)
    connect(qapp, window, fake)
    assert "타임라인을 열어" in window.info["timeline"].text()
    click(qapp, window, window.audio_btn)
    rec = window.session.steps["audio"]
    assert not rec.ok and "타임라인을 열어" in rec.summary
    assert window.connected and fake.placed == []


def test_report_has_step_summary_and_raw_answers(qapp, window, fake, tmp_path):
    connect(qapp, window, fake)
    click(qapp, window, window.marker_btn)
    fake.online = False
    click(qapp, window, window.audio_btn)
    window.report_btn.click()
    wait_until(qapp, lambda: window.last_report is not None)
    path = window.last_report
    assert path.parent == tmp_path / "desktop"
    assert re.fullmatch(r"AI도우미_결과_\d{8}-\d{4}\.txt", path.name)
    text = path.read_text(encoding="utf-8-sig")
    lines = text.splitlines()
    assert lines[0] == report.REPORT_TITLE
    assert '① 연결: 됨 - DaVinci Resolve 21.1.0.0 / 프로젝트 "시험 프로젝트" / 타임라인 "타임라인 1"' in lines
    assert any(line.startswith("② 표시 찍기: 됨 - 타임라인 시작에서 240프레임") for line in lines)
    assert any(line.startswith("③ 소리 넣기: 안 됨 - 리졸브가 대답하지 않습니다.") for line in lines)
    assert "시험 흔적 지우기: 아직 안 함" in lines
    assert f"답한 Fusion.prefs: {fake.prefs}" in lines
    assert '"Timeline.AddMarker": "ok"' in text  # 리졸브의 답(calls)이 그대로 들어 있다
    assert '"path": "D:\\\\촬영\\\\1화.mp4"' in text  # 사용자 경로는 가리지 않는다
    assert "BridgeTimeout" in text
    assert re.search(r"^코드 지문: [0-9a-f]{12}$", text, re.M)  # ZIP으로 받아 git 정보가 없어도 어떤 코드인지
    assert "[우체통의 요청 파일]" in text and "---- 리졸브의 마지막 답" in text


def test_report_reads_pc_info_off_the_window_thread(qapp, window, fake, monkeypatch):
    """윈도우 판을 읽는 일('ver' 명령 실행)은 창이 아니라 작업 스레드에서 한다 (창이 1.2초 멈췄던 문제)."""
    threads = []
    real = report.system_lines

    def spy():
        threads.append(threading.current_thread() is threading.main_thread())
        return real()

    monkeypatch.setattr(report, "system_lines", spy)
    window.report_btn.click()
    wait_until(qapp, lambda: window.last_report is not None)
    assert threads == [False]
    text = window.last_report.read_text(encoding="utf-8-sig")
    assert re.search(r"^코드 지문: [0-9a-f]{12}$", text, re.M)


def test_report_keeps_every_attempt(qapp, window, fake):
    """같은 단계를 여러 번 해도 각 시도의 답이 모두 결과 파일에 남는다."""
    connect(qapp, window, fake)
    click(qapp, window, window.marker_btn)
    fake.state_data = _state(current_tc="01:00:20:00")
    click(qapp, window, window.marker_btn)
    fake.late_answers = [{"id": 17, "op": "ping", "owner": "o1", "ok": True, "error": None}]
    text = window.report_text({})
    assert "② 표시 찍기 1번째" in text and "② 표시 찍기 2번째" in text
    assert '"offset": 240' in text and '"offset": 480' in text
    assert "[답을 기다리다 그만둔 뒤에 온 답]" in text and '"id": 17' in text
    assert "② 표시 찍기: 됨 - 타임라인 시작에서 480프레임" in text  # 맨 위 요약은 마지막 결과


def test_cleanup_checks_that_markers_are_gone(qapp, window, fake):
    connect(qapp, window, fake)
    click(qapp, window, window.marker_btn)
    click(qapp, window, window.marker_btn)
    fake.delete_leaves = True
    click(qapp, window, window.cleanup_btn)
    rec = window.session.steps["cleanup"]
    assert not rec.ok and "시험 표시 2개가 아직 남아" in rec.summary
    fake.delete_leaves = False
    click(qapp, window, window.cleanup_btn)
    rec = window.session.steps["cleanup"]
    assert rec.ok and rec.summary.startswith("시험 표시 2개 지움")
    assert len(rec.data["markers_before"]["markers"]) == 2 and rec.data["markers_after"]["markers"] == []


def test_window_opens_even_when_mailbox_cannot_be_ascii(qapp, tmp_path, monkeypatch):
    """ProgramData·%PUBLIC%에 쓸 수 없는 한글 사용자 PC: 오류 창 대신 AI 도우미 창이 뜨고 이유가 보인다."""
    from app.companion.window import HelperWindow
    from engine.resolve_link import paths

    local = tmp_path / "홍길동" / "Local"
    monkeypatch.delenv("AIH_MAILBOX_DIR", raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(local))
    monkeypatch.setenv("AIH_PREFS_FILE", str(tmp_path / "Fusion.prefs"))
    for var in ("PROGRAMDATA", "PUBLIC"):
        blocker = tmp_path / var
        blocker.write_text("폴더를 만들 수 없는 자리", encoding="utf-8")
        monkeypatch.setenv(var, str(blocker))
    monkeypatch.setattr(paths, "_short_path", lambda p: None)
    w = HelperWindow(interactive=False, report_dir=tmp_path, auto_ping_ms=10_000)
    try:
        assert w.bridge.mailbox == local / "video-editing-systems" / "bridge"
        assert "영문이 아닌 글자" in w.log_view.toPlainText()
        assert "영문 경로가 아닙니다" in w.report_text({})
    finally:
        w.close()


def test_close_stops_worker_and_bridge(qapp, fake, tmp_path):
    from app.companion.window import HelperWindow

    w = HelperWindow(bridge=fake, interactive=False, report_dir=tmp_path, auto_ping_ms=50,
                     state_root=tmp_path / "state", process_check=None)
    w.show()
    wait_until(qapp, lambda: w.session.auto_attempts >= 1)
    w.close()
    assert fake.closed and w.thread.isFinished()
    assert not w.connect_btn.isEnabled()


# ---------------------------------------------------------------------------
# 단계 계산과 결과 파일 (화면 없이)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("changes, expected", [
    ({}, (240, "playhead")),
    ({"fps": "29.97", "drop_frame": True, "start_tc": "01:00:00;00", "current_tc": "01:01:00;02",
      "start_frame": 107892, "end_frame": 107892 + 5000}, (1800, "playhead")),
    ({"fps": "29.97", "drop_frame": None, "start_tc": "00:00:00;00", "current_tc": "00:10:00;00",
      "start_frame": 0, "end_frame": 30000}, (17982, "playhead")),
    ({"current_tc": None}, (24, "start_plus_1s")),
    ({"current_tc": "00:59:59:00"}, (24, "start_plus_1s")),  # 시작보다 앞
    ({"current_tc": "02:00:00:00"}, (24, "start_plus_1s")),  # 타임라인 끝보다 뒤
    ({"current_tc": "쓰레기"}, (24, "start_plus_1s")),
    ({"fps": None, "current_tc": None}, (24, "start_plus_1s")),
    ({"fps": "25", "current_tc": None, "end_frame": 86410}, (10, "start_plus_1s")),  # 1초보다 짧은 타임라인
])
def test_playhead_offset(changes, expected):
    assert steps.playhead_offset(_state(**changes)) == expected


def test_timeline_start_frame_from_timecode():
    assert steps.timeline_start_frame(_state(start_frame=None)) == 86400
    with pytest.raises(steps.StepProblem):
        steps.timeline_start_frame(_state(start_frame=None, start_tc=None))


def test_explain_bridge_errors():
    from engine.resolve_link.bridge import BridgeError

    assert "프로젝트를 열어" in steps.explain(BridgeError("no_project", "ProjectManager.GetCurrentProject"))
    assert "bad_path:outside" in steps.explain(BridgeError("bad_path:outside", "path_guard"))
    info = steps.error_info(BridgeError("import_failed", "MediaPool.ImportMedia", "place_audio"))
    assert info["error"] == "import_failed" and info["func"] == "MediaPool.ImportMedia"
    assert "가져오지 못했습니다" in info["message"]
    assert "예상하지 못한 오류" in steps.explain(RuntimeError("x"))


def test_cleanup_summary_warns_about_tracks_left_behind():
    ok, text = steps.summarize("cleanup", {"delete_markers": {"deleted": True},
                                           "remove_audio": {"removed_tracks": 0, "skipped": 1}})
    assert not ok and "직접 지워 주세요" in text
    ok, text = steps.summarize("cleanup", {"delete_markers": {"deleted": False},
                                           "remove_audio": {"removed_tracks": 1, "skipped": 0}})
    assert ok and text == "시험 표시 없음, 시험 트랙 1개 지움"
    # 트랙 이름이 바뀌지 않아 찾지 못한 시험 소리 (0개 지움인데 타임라인에 남아 있음)
    left = {"items": {"audio": [{"path": "C:\\x\\bridge\\files\\AIH_TEST_TONE.WAV"}]}}
    ok, text = steps.summarize("cleanup", {"delete_markers": {"deleted": False, "deleted_count": 0, "remaining": 0},
                                           "remove_audio": {"removed_tracks": 0, "skipped": 0}, "state": left})
    assert not ok and "시험 소리 1개가 타임라인에 아직 남아" in text
    # 표시 목록을 다시 못 읽으면 스크립트가 센 남은 수를 쓴다
    ok, text = steps.summarize("cleanup", {"delete_markers": {"deleted": True, "deleted_count": 1, "remaining": 1},
                                           "remove_audio": {"removed_tracks": 0, "skipped": 0}})
    assert not ok and "시험 표시 1개가 아직 남아" in text


def test_marker_problems_from_listing():
    out = {"marker": {"added": True, "frame": 30}, "offset_how": "playhead"}
    mk = lambda **kw: dict({"frame": 30, "name": steps.TEST_NAME, "note": steps.TEST_NOTE,  # noqa: E731
                            "custom": steps.TEST_CUSTOM}, **kw)
    assert steps.summarize("marker", dict(out, markers_after={"markers": [mk()]}))[0]
    assert steps.marker_problems(dict(out, markers_after={"markers": [mk(custom="")]})) == [
        "custom data가 저장되지 않았습니다"]
    assert steps.marker_problems(dict(out, markers_after={"markers": [mk(frame=86430)]})) == [
        "표시가 30프레임이 아니라 86430프레임에 있습니다"]
    assert steps.marker_problems(dict(out, markers_after={"markers": [mk(note="?")]})) == ["메모가 다르게 저장되었습니다"]
    ok, text = steps.summarize("marker", out)
    assert ok and "확인하지는 못했습니다" in text


def test_error_info_keeps_lua_calls():
    """Lua가 실패로 답하면, 어떤 함수가 어떤 오류를 냈는지(calls)와 detail을 결과 파일에 남긴다."""
    from engine.resolve_link.bridge import BridgeError

    payload = {"ok": False, "error": "lua_error", "func": "place_audio", "detail": "attempt to index nil",
               "calls": {"MediaPool.ImportMedia": "err:bad argument #1"}}
    info = steps.error_info(BridgeError("lua_error", "place_audio", "place_audio", payload=payload))
    assert info["calls"] == payload["calls"] and info["detail"] == "attempt to index nil"
    timeout = steps.error_info(BridgeTimeout("ping", req_id=5, prefs_changed=["C:\\p\\Fusion.prefs"]))
    assert timeout["req_id"] == 5 and timeout["prefs_changed"] == ["C:\\p\\Fusion.prefs"]


def test_report_step_lines_and_fatal_answers(tmp_path):
    session = report.TestSession()
    session.record("connect", True, "DaVinci Resolve 21.1", {"ping": {"owner": "o1"}})
    session.record("audio", False, "리졸브가 대답하지 않습니다.", {}, {"type": "BridgeTimeout"})
    assert report.step_lines(session) == [
        "① 연결: 됨 - DaVinci Resolve 21.1",
        "② 표시 찍기: 아직 안 함",
        "③ 소리 넣기: 안 됨 - 리졸브가 대답하지 않습니다.",
        "시험 흔적 지우기: 아직 안 함",
    ]
    fatal = b'{"ok":false,"sv":"1.0.0","error":"no_setfenv","func":"env"}'.hex()
    prefs = tmp_path / "Fusion.prefs"
    prefs.write_text(f'Global = {{ AIHelper = {{ Response = "AIH1:o1_2:0:{fatal}", }}, }}', encoding="utf-8")
    [found] = report.find_fatal_responses([prefs, tmp_path / "none.prefs"])
    assert found["answer"]["error"] == "no_setfenv" and found["file"] == str(prefs)
    text = report.build_report(session, {"connected": False, "mailbox": "C:\\x"}, {"fatal": [found]})
    assert "no_setfenv" in text and "우체통 폴더: C:\\x" in text


def test_app_version_reads_git_files(tmp_path):
    git = tmp_path / ".git"
    (git / "refs" / "heads").mkdir(parents=True)
    (git / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
    assert report.app_version(tmp_path) == "unknown"
    (git / "packed-refs").write_text("# pack-refs\n" + "a" * 40 + " refs/heads/main\n", encoding="utf-8")
    assert report.app_version(tmp_path) == "aaaaaaa"
    (git / "refs" / "heads" / "main").write_text("b" * 40 + "\n", encoding="utf-8")
    assert report.app_version(tmp_path) == "bbbbbbb"
    assert report.app_version(tmp_path / "no-repo") == "unknown"


def test_helper_smoke_test_command(tmp_path):
    """python -m app --helper-smoke-test: 리졸브 없이 설치 → 창 → 결과 파일까지 (CI와 같은 명령)."""
    env = dict(
        os.environ,
        QT_QPA_PLATFORM="offscreen",
        LOCALAPPDATA=str(tmp_path / "Local"),
        APPDATA=str(tmp_path / "Roaming"),
        PROGRAMDATA=str(tmp_path / "ProgramData"),
        AIH_PREFS_FILE=str(tmp_path / "Fusion.prefs"),
        PYTHONIOENCODING="utf-8",
    )
    env.pop("AIH_MAILBOX_DIR", None)
    proc = subprocess.run(
        [sys.executable, "-m", "app", "--helper-smoke-test"], cwd=str(ROOT), env=env,
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60,
        stdin=subprocess.DEVNULL,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "HELPER SMOKE OK" in proc.stdout
    state = tmp_path / "Local" / "video-editing-systems"
    assert (state / "window_ok.flag").is_file()
    assert len(list(state.glob("AI도우미_결과_*.txt"))) == 1
    assert "3 slots" in proc.stdout  # 자동화 버튼 3개, 대화 입력 칸, ⋯ 메뉴, 아래쪽이 보이는지도 확인한다
    installed = list((tmp_path / "Roaming").rglob("AI_Helper_Connect.lua"))
    assert len(installed) == 1
    text = installed[0].read_text(encoding="utf-8")
    assert "@@MAILBOX_HEX@@" not in text and f'AIH.VERSION = "{SCRIPT_VERSION}"' in text
