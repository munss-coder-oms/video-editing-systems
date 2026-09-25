"""앱 ↔ 리졸브 스크립트 전체 왕복 시험 (리졸브 없이).

실제 설치 프로그램이 실제 Lua 틀(resolve_scripts/AI_Helper_Connect.lua)을 임시 Utility 폴더에 설치하고,
그 파일을 LuaJIT(lupa)에서 리졸브 21 무료판처럼 io, require, package, ffi, debug,
os.execute, os.remove를 없앤 채 돌린다. 스크립트 반복은 뒤쪽 스레드에서 돌고
(bmd.wait = 실제로 잠깐 쉬기), 가짜 fusion:SavePrefs()가 가짜 Fusion.prefs 파일을 쓴다.
앱 쪽은 실제 LuaBridge가 AIH_PREFS_FILE로 그 파일을 읽어 답을 받는다.

가짜 리졸브는 tests/test_lua_script.py의 것을 그대로 쓴다.
사용자 PC에는 lupa가 없으므로 이 시험은 건너뛴다.
"""

from __future__ import annotations

import math
import threading
import time
import wave
from pathlib import Path

import pytest

pytest.importorskip("lupa")
lupa_jit = pytest.importorskip("lupa.luajit21")

from app.companion import report, steps  # noqa: E402
from engine.resolve_link import SCRIPT_VERSION, protocol  # noqa: E402
from engine.resolve_link.bridge import BridgeError, BridgeTimeout, LuaBridge  # noqa: E402
from engine.resolve_link.install import install_script  # noqa: E402
from engine.resolve_link.testfiles import make_test_tone  # noqa: E402
from tests.test_lua_script import FAKE_LUA, prefs_text  # noqa: E402

TICK = 0.02  # bmd.wait 한 번에 실제로 쉬는 시간 (시험을 빨리 끝내려고 0.1초보다 짧게)
CLIP_PATH = "C:\\영상\\촬영 원본.mp4"
TRACK = "AI 도우미 시험"

# 리졸브 스크립트 메뉴에서 없는 것들 (os.exit는 시험 프로그램을 끄지 않게 뺀다)
SANDBOX = """
io = nil
require = nil
package = nil
debug = nil
ffi = nil
python = nil
os.execute = nil
os.remove = nil
os.exit = nil
"""


class LoopStopped(Exception):
    """시험이 끝나 뒤쪽 스레드의 스크립트 반복을 멈춘다."""


class Rig:
    """임시 폴더에 설치한 스크립트 + 가짜 리졸브 + 실제 LuaBridge."""

    def __init__(self, tmp_path: Path, monkeypatch) -> None:
        self.mailbox = tmp_path / "bridge"
        self.prefs = tmp_path / "Profiles" / "Fusion.prefs"
        monkeypatch.setenv("AIH_MAILBOX_DIR", str(self.mailbox))
        monkeypatch.setenv("AIH_PREFS_FILE", str(self.prefs))
        monkeypatch.setenv("APPDATA", str(tmp_path / "Roaming"))
        monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "Local"))
        monkeypatch.delenv("PROGRAMDATA", raising=False)

        self.install = install_script()
        [self.script] = self.install.paths
        self.source = self.script.read_text(encoding="utf-8")

        self.saves = 0
        self.lua = lupa_jit.LuaRuntime(unpack_returned_tuples=True)
        self.fake = self.lua.execute(FAKE_LUA)(self._save_prefs)
        self.lua.execute(SANDBOX)
        g = self.lua.globals()
        g.resolve, g.fusion, g.bmd = self.fake.resolve, self.fake.fusion, self.fake.bmd
        self.fake.bmd.wait = self._wait  # 가짜의 코루틴 멈춤 대신 실제로 쉰다
        self.fake.media_frames = self._media_frames
        # 사용자가 타임라인에 올려 둔 영상 하나
        self.fake.add_clip("video", 1, "촬영 원본.mp4", 86400, 900, CLIP_PATH)

        self.backup = tmp_path / "backup"
        self.bridge = LuaBridge(backup_dir=self.backup, poll_interval=TICK)
        self.stop_flag = threading.Event()
        self.thread: threading.Thread | None = None
        self.error: BaseException | None = None
        self.in_loop_calls: list = []

    # --- 가짜 리졸브가 부르는 것 (스크립트 반복 스레드에서) ---
    def _save_prefs(self, prefs) -> None:
        self.saves += 1
        self.prefs.parent.mkdir(parents=True, exist_ok=True)
        # 진짜 리졸브처럼 제자리에 덮어쓴다 (앱은 반쯤 쓰인 파일도 견뎌야 한다).
        self.prefs.write_text(prefs_text(dict(prefs.items())), encoding="utf-8")

    @staticmethod
    def _media_frames(path) -> int:
        """가져온 WAV의 길이를 29.97 타임라인 프레임으로 (버림). 파일보다 긴 범위는 가짜 리졸브가 거절한다."""
        try:
            with wave.open(str(path), "rb") as w:
                return math.floor(w.getnframes() / w.getframerate() * 29.97)
        except (OSError, EOFError, wave.Error):
            return 90

    def _wait(self, seconds) -> None:
        if self.stop_flag.is_set():
            raise LoopStopped("시험 끝")
        while self.in_loop_calls:
            fn, done = self.in_loop_calls.pop(0)
            fn()
            done.set()
        time.sleep(TICK)

    def in_loop(self, fn) -> None:
        """스크립트가 도는 동안 가짜 리졸브를 바꾼다: Lua는 한 스레드에서만 만질 수 있어 반복 스레드에 맡긴다."""
        done = threading.Event()
        self.in_loop_calls.append((fn, done))
        assert done.wait(10), "스크립트 반복이 가짜 리졸브를 바꾸지 못함"

    # --- 스크립트 메뉴 누르기 / 멈추기 ---
    def start(self) -> None:
        assert self.thread is None

        def run() -> None:
            try:
                self.lua.execute(self.source)
            except BaseException as exc:  # 시험이 멈춘 것이 아니면 기록해 두고 실패시킨다
                if not self.stop_flag.is_set():
                    self.error = exc

        self.thread = threading.Thread(target=run, name="lua-loop", daemon=True)
        self.thread.start()

    def join(self) -> None:
        """스크립트가 스스로(stop 요청) 끝나기를 기다린다."""
        assert self.thread is not None
        self.thread.join(10)
        assert not self.thread.is_alive(), "스크립트 반복이 끝나지 않음"
        assert self.error is None, self.error

    def stop(self) -> None:
        self.stop_flag.set()
        self.bridge.close()
        if self.thread is not None:
            self.thread.join(10)
            assert not self.thread.is_alive(), "스크립트 반복이 멈추지 않음"

    # --- 반복이 끝난 뒤 가짜 리졸브 기록 보기 ---
    def logged(self, name: str) -> list:
        assert self.thread is None or not self.thread.is_alive()
        out = []
        log = self.fake.log
        for i in range(1, len(log) + 1):
            e = log[i]
            if e.name == name:
                out.append([e.args[j] for j in range(1, e.n + 1)])
        return out


@pytest.fixture
def rig(tmp_path, monkeypatch):
    r = Rig(tmp_path, monkeypatch)
    try:
        yield r
    finally:
        r.stop()


def _table(t) -> dict:
    return dict(t.items())


def test_full_round_trip(rig):
    rig.start()
    ping = rig.bridge.ping(timeout=10)
    assert ping["script_version"] == SCRIPT_VERSION == rig.bridge.script_version
    assert ping["owner"] == rig.bridge.owner
    assert ping["owner_readback"] is True
    assert ping["mailbox"] == str(rig.mailbox)
    assert ping["product"] == "DaVinci Resolve" and ping["resolve_version"] == "21.1.0.0"
    env = ping["env"]
    assert env["loadfile"] and env["setfenv"] and env["bmd_wait"] and env["os_time"]
    assert not (env["io"] or env["require"] or env["ffi"] or env["os_execute"])
    assert rig.bridge.prefs_path == rig.prefs and rig.bridge.connected
    # 스크립트가 아는 작업 목록을 기억하고, 답을 받은 요청 파일은 지운다 (S2)
    assert rig.bridge.known_ops == frozenset(protocol.OPS)
    assert not (rig.mailbox / "request.lua").exists()

    state = rig.bridge.state()
    assert state["project"] == "시험 프로젝트" and state["timeline"] == "타임라인 1"
    assert state["fps"] == "29.97" and state["drop_frame"] is False
    assert (state["start_frame"], state["start_tc"], state["current_tc"]) == (86400, "01:00:00:00", "01:00:10:00")
    assert state["tracks"] == {"video": 1, "audio": 0, "subtitle": 0}
    [clip] = state["items"]["video"]
    assert (clip["path"], clip["start"], clip["end"], clip["track"]) == (CLIP_PATH, 86400, 87300, 1)
    assert state["items"]["audio"] == [] and state["truncated"] is False

    # 한글, 따옴표, ]], 역슬래시, 줄바꿈이 섞인 이름이 코드로 실행되지 않고 그대로 도착한다
    hostile = '"}) resolve:GetProjectManager() -- ]] 한글 \\ \r\n 끝'
    marker = rig.bridge.add_marker(300, name=hostile, note="메모 ]]", custom="aih_test")
    assert marker["added"] is True and marker["frame"] == 300
    assert rig.bridge.get_markers()["markers"] == [
        {"frame": 300, "color": "Yellow", "name": hostile, "note": "메모 ]]", "duration": 1, "custom": "aih_test"}
    ]

    tone = make_test_tone(seconds=steps.TONE_FILE_SECONDS)
    assert tone.parent == rig.mailbox / "files"
    audio = rig.bridge.place_audio(tone, TRACK, 86400 + 300, 89)
    assert audio["imported"] is True and audio["track_index"] == 1 and audio["appended"] == 1
    assert (audio["item_start"], audio["item_end"]) == (86700, 86789)
    assert audio["clip"]["path"] == str(tone) and audio["clip"]["frames"] == "104"
    assert (audio["placed_frames"], audio["length_ok"]) == (89, True)
    state = rig.bridge.state()
    assert state["tracks"]["audio"] == 1
    [placed] = state["items"]["audio"]
    assert placed["path"] == str(tone) and placed["start"] == 86700

    again = rig.bridge.place_audio(tone, TRACK, 86400, 89)
    assert again["reused"] is True and again["track_index"] == 2  # 같은 파일은 다시 가져오지 않는다

    removed = rig.bridge.remove_audio(TRACK)
    assert removed["removed_tracks"] == 2 and removed["skipped"] == 0
    deleted = rig.bridge.delete_markers("aih_test")
    assert (deleted["deleted"], deleted["deleted_count"], deleted["remaining"]) == (True, 1, 0)
    assert rig.bridge.get_markers()["markers"] == []
    assert rig.bridge.state()["tracks"]["audio"] == 0

    assert rig.bridge.stop() == {"stopping": True}
    rig.join()  # stop 요청으로 스크립트가 스스로 끝남

    # 리졸브 쪽에 도착한 값
    assert rig.logged("AddMarker") == [[300, "Yellow", hostile, "메모 ]]", 1, "aih_test"]]
    assert rig.logged("ImportMedia") == [[str(tone), 1, "AI 도우미"]]
    appends = [_table(a[0]) for a in rig.logged("AppendToTimeline")]
    assert appends[0] == {
        "count": 1, "keys": "endFrame,mediaPoolItem,mediaType,recordFrame,startFrame,trackIndex",
        "item": "aih_test_tone.wav", "startFrame": 0, "endFrame": 89, "mediaType": 2,  # 들어가지 않는 끝
        "trackIndex": 1, "recordFrame": 86700,
    }
    assert rig.logged("SetTrackName") == [["audio", 1, TRACK], ["audio", 2, TRACK]]
    assert [a[:2] for a in rig.logged("DeleteTrack")] == [["audio", 2], ["audio", 1]]
    assert list(rig.fake.track_names("video").values()) == ["Video 1"]  # 사용자 트랙은 그대로
    # 처음 답을 받은 뒤 Fusion.prefs를 한 번 백업해 둔다
    assert list(rig.backup.glob("Fusion.prefs.*.bak"))


def test_request_waiting_before_the_click_is_answered(rig):
    """창이 먼저 물어보고 있을 때 사용자가 스크립트 메뉴를 누르면, 그 요청에 바로 답한다."""
    result: dict = {}
    asker = threading.Thread(target=lambda: result.update(ping=rig.bridge.ping(timeout=10)))
    asker.start()
    request = rig.mailbox / "request.lua"
    deadline = time.monotonic() + 5
    while not request.exists() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert request.exists()
    rig.start()
    asker.join(15)
    assert not asker.is_alive()
    assert result["ping"]["script_version"] == SCRIPT_VERSION


def test_timeout_before_the_click_then_connects(rig):
    with pytest.raises(BridgeTimeout) as info:
        rig.bridge.ping(timeout=0.3)
    assert "AI_Helper_Connect" in str(info.value)
    # 답을 못 받은 요청은 지워서, 나중에 메뉴를 눌렀을 때 뒤늦게 실행되지 않게 한다
    assert not (rig.mailbox / "request.lua").exists()
    rig.start()
    assert rig.bridge.ping(timeout=10)["owner"] == rig.bridge.owner


def test_paths_outside_files_folder_are_refused(rig):
    rig.start()
    files = rig.mailbox / "files"
    for path, code in (
        (rig.mailbox / "evil.wav", "bad_path:outside"),
        (files / ".." / "evil.wav", "bad_path:dotdot"),
        (files / "notes.txt", "bad_path:not_wav"),
    ):
        with pytest.raises(BridgeError) as info:
            rig.bridge.place_audio(path, TRACK, 86400, 10)
        assert (info.value.error, info.value.func) == (code, "path_guard")
    rig.bridge.stop()
    rig.join()
    assert rig.logged("ImportMedia") == [] and rig.logged("AddTrack") == []


def test_helper_steps_against_the_real_script(rig):
    """AI 도우미 창의 버튼 단계(steps.py)를 실제 스크립트에 대고 돌린다."""
    rig.start()
    out = steps.run_step(steps.connect_step, rig.bridge)
    assert out["state"]["timeline"] == "타임라인 1"
    assert steps.summarize("connect", out) == (True, 'DaVinci Resolve 21.1.0.0 / 프로젝트 "시험 프로젝트" / 타임라인 "타임라인 1"')

    out = steps.run_step(steps.marker_step, rig.bridge)
    # 재생 위치 01:00:10:00 - 시작 01:00:00:00 = 10초 = 300프레임 (29.97 논드롭)
    assert (out["offset"], out["offset_how"]) == (300, "playhead")
    assert out["marker"]["added"] is True and steps.summarize("marker", out)[0]

    # 29.97에서 3초 = 89.91프레임 → 89 (반올림한 90이면 3초짜리 소리 밖까지 달라고 해 거절된다)
    out = steps.run_step(steps.audio_step, rig.bridge)
    assert (out["record_frame"], out["frames"]) == (86700, 89)
    assert out["audio"]["appended"] == 1 and steps.summarize("audio", out)[0]
    assert out["audio"]["append_retry"] == "not_needed" and out["audio"]["track_named"] is True
    [placed] = out["state_after"]["items"]["audio"]  # 넣은 뒤 리졸브가 알려 주는 파일 경로
    assert placed["path"] == out["tone"] and placed["start"] == 86700

    out = steps.run_step(steps.cleanup_step, rig.bridge)
    assert out["delete_markers"]["deleted"] is True
    assert out["remove_audio"]["removed_tracks"] == 1
    assert out["state"]["tracks"]["audio"] == 0
    assert steps.summarize("cleanup", out) == (True, "시험 표시 1개 지움, 시험 트랙 1개 지움")
    assert out["markers_after"]["markers"] == []

    rig.bridge.stop()
    rig.join()
    [marker] = rig.logged("AddMarker")
    assert (marker[0], marker[1], marker[2], marker[5]) == (300, "Yellow", "AI 도우미 시험", "aih_test")


def test_helper_steps_without_open_project(rig):
    rig.fake.project_open = False
    rig.start()
    out = steps.run_step(steps.connect_step, rig.bridge)
    assert out["state"]["project"] is None
    with pytest.raises(steps.StepFailed) as info:
        steps.run_step(steps.marker_step, rig.bridge)
    assert isinstance(info.value.cause, steps.StepProblem)
    assert "프로젝트를 열어" in steps.explain(info.value.cause)
    assert "ping" in info.value.partial  # 실패 전까지 받은 답은 결과 파일에 남는다


def test_lua_failure_details_reach_the_report(rig):
    """리졸브 함수가 오류를 내면 그 오류 글과 함수별 결과(calls)가 결과 파일까지 간다."""
    rig.lua.execute(
        "local S = ...; S.pool.ImportMedia = function() error(\"bad argument #1 to 'ImportMedia' (table expected)\") end",
        rig.fake,
    )
    rig.start()
    steps.run_step(steps.connect_step, rig.bridge)
    with pytest.raises(steps.StepFailed) as info:
        steps.run_step(steps.audio_step, rig.bridge)
    cause = info.value.cause
    assert isinstance(cause, BridgeError) and cause.error == "import_failed"
    error = steps.error_info(cause)
    assert error["calls"]["MediaPool.ImportMedia"].startswith("err:")
    session = report.TestSession()
    session.record("audio", False, steps.explain(cause), info.value.partial, error)
    text = report.build_report(session, {"connected": True, "last_response": rig.bridge.last_response}, {})
    assert "bad argument #1 to 'ImportMedia' (table expected)" in text
    assert '"Folder.GetClipList": "ok"' in text
    assert "③ 소리 넣기: 안 됨 - 리졸브가 시험용 소리 파일을 가져오지 못했습니다." in text


def test_helper_steps_on_drop_frame_timeline(rig):
    """29.97 드롭 프레임 타임라인 (';' 타임코드): 재생 위치 10초 = 300프레임."""
    tl = rig.fake.timeline
    tl.start, tl.start_tc, tl.current_tc = 107892, "01:00:00;00", "01:00:10;00"
    tl.settings.timelineDropFrameTimecode = "1"
    rig.start()
    out = steps.run_step(steps.marker_step, rig.bridge)
    assert out["state"]["drop_frame"] is True and out["drop_frame_used"] is True
    assert (out["offset"], out["offset_how"]) == (300, "playhead")
    assert steps.summarize("marker", out)[0]
    out = steps.run_step(steps.audio_step, rig.bridge)
    assert out["record_frame"] == 107892 + 300 and out["audio"]["item_start"] == 108192


def test_panel_connect_and_probe_against_the_real_script(rig, tmp_path):
    """새 창의 연결 확인(+붙여 하는 읽기)과 [기능 점검] 단계를 실제 스크립트에 대고 돌린다."""
    pytest.importorskip("PySide6")
    import functools

    from app.companion import connection as conn
    from app.companion.window import probe_step
    from engine.resolve_link.caps import CapabilityStore
    from engine.resolve_link.probe import ProbeStateStore

    # 영상과 함께 붙은 소리 (C2 트랙 끄기, C3 클립 끄기, C6 다시 넣기에 필요). 원본은 충분히 길다.
    video = rig.fake.timeline.tracks.video[1]["items"][1]
    audio = rig.fake.add_clip("audio", 1, "촬영 원본.mp4", 86400, 900, None)
    audio.mpi = video.mpi
    rig.fake.link(video, audio)
    video.mpi.props["Frames"] = "100000"
    rig.start()
    caps_store = CapabilityStore(tmp_path / "caps")
    extras = [functools.partial(conn.extra_audio_items, known=None),
              functools.partial(conn.extra_probe_read, store=caps_store),
              functools.partial(conn.extra_reconcile, root=tmp_path)]
    out = steps.run_step(functools.partial(conn.panel_connect_step, extras=extras), rig.bridge)
    assert "extra_errors" not in out, out.get("extra_errors")
    assert out["state_kind"] == "timeline_info" and out["state"]["timeline"] == "타임라인 1"
    [item] = out["audio_items"]["items"]
    assert item["path"] == CLIP_PATH and out["probe_read"]["existence_reliable"] is True
    assert not out["caps"].needs_probe_read(SCRIPT_VERSION)

    seen = []
    probe_store = ProbeStateStore(tmp_path / "caps" / "probe_state.json")
    step = functools.partial(probe_step, store=probe_store, caps_store=caps_store,
                             on_stage=lambda stage, data: seen.append(stage))
    out = steps.run_step(step, rig.bridge)
    run = out["probe_run"]
    assert run["refused"] is None and not run["leftover"]
    assert [s for s in ("C1", "C2", "C3", "C4", "C5", "C6", "C7") if not run["stages"][s]["ok"]] == []
    assert all(run["stages"][s]["fingerprint"] for s in ("C1", "C2", "C3", "C4", "C5", "C6", "C7"))
    assert run["stages"]["C7"]["detail"]["imported"] is True
    assert run["cleanup"]["detail"]["deleted"] is True
    assert seen[0] == "read" and seen[-1] == "C8"
    assert probe_store.load() is None  # 정리했으니 남은 복사본 기록도 없다
    assert out["state"]["timeline"] == "타임라인 1"  # 원래 타임라인으로 돌아왔다
    caps = out["caps"]
    assert caps.has("fresh_import") is True and caps.has("exact_placement") is True


# ── 2.1b 자동화 버튼: 실제 스크립트에 대고 ─────────────────────────────

OBS_MAPPING = '{"track_mapping":{"1":{"channel_idx":[%d,%d],"mute":false,"type":"Stereo"}}}'


def _ops(rig):
    from engine.resolve_link.ops import ResolveOps
    from engine.resolve_link.transport import LuaTransport

    return ResolveOps(LuaTransport(rig.bridge))


def _user_marker(rig, frame: int, custom: str = "") -> None:
    rig.bridge.request("add_marker", {"frame": frame, "color": "Green", "name": "내 표시", "custom": custom})


@pytest.mark.usefixtures("obs_video")
def test_slot_one_end_to_end_and_undo(rig, tmp_path, obs_video):
    """OBS 녹화(4스트림)를 A1..A4에: 계산(읽기만) → 넣기 → 다시 읽기 → 되돌리기. 사용자 표시는 그대로."""
    from engine.analysis_cache import AnalysisCache
    from engine.automation.plan import PlanEnv, SlotRequest, VoiceMemory, VoiceOverride, VoiceQuestion, plan_slot
    from engine.edits.apply import apply_markers, undo_proposal
    from engine.edits.journal import key_for

    rig.lua.execute(
        "local S, path, fmt = ...\n"
        "for k = 1, 4 do\n"
        "  local it = S.add_clip('audio', k, 'obs.mp4', 86400, 899, path)\n"
        "  it.left, it.src = 0, 0\n"
        "  local text = string.format(fmt, 2 * k - 1, 2 * k)\n"
        "  it.GetSourceAudioChannelMapping = function() return text end\n"
        "end", rig.fake, str(obs_video), OBS_MAPPING)
    rig.start()
    rig.bridge.ping(timeout=10)
    _user_marker(rig, 5)
    _user_marker(rig, 330, "user")  # 쉬는 곳 한가운데에 사용자 표시
    ops = _ops(rig)
    env = PlanEnv(ops=ops, cache=AnalysisCache(tmp_path / "analysis"))
    req = SlotRequest(slot=1, kind="mark_pauses", name="쉬는 곳 표시", params={})
    marks: list = []
    rig.in_loop(lambda: marks.append(len(rig.fake.log)))
    q = plan_slot(req, env, VoiceMemory())
    assert isinstance(q, VoiceQuestion) and q.mix == 0
    p = plan_slot(req, env, VoiceMemory(), override=VoiceOverride(q.signature, 1))
    assert p.debug["mapping_methods"] == {"mapping": 4} and p.debug["mapping_conflicts"] == 0
    assert p.count == 2 and p.tl_start == 86400 and p.timeline["timeline"] == "타임라인 1"
    rig.in_loop(lambda: marks.append(len(rig.fake.log)))

    out = apply_markers(ops, p, root=tmp_path)
    assert out.status == "applied" and out.placed == 2 and out.dur_ok is True and out.receipt["readback"] is True
    ours = ops.get_markers(p.prefix)
    assert [m["frame"] for m in ours] == [s.frame for s in p.specs]
    assert [m["duration"] for m in ours] == [s.dur for s in p.specs]
    assert all(m["name"].startswith("쉼 ") and m["color"] == "Blue" for m in ours)

    key = key_for(ops.timeline_info())
    undo = undo_proposal(ops, p.id, root=tmp_path, journal_key=key)
    assert undo.status == "undone" and undo.deleted == 2
    left = ops.get_markers()
    assert sorted((m["frame"], m["custom"]) for m in left) == [(5, ""), (330, "user")]
    rig.bridge.stop()
    rig.join()
    # 계산하는 동안에는 리졸브에서 바꾸는 함수를 하나도 부르지 않았다 (가짜의 기록은 바꾸는 함수만 남긴다)
    log = rig.fake.log
    names = {log[i].name for i in range(marks[0] + 1, marks[1] + 1)}
    assert not names & {"AddMarker", "DeleteMarkerAtFrame", "DeleteMarkerByCustomData", "DeleteTrack",
                        "DeleteClips", "SetClipEnabled", "SetTrackEnable", "OpenPage", "SetCurrentTimecode"}
    assert [a[5] for a in rig.logged("AddMarker")][-2:] == [s.custom for s in p.specs]


def test_remove_all_ours_with_legacy_traces_on_the_color_page(rig, tmp_path):
    """1차 시험판 흔적(aih_test 표시 둘, "AI 도우미 시험" 트랙) + aih: 표시. 색 보정 화면에서 [바꿔서 빼기]."""
    from engine.edits.apply import LEGACY_TEST_TRACK, remove_all_ours, scan_ours
    from engine.resolve_link.ops import MarkerSpec

    rig.start()
    rig.bridge.ping(timeout=10)
    ops = _ops(rig)
    _user_marker(rig, 5)
    rig.bridge.add_marker(300, name="AI 도우미 시험", custom="aih_test")
    rig.bridge.add_marker(310, name="AI 도우미 시험", custom="aih_test")
    ops.add_markers([MarkerSpec(frame=600, dur=30, color="Blue", name="쉼", note="", custom="aih:Pold:1")])
    tone = make_test_tone(seconds=steps.TONE_FILE_SECONDS)
    rig.bridge.place_audio(tone, LEGACY_TEST_TRACK, 86400 + 300, 89)
    rig.in_loop(lambda: setattr(rig.fake, "page", "color"))

    scan = scan_ours(ops)
    assert (scan.markers, scan.legacy_markers, len(scan.legacy_tracks), scan.page) == (1, 2, 1, "color")
    assert scan.needs_edit_page
    # 편집 화면이 아니면 트랙은 묻지 않고는 빼지 않는다
    out = remove_all_ours(ops, scan, root=tmp_path)
    assert (out.markers_deleted, out.legacy_deleted, out.track_skipped) == (1, 2, "need_edit_page")
    assert rig.bridge.state()["tracks"]["audio"] == 1
    out = remove_all_ours(ops, scan_ours(ops), root=tmp_path, switch_page=True)
    assert out.track["removed_tracks"] == 1 and out.track["switched_page"] is True
    assert rig.bridge.state()["tracks"]["audio"] == 0
    assert [(m["frame"], m["custom"]) for m in ops.get_markers()] == [(5, "")]
    rig.bridge.stop()
    rig.join()
    assert [x[0] for x in rig.logged("OpenPage")] == ["edit", "color"]
