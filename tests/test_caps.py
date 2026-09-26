"""기능 점검 기록 (engine/resolve_link/caps.py)."""

from __future__ import annotations

from engine.resolve_link.caps import CAPS, CapabilityStore

READ = {
    "exists": {"Timeline.GetMarkInOut": True, "Timeline.GetSelectedClips": False, "Timeline.GetUniqueId": True},
    "existence_reliable": True, "props_keys": {"video": ["Pan"], "audio": None},
    "project_uid": "p", "timeline_uid": "t", "source_audio_mapping": [{"track": 1, "mapping": "{}"}],
    "calls": {"x": "ok"},
}


def test_version_change_triggers_probe_read(tmp_path):
    store = CapabilityStore(tmp_path)
    caps = store.load("DaVinci Resolve", "21.1.0.0")
    assert caps.needs_probe_read("1.1.0") is True
    store.record_read("DaVinci Resolve", "21.1.0.0", "1.1.0", READ)
    assert store.load("DaVinci Resolve", "21.1.0.0").needs_probe_read("1.1.0") is False
    # 리졸브를 올리면 다른 파일: 다시 점검
    assert store.load("DaVinci Resolve", "21.1.1.2").needs_probe_read("1.1.0") is True
    # 스크립트 판이 바뀌어도 다시
    assert store.load("DaVinci Resolve", "21.1.0.0").needs_probe_read("1.2.0") is True
    assert store.path_for("DaVinci Resolve", "21.1.0.0").name == "resolve-DaVinci_Resolve-21.1.0.0.json"


def test_gated_options(tmp_path):
    store = CapabilityStore(tmp_path)
    caps = store.record_read("R", "1", "1.1.0", READ)
    assert caps.has("in_out") is True and caps.has("selected_clips") is False and caps.has("unique_id") is True
    assert caps.has("jump") is None and not caps.allowed("jump")  # 점검 전에는 켜지 않는다
    stages = {
        "C2": {"ok": True, "detail": {}}, "C3": {"ok": False, "detail": {}},
        "C4": {"ok": True, "detail": {"range_ok": False, "left": 0, "point_only": True}},
        "C5": {"ok": False, "error": "BridgeTimeout"},
        "C7": {"ok": True, "detail": {"imported": True, "length_ok": True}},
        "C8": {"ok": True, "detail": {"clip_deleted": False, "deleted": True}},
    }
    caps = store.record_copy("R", "1", "1.1.0", stages)
    assert caps.allowed("track_enable") and caps.has("clip_enable") is False
    assert caps.has("range_markers") is False and caps.allowed("marker_delete")
    assert caps.has("jump") is None  # 시간 초과: 모름
    assert caps.allowed("fresh_import") and caps.allowed("exact_placement")
    assert caps.has("delete_media_clip") is False and caps.has("multi_stream_append") is None
    assert set(caps.missing()) >= {"jump", "range_markers", "multi_stream_append"}
    assert len(caps.summary_lines()) == len(CAPS)
    reloaded = store.load("R", "1")
    assert reloaded.copy["C4"]["detail"]["point_only"] is True and "calls" not in reloaded.read


def test_unreliable_existence_means_unknown(tmp_path):
    store = CapabilityStore(tmp_path)
    caps = store.record_read("R", "1", "1.1.0", dict(READ, existence_reliable=False))
    assert caps.has("in_out") is None


def test_corrupt_caps_file_is_ignored(tmp_path):
    store = CapabilityStore(tmp_path)
    path = store.path_for("R", "1")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{", encoding="utf-8")
    assert store.load("R", "1").needs_probe_read("1.1.0")
    store.record_manual("R", "1", "1.1.0", "M3", "모두 사라짐")
    assert store.load("R", "1").manual["M3"]["answer"] == "모두 사라짐"
