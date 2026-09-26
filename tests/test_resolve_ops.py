"""ResolveOps(결과 모양), LuaTransport, 리졸브 프로세스 확인 (engine/resolve_link/ops.py, transport.py, process.py)."""

from __future__ import annotations

import pytest

from engine.resolve_link import process
from engine.resolve_link.ops import Item, ResolveOps, ScopeInfo, TimelineInfo, is_our_custom
from engine.resolve_link.transport import LuaTransport


class Recorder:
    name = "rec"

    def __init__(self, answers):
        self.answers = answers
        self.sent = []

    def request(self, op, args=None, *, timeout=None, cancel=None):
        self.sent.append((op, dict(args or {})))
        a = self.answers[op]
        return a(args or {}) if callable(a) else a

    def supports(self, op):
        return True


def test_timeline_info_shape():
    r = {"project": "p", "project_uid": "pu", "timeline": "AI 도우미 점검용 140210", "timeline_uid": "tu",
         "start_frame": 216000, "end_frame": 283570, "fps": "60", "drop_frame": False, "page": "edit",
         "tracks": {"video": [{"index": 1, "name": "V1", "enabled": True, "locked": False, "count": 1}],
                    "audio": [{"index": i, "name": f"A{i}", "enabled": True, "locked": False, "subtype": "stereo",
                               "count": 1} for i in (1, 2, 3, 4)], "subtitle": []}}
    info = ResolveOps(Recorder({"timeline_info": r})).timeline_info()
    assert info.length_frames == 67570 and info.duration_seconds == pytest.approx(1126.1667, abs=1e-3)
    assert info.is_probe_copy and info.track_count("audio") == 4 and info.tracks["audio"][3].name == "A4"
    legacy = TimelineInfo.from_state({"project": "p", "timeline": "t", "tracks": {"video": 1, "audio": 2}})
    assert legacy.track_count("audio") == 2 and not legacy.is_probe_copy


def test_timeline_items_follow_pages():
    def page(args):
        off = args["offset"]
        rows = [{"uid": f"i{n}", "kind": "audio", "track": 1, "start": n, "end": n + 1, "linked_uids": ["v"]}
                for n in range(off, min(off + 100, 250))]
        return {"items": rows, "next": off + 100 if off + 100 < 250 else None}

    rec = Recorder({"timeline_items": page})
    items = ResolveOps(rec).timeline_items("audio", track_from=1)
    assert len(items) == 250 and isinstance(items[0], Item) and items[0].linked_uids == ["v"]
    assert [a["offset"] for _, a in rec.sent] == [0, 100, 200] and rec.sent[0][1]["track_from"] == 1


def test_scope_playhead_frame_from_timecode():
    s = ScopeInfo.from_result({"start_frame": 216000, "start_tc": "01:00:00:00", "playhead_tc": "01:03:20:00",
                               "fps": "60", "drop_frame": False, "in_out": None, "selected_uids": None})
    assert s.playhead_frame == 216000 + 200 * 60 and s.selected_uids is None
    df = ScopeInfo.from_result({"start_frame": 107892, "start_tc": "01:00:00;00", "playhead_tc": "01:00:10;00",
                                "fps": "29.97", "drop_frame": True})
    assert df.playhead_frame == 107892 + 300
    assert ScopeInfo.from_result({"playhead_tc": None}).playhead_frame is None


def test_delete_markers_guard_in_python():
    rec = Recorder({"delete_markers": {"deleted": True}})
    ops = ResolveOps(rec)
    for bad in ("autosubs_1", "", "aih", "AIH:x"):
        with pytest.raises(ValueError):
            ops.delete_markers(bad)
    assert rec.sent == []
    ops.delete_markers("aih:P1:1")
    ops.delete_markers("aih_test")
    assert [a["custom"] for _, a in rec.sent] == ["aih:P1:1", "aih_test"]
    assert is_our_custom("aih:x") and not is_our_custom(None)


def test_probe_copy_and_switch_args():
    rec = Recorder({"probe_copy": {"stage": "C8", "ok": True, "detail": {"deleted": True}, "fingerprint": None},
                    "switch_timeline": {"switched": True}})
    ops = ResolveOps(rec)
    r = ops.probe_copy("C8", expect_fingerprint="1:2:3", copy_uid=None)
    assert r.ok and r.detail["deleted"] and rec.sent[0][1] == {"stage": "C8", "expect_fingerprint": "1:2:3"}
    with pytest.raises(ValueError):
        ops.probe_copy("C9")
    with pytest.raises(ValueError):
        ops.switch_timeline()
    ops.switch_timeline(uid="tl-1")
    assert rec.sent[-1] == ("switch_timeline", {"uid": "tl-1", "name": None})


def test_lua_transport_passes_cancel_and_timeout():
    class B:
        def __init__(self):
            self.calls = []

        def request(self, op, args=None, timeout=None, cancel=None):
            self.calls.append((op, args, timeout, cancel))
            return {"ok": 1}

        def supports(self, op):
            return op == "ping"

    b = B()
    t = LuaTransport(b)
    ev = object()
    assert t.request("scope", {"x": 1}, timeout=3, cancel=ev) == {"ok": 1}
    assert b.calls == [("scope", {"x": 1}, 3, ev)] and t.supports("ping") and not t.supports("scope")


def test_tasklist_parsing_and_platforms():
    found = '"Resolve.exe","12345","Console","1","1,234,567 K"\r\n'
    none = "정보: 지정된 조건에 맞는 작업이 실행되고 있지 않습니다.\r\n"
    assert process.parse_tasklist(found) is True
    assert process.parse_tasklist(none) is False
    assert process.parse_tasklist('"resolve.EXE","1"') is True
    assert process.resolve_running(runner=lambda n: found, platform="win32") is True
    assert process.resolve_running(runner=lambda n: none, platform="win32") is False
    assert process.resolve_running(runner=lambda n: None, platform="win32") is None
    assert process.resolve_running(runner=lambda n: found, platform="linux") is None


@pytest.mark.parametrize("fps", [30, 30.0, "30"])
def test_numeric_clip_fps_is_kept(fps):
    """리졸브가 클립 속도를 숫자(30)로 줘도 버리지 않는다: 60fps 타임라인의 30fps 클립이 '속도 바꿈'으로 빠지지 않게."""
    from engine.timeline.map import item_windows

    row = {"uid": "a", "kind": "audio", "track": 1, "start": 1000, "end": 4600, "duration": 3600,
           "left_offset": 600, "source_start": 300, "source_end": 2100, "clip_fps": fps}
    item = Item.from_row(row)
    assert item.clip_fps == "30"
    check = item_windows([item], 60.0)
    assert len(check.windows) == 1 and check.refused_speed == []
    assert Item.from_row(dict(row, clip_fps=29.97)).clip_fps == "29.97"
    assert Item.from_row(dict(row, clip_fps=True)).clip_fps is None


def test_get_markers_follow_pages_and_stop_at_limit():
    """표시가 많으면 Lua가 나눠 준다(next): 이어서 받고, limit을 채우면 멈춘다."""
    rows = [{"frame": n, "custom": f"aih:P1:{n}", "color": "Blue"} for n in range(1, 251)]

    def page(args):
        off, lim = args.get("offset", 0), args.get("limit", 2000)
        take = min(lim, 100)
        chunk = rows[off:off + take]
        nxt = off + len(chunk) if off + len(chunk) < len(rows) else None
        return {"markers": chunk, "total": len(rows), "next": nxt}

    rec = Recorder({"get_markers": page})
    got, total = ResolveOps(rec).read_markers("aih:")
    assert len(got) == 250 and total == 250
    assert [a.get("offset", 0) for _, a in rec.sent] == [0, 100, 200]
    rec2 = Recorder({"get_markers": page})
    got, total = ResolveOps(rec2).read_markers("aih:", limit=150)
    assert len(got) == 150 and total == 250 and len(rec2.sent) == 2
    rec3 = Recorder({"get_markers": page})
    assert ResolveOps(rec3).marker_total("aih:") == 250 and len(rec3.sent) == 1
    # 예전 스크립트(next 없음)는 한 번에
    rec4 = Recorder({"get_markers": {"markers": rows[:5], "total": 5}})
    assert len(ResolveOps(rec4).get_markers("aih:")) == 5 and len(rec4.sent) == 1
