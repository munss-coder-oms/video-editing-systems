"""기능 점검(ProbeRunner)과 남은 점검용 복사본 (engine/resolve_link/probe.py).

실제 Lua 스크립트(가짜 리졸브, tests/test_lua_script.py)에 대고 돌린다. lupa가 없으면 그 부분은 건너뛴다.
"""

from __future__ import annotations

import json
import wave
from pathlib import Path
from types import SimpleNamespace

import pytest

from engine.resolve_link.bridge import BridgeError, BridgeTimeout
from engine.resolve_link.ops import ResolveOps, TimelineInfo
from engine.resolve_link.probe import (
    PROBE_SAMPLES,
    ProbeRunner,
    ProbeState,
    ProbeStateStore,
    c5_timecode,
    delete_leftover,
    leftover_name,
    make_probe_wav,
    probe_frames,
    wav_samples,
)


class HarnessTransport:
    """test_lua_script.Harness를 Transport처럼 (요청 파일 → 실제 Lua → 답)."""

    name = "harness"

    def __init__(self, h, fail_at=None):
        self.h = h
        self.sent = []
        self.fail_at = fail_at or {}

    def request(self, op, args=None, *, timeout=None, cancel=None):
        stage = (args or {}).get("stage")
        self.sent.append(stage or op)
        hook = self.fail_at.get(stage or op)
        if hook is not None:
            hook()
            raise BridgeTimeout(op)
        args = {k: v for k, v in (args or {}).items() if v is not None}  # 실제 요청도 None은 뺀다
        res = self.h.op(op, args)
        if not res.get("ok"):
            raise BridgeError(res.get("error"), res.get("func"), op, payload=res)
        return res["result"]

    def supports(self, op):
        return True


@pytest.fixture
def lua(tmp_path):
    pytest.importorskip("lupa.luajit21")
    from tests.test_lua_script import Harness, _obs_timeline

    h = Harness(tmp_path)
    _obs_timeline(h, 2)
    h.fake.media_frames = lambda path: 200 if path.endswith(".wav") else 100000
    h.init()
    store = ProbeStateStore(tmp_path / "caps" / "probe_state.json")
    return SimpleNamespace(h=h, store=store, tmp=tmp_path)


def _runner(env, transport, **kw):
    folder = env.tmp / "bridge" / "files" / "probe"
    return ProbeRunner(ResolveOps(transport), env.store, wav_maker=lambda: make_probe_wav(folder), **kw)


def test_probe_wav_is_exact_and_frames_come_from_samples(tmp_path):
    p = make_probe_wav(tmp_path, now=0)
    assert p.name.startswith("probe_") and p.suffix == ".wav"
    assert wav_samples(p) == PROBE_SAMPLES == 96000
    with wave.open(str(p), "rb") as w:
        assert (w.getframerate(), w.getnchannels(), w.getsampwidth()) == (48000, 2, 2)
    assert make_probe_wav(tmp_path, now=0) != p  # 같은 초에 다시 만들어도 새 이름
    assert probe_frames(96000, 48000, 60.0) == 120
    assert probe_frames(96000, 48000, 29.97) == 59
    assert probe_frames(96000, 48000, 23.976) == 47


def test_c5_timecode():
    info = TimelineInfo.from_result({"timeline": "t", "start_tc": "01:00:00:00", "fps": "60", "start_frame": 216000,
                                     "end_frame": 283570, "drop_frame": False})
    assert c5_timecode(info) == "01:00:10:00"
    df = TimelineInfo.from_result({"timeline": "t", "start_tc": "01:00:00;00", "fps": "29.97", "drop_frame": True,
                                   "start_frame": 0, "end_frame": 100000})
    assert c5_timecode(df) == "01:00:10;00"
    short = TimelineInfo.from_result({"timeline": "t", "start_tc": "00:00:00:00", "fps": "30", "start_frame": 0,
                                      "end_frame": 60})
    assert c5_timecode(short) == "00:00:01:00"


def test_full_probe_leaves_nothing(lua):
    t = HarnessTransport(lua.h)
    seen = []
    run = _runner(lua, t, on_stage=lambda s, d: seen.append(s)).run(suffix="140210")
    assert run.refused is None and run.leftover is False
    assert [s for s in t.sent if s.startswith("C")] == ["C1", "C2", "C3", "C4", "C5", "C6", "C7", "C8"]
    assert seen == ["read", "C1", "C2", "C3", "C4", "C5", "C6", "C7", "C8"]
    for stage in ("C1", "C2", "C3", "C4", "C5", "C6", "C7"):
        assert run.stages[stage]["ok"] is True, (stage, run.stages[stage])
        assert run.stages[stage]["fingerprint"]
    assert run.stages["C7"]["detail"]["imported"] is True
    assert run.stages["C7"]["detail"]["placed_frames"] == run.wav["frames"] == 59
    assert run.cleanup["detail"]["deleted"] is True and run.last_fp == run.stages["C7"]["fingerprint"]
    assert not lua.store.path.exists()  # 지웠으니 기록도 지운다
    assert run.read["exists"]["Timeline.DuplicateTimeline"] is True
    assert set(run.seconds) >= {"read", "C1", "C8"}


def test_stop_at_c3_with_item_left_disabled_keeps_copy(lua):
    h = lua.h

    def leave_disabled():
        h.lua.execute("local S = ...; S.timeline.tracks.audio[1].items[1].enabled = false", h.fake)

    t = HarnessTransport(h, fail_at={"C3": leave_disabled})
    run = _runner(lua, t).run(suffix="140210")
    assert run.stages["C3"]["error"] == "BridgeTimeout"
    assert "C4" not in run.stages  # 멈춘 뒤에는 정리(C8)만
    assert run.last_fp == run.stages["C2"]["fingerprint"]
    assert run.cleanup["detail"]["reason"] == "fingerprint_mismatch" and run.leftover is True
    state = lua.store.load()
    assert state.last_fp == run.stages["C2"]["fingerprint"] and state.copy_name == "AI 도우미 점검용 140210"
    assert "AI 도우미 점검용 140210" in [h.fake.timelines[i].name for i in range(1, len(h.fake.timelines) + 1)]
    # 확인해도 지문이 달라 지우지 않는다
    r = delete_leftover(ResolveOps(t), lua.store, confirmed=True)
    assert r.deleted is False and r.reason == "fingerprint_mismatch"
    assert lua.store.load() is not None


def test_stop_at_c3_after_restore_deletes_after_confirm(lua):
    h = lua.h
    t = HarnessTransport(h, fail_at={"C3": lambda: None})  # C3이 되돌린 뒤 답이 끊긴 것처럼
    t.fail_at["C8"] = lambda: None  # 정리 요청도 답이 없음 → 복사본이 남는다
    run = _runner(lua, t).run(suffix="140210")
    assert run.leftover is True and run.cleanup["error"] == "BridgeTimeout"
    info = ResolveOps(HarnessTransport(h)).timeline_info()
    assert leftover_name(info, lua.store.load()) == "AI 도우미 점검용 140210"  # 머리말 경고
    ops = ResolveOps(HarnessTransport(h))
    assert delete_leftover(ops, lua.store, confirmed=False).reason == "not_confirmed"
    names = [h.fake.timelines[i].name for i in range(1, len(h.fake.timelines) + 1)]
    assert "AI 도우미 점검용 140210" in names  # 확인 전에는 그대로
    r = delete_leftover(ops, lua.store, confirmed=True)
    assert r.deleted is True and lua.store.load() is None
    assert [h.fake.timelines[i].name for i in range(1, len(h.fake.timelines) + 1)] == ["타임라인 1"]


def test_missing_probe_state_never_deletes(lua):
    h = lua.h
    t = HarnessTransport(h, fail_at={"C3": lambda: None, "C8": lambda: None})
    _runner(lua, t).run(suffix="140210")
    lua.store.clear()
    r = delete_leftover(ResolveOps(HarnessTransport(h)), lua.store, confirmed=True)
    assert r.deleted is False and r.reason == "no_state"
    assert len(h.fake.timelines) == 2


def test_probe_refuses_on_copy_and_with_leftover(lua):
    h = lua.h
    t = HarnessTransport(h, fail_at={"C8": lambda: None})
    run = _runner(lua, t).run(suffix="140210")
    assert run.leftover is True
    # 복사본이 열려 있으면 새 점검을 하지 않는다
    again = _runner(lua, HarnessTransport(h)).run(suffix="140211")
    assert again.refused == "on_probe_copy"
    # 원래 타임라인으로 돌아가도, 남은 복사본 기록이 있으면 먼저 정리하게 한다
    ResolveOps(HarnessTransport(h)).switch_timeline(uid=lua.store.load().original_uid)
    again = _runner(lua, HarnessTransport(h)).run(suffix="140212")
    assert again.refused == "leftover" and len(h.fake.timelines) == 2


def test_probe_state_store_round_trip(tmp_path):
    store = ProbeStateStore(tmp_path / "caps" / "probe_state.json")
    assert store.load() is None
    store.save(ProbeState(original_uid="a", copy_uid="b", copy_name="AI 도우미 점검용 1", last_fp="1:2:3"))
    loaded = store.load()
    assert loaded.copy_uid == "b" and loaded.last_fp == "1:2:3" and loaded.updated_at
    assert json.loads(store.path.read_text(encoding="utf-8"))["probe_version"] == 1
    store.path.write_text("{", encoding="utf-8")
    assert store.load() is None


def test_delete_leftover_without_state_sends_nothing(tmp_path):
    class NoCalls:
        name = "none"

        def request(self, *a, **k):
            raise AssertionError("보내면 안 됨")

        def supports(self, op):
            return True

    store = ProbeStateStore(tmp_path / "probe_state.json")
    assert delete_leftover(ResolveOps(NoCalls()), store, confirmed=True).reason == "no_state"
    store.save(ProbeState(copy_uid="x", copy_name="AI 도우미 점검용 1"))  # 지문이 없는 기록
    assert delete_leftover(ResolveOps(NoCalls()), store, confirmed=True).reason == "no_fingerprint"


# ---------------------------------------------------------------------------
# 남은 복사본 지우기는 지금 보는 타임라인·화면·재생 위치를 건드리지 않는다 (검토 반영)
# ---------------------------------------------------------------------------

def _names(h):
    return [h.fake.timelines[i].name for i in range(1, len(h.fake.timelines) + 1)]


def _fresh_log(h):
    h.fake.log = h.lua.table()


def test_leftover_delete_keeps_the_users_timeline_page_and_playhead(lua):
    """점검 뒤 사용자가 다른 타임라인·색 화면에서 일하는 중: [지우기]는 그 타임라인·화면을 바꾸지 않는다."""
    h = lua.h
    t = HarnessTransport(h, fail_at={"C8": lambda: None})  # 정리 답이 끊겨 복사본이 남음
    run = _runner(lua, t).run(suffix="140210")
    assert run.leftover is True
    ops = ResolveOps(HarnessTransport(h))
    ops.switch_timeline(uid=lua.store.load().original_uid)
    other = h.fake.add_timeline("타임라인 2")
    h.lua.execute("local S, t = ...; S.timeline = t; S.page = 'color'; t.current_tc = '01:02:03:04'", h.fake, other)
    _fresh_log(h)
    r = delete_leftover(ops, lua.store, confirmed=True)
    assert r.deleted is True and "AI 도우미 점검용 140210" not in _names(h)
    assert h.fake.timeline.name == "타임라인 2" and h.fake.page == "color"
    assert h.fake.timeline.current_tc == "01:02:03:04"
    assert h.logged("SetCurrentTimeline") == [] and h.logged("OpenPage") == []
    assert h.logged("SetCurrentTimecode") == []
    # 점검용 소리 클립은 복사본을 지운 뒤에 지운다
    assert len(h.logged("MediaPool.DeleteClips")) == 1 and r.detail["detail"]["clip_deleted"] is True


def test_leftover_delete_on_the_original_timeline_keeps_page_and_playhead(lua):
    """원래 타임라인에서 페어라이트 화면·다른 재생 위치: 점검 때(C1)의 화면·재생 위치로 되돌리지 않는다."""
    h = lua.h
    h.lua.execute("local S = ...; S.timeline.current_tc = '01:00:10:00'", h.fake)
    t = HarnessTransport(h, fail_at={"C3": lambda: None, "C8": lambda: None})
    _runner(lua, t).run(suffix="140210")
    ops = ResolveOps(HarnessTransport(h))
    ops.switch_timeline(uid=lua.store.load().original_uid)
    h.lua.execute("local S = ...; S.page = 'fairlight'; S.timeline.current_tc = '01:03:00:00'", h.fake)
    _fresh_log(h)
    r = delete_leftover(ops, lua.store, confirmed=True)
    assert r.deleted is True
    assert h.fake.page == "fairlight" and h.fake.timeline.current_tc == "01:03:00:00"
    assert h.logged("OpenPage") == [] and h.logged("SetCurrentTimecode") == []
    assert h.logged("SetCurrentTimeline") == []


def test_leftover_delete_with_fingerprint_mismatch_changes_nothing(lua):
    """지문이 달라 지우지 않을 때도 타임라인·화면을 옮기지 않는다."""
    h = lua.h

    def leave_disabled():
        h.lua.execute("local S = ...; S.timeline.tracks.audio[1].items[1].enabled = false", h.fake)

    t = HarnessTransport(h, fail_at={"C3": leave_disabled})
    run = _runner(lua, t).run(suffix="140210")
    assert run.cleanup["detail"]["reason"] == "fingerprint_mismatch"
    other = h.fake.add_timeline("타임라인 2")
    h.lua.execute("local S, t = ...; S.timeline = t; S.page = 'color'", h.fake, other)
    _fresh_log(h)
    r = delete_leftover(ResolveOps(HarnessTransport(h)), lua.store, confirmed=True)
    assert r.deleted is False and r.reason == "fingerprint_mismatch"
    assert h.fake.timeline.name == "타임라인 2" and h.fake.page == "color"
    assert h.logged("SetCurrentTimeline") == [] and h.logged("OpenPage") == []


def test_leftover_copy_open_switches_only_to_the_recorded_original(lua):
    """복사본이 열려 있으면 (지울 수 있게) 적어 둔 원래 타임라인으로만 옮긴다. 화면·재생 위치는 그대로."""
    h = lua.h
    t = HarnessTransport(h, fail_at={"C8": lambda: None})
    _runner(lua, t).run(suffix="140210")
    assert h.fake.timeline.name == "AI 도우미 점검용 140210"
    h.lua.execute("local S = ...; S.page = 'fairlight'", h.fake)
    _fresh_log(h)
    r = delete_leftover(ResolveOps(HarnessTransport(h)), lua.store, confirmed=True)
    assert r.deleted is True and h.fake.timeline.name == "타임라인 1"
    assert [x[0] for x in h.logged("SetCurrentTimeline")] == ["타임라인 1"]
    assert h.fake.page == "fairlight" and h.logged("OpenPage") == [] and h.logged("SetCurrentTimecode") == []


def test_run_cleanup_keeps_probe_clip_while_the_copy_is_kept(lua):
    """C7의 답을 못 받아 복사본이 남으면, 그 복사본이 쓰는 점검용 클립도 지우지 않는다 (복사본을 바꾸지 않게)."""
    h = lua.h

    class LostC7(HarnessTransport):
        def request(self, op, args=None, *, timeout=None, cancel=None):
            res = super().request(op, args, timeout=timeout, cancel=cancel)
            if (args or {}).get("stage") == "C7":
                raise BridgeTimeout(op)  # 리졸브는 했는데 답이 끊김
            return res

    run = _runner(lua, LostC7(h)).run(suffix="140210")
    detail = run.cleanup["detail"]
    assert detail["deleted"] is False and detail["reason"] == "fingerprint_mismatch"
    assert detail["clip_deleted"] is None and detail["clip_kept"] is True
    assert h.logged("MediaPool.DeleteClips") == []
    copy = next(h.fake.timelines[i] for i in range(1, len(h.fake.timelines) + 1)
                if h.fake.timelines[i].name == "AI 도우미 점검용 140210")
    names = [copy.tracks.audio[i].name for i in range(1, len(copy.tracks.audio) + 1)]
    assert "AI 도우미 점검" in names
    state = lua.store.load()
    assert state.clip_path and state.has_copy  # 나중에 복사본을 지울 때 클립도 지울 수 있게 남긴다


def test_leftover_in_another_project_keeps_records_and_touches_nothing(lua):
    """다른 프로젝트가 열려 있으면 '없음'으로 보지 않는다: 기록을 지우지 않고, 원래 프로젝트에서 다시 지울 수 있다."""
    h = lua.h
    t = HarnessTransport(h, fail_at={"C8": lambda: None})
    _runner(lua, t).run(suffix="140210")
    state = lua.store.load()
    assert state.project_uid == "proj-1" and state.project_name == "시험 프로젝트"
    h.lua.execute("local S = ...; S.project.GetUniqueId = function() return 'proj-2' end; "
                  "S.project.name = '다른 프로젝트'", h.fake)
    _fresh_log(h)
    ops = ResolveOps(HarnessTransport(h))
    r = delete_leftover(ops, lua.store, confirmed=True)
    assert r.deleted is False and r.reason == "other_project"
    assert r.detail["detail"]["recorded_project"] == "시험 프로젝트"
    assert lua.store.load() is not None and len(h.fake.timelines) == 2
    assert h.logged("SetCurrentTimeline") == [] and h.logged("DeleteTimelines") == []
    assert h.logged("MediaPool.DeleteClips") == []
    # 원래 프로젝트로 돌아오면 지울 수 있다
    h.lua.execute("local S = ...; S.project.GetUniqueId = function() return 'proj-1' end; "
                  "S.project.name = '시험 프로젝트'", h.fake)
    r = delete_leftover(ops, lua.store, confirmed=True)
    assert r.deleted is True and lua.store.load() is None


def test_unreadable_timeline_list_is_not_treated_as_gone(lua):
    h = lua.h
    t = HarnessTransport(h, fail_at={"C8": lambda: None})
    _runner(lua, t).run(suffix="140210")
    ops = ResolveOps(HarnessTransport(h))
    ops.switch_timeline(uid=lua.store.load().original_uid)
    h.lua.execute("local S = ...; S.project.GetTimelineCount = function() return nil end", h.fake)
    r = delete_leftover(ops, lua.store, confirmed=True)
    assert r.deleted is False and r.reason == "timelines_unreadable"
    assert r.detail["detail"]["copy_found"] is None
    assert lua.store.load() is not None  # 기록은 그대로


def test_name_only_leftover_record_blocks_a_new_probe(lua):
    """복사본 번호(GetUniqueId)를 못 읽어 이름만 적힌 기록도 새 점검을 막는다 (기록을 덮어쓰지 않게)."""
    lua.store.save(ProbeState(copy_uid=None, copy_name="AI 도우미 점검용 120000", last_fp="1:2:3"))
    t = HarnessTransport(lua.h)
    run = _runner(lua, t).run(suffix="140210")
    assert run.refused == "leftover" and run.leftover is True
    assert "C1" not in t.sent
    assert lua.store.load().copy_name == "AI 도우미 점검용 120000"
