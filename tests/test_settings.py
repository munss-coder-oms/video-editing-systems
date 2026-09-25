"""도우미 창 설정 파일 (engine/settings.py)."""

from __future__ import annotations

import json

import pytest

from engine.settings import SCHEMA_VERSION, Settings, default_settings


def test_defaults_and_slot_order(tmp_path):
    s = Settings(tmp_path / "settings.json")
    assert [x["slot"] for x in s.slots] == [1, 2, 3]
    assert [x["kind"] for x in s.slots] == ["mark_pauses", "mark_spikes", "balance_voice"]
    assert s.data["chat"]["range_edit_mode"] is None  # 답이 없으면 기본값도 없다
    assert not (tmp_path / "settings.json").exists()  # 읽기만으로는 파일을 만들지 않는다


def test_round_trip_and_atomic_save(tmp_path):
    path = tmp_path / "settings.json"
    s = Settings(path)
    s.set_ui("text_scale", 130)
    assert not path.with_name("settings.json.tmp").exists()
    again = Settings(path)
    assert again.ui["text_scale"] == 130
    with pytest.raises(ValueError):
        s.set_ui("text_scale", 120)


def test_migration_from_version_0(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"ui": {"text_scale": 115}}), encoding="utf-8")
    s = Settings(path)
    assert s.data["schema_version"] == SCHEMA_VERSION
    assert s.ui["text_scale"] == 115 and len(s.slots) == 3
    assert s.moved_bad is None


@pytest.mark.parametrize("content", ["{not json", "[]", json.dumps({"schema_version": 1, "automations": {"slots": []}}),
                                      json.dumps({"schema_version": "x"})])
def test_corrupt_file_is_moved_aside(tmp_path, content):
    path = tmp_path / "settings.json"
    path.write_text(content, encoding="utf-8")
    s = Settings(path, clock=lambda: 1_759_000_000)
    assert s.data == default_settings()
    assert s.moved_bad is not None and s.moved_bad.name.startswith("settings.json.bad-")
    assert s.moved_bad.read_text(encoding="utf-8") == content
    assert not path.exists()


def test_newer_schema_loads_defaults_without_touching_values(tmp_path):
    path = tmp_path / "settings.json"
    newer = {"schema_version": SCHEMA_VERSION + 1, "ui": {"text_scale": 130}}
    path.write_text(json.dumps(newer), encoding="utf-8")
    s = Settings(path)
    assert s.ui["text_scale"] == 100
    assert json.loads(s.moved_bad.read_text(encoding="utf-8")) == newer


def test_slot_reorder_is_display_order(tmp_path):
    path = tmp_path / "settings.json"
    s = Settings(path)
    s.move_slot(3, 0)
    assert [x["slot"] for x in s.slots] == [3, 1, 2]
    s.move_slot(1, 99)
    assert [x["slot"] for x in Settings(path).slots] == [3, 2, 1]


def test_update_slot_keeps_previous_and_restores(tmp_path):
    path = tmp_path / "settings.json"
    s = Settings(path)
    s.update_slot(1, {"min_s": 2.0})
    slot = Settings(path).slot(1)
    assert slot["params"]["min_s"] == 2.0 and slot["previous"]["params"]["min_s"] == 1.5
    s.update_slot(1, {"min_s": 2.0})  # 같은 값: previous를 덮지 않는다
    assert s.slot(1)["previous"]["params"]["min_s"] == 1.5
    assert s.restore_previous(1) is True
    slot = Settings(path).slot(1)
    assert slot["params"]["min_s"] == 1.5 and slot["previous"] is None
    assert s.restore_previous(1) is False


def test_save_slot_drops_range_and_restore_brings_back_kind(tmp_path):
    s = Settings(tmp_path / "settings.json")
    s.save_slot(3, "mark_pauses", "쉬는 곳 (2초)", {"min_s": 2.0, "range": [300, 360], "start_s": 300, "end_s": 360})
    slot = s.slot(3)
    assert slot["kind"] == "mark_pauses" and slot["params"] == {"min_s": 2.0}
    assert slot["previous"]["kind"] == "balance_voice"
    s.restore_previous(3)
    assert s.slot(3)["kind"] == "balance_voice" and s.slot(3)["name"] == "소리 고르게"


def test_configure_slot_keeps_one_previous_step(tmp_path):
    s = Settings(tmp_path / "settings.json")
    same = s.slot(1)
    s.configure_slot(1, same["kind"], same["name"], dict(same["params"]))
    assert s.slot(1).get("previous") is None and not (tmp_path / "settings.json").exists()  # 달라진 것 없음
    s.configure_slot(1, "mark_pauses", "쉬는 곳 표시", dict(same["params"], min_s=2.0))
    assert s.slot(1)["params"]["min_s"] == 2.0 and s.slot(1)["previous"]["params"]["min_s"] == 1.5
    s.configure_slot(1, "mark_spikes", "튀는 소리", {"above_lu": 10.0})
    assert s.slot(1)["previous"]["kind"] == "mark_pauses" and s.slot(1)["previous"]["params"]["min_s"] == 2.0
    again = Settings(tmp_path / "settings.json")
    assert again.slot(1)["kind"] == "mark_spikes"
    assert again.restore_previous(1) is True and again.slot(1)["params"]["min_s"] == 2.0
    assert again.restore_previous(1) is False  # 한 단계만


def test_voice_choice_by_layout_and_perf(tmp_path):
    s = Settings(tmp_path / "settings.json", clock=lambda: 1_800_000_000.0)
    assert s.voice_choice("a4:2,2,2,2:|||") is None and s.voice_profile("a4:2,2,2,2:|||") is None
    s.set_voice("a4:2,2,2,2:|||", 1, mix=0, profile={"typical": -24.0})
    s.set_voice("a2:2,1:|", 0)
    again = Settings(tmp_path / "settings.json")
    c = again.voice_choice("a4:2,2,2,2:|||")
    assert (c["stream"], c["mix"]) == (1, 0) and c["at"].startswith("20")
    assert again.voice_profile("a4:2,2,2,2:|||") == {"typical": -24.0}
    assert again.voice_choice("a2:2,1:|")["stream"] == 0 and again.voice_profile("a2:2,1:|") is None
    assert again.sec_per_min("mark_pauses") is None
    again.record_perf("mark_pauses", 4.25)
    again.record_perf("mark_spikes", 0.0)  # 0초는 적지 않는다
    third = Settings(tmp_path / "settings.json")
    assert third.sec_per_min("mark_pauses") == 4.25 and third.sec_per_min("mark_spikes") is None


def test_broken_voice_section_is_rebuilt(tmp_path):
    path = tmp_path / "settings.json"
    data = default_settings()
    data["voice"] = "깨짐"
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    s = Settings(path)
    assert s.voice_choice("x") is None
    s.set_voice("x", 2)
    assert Settings(path).voice_choice("x")["stream"] == 2


def test_legacy_test_track_name_is_shared():
    from app.companion import steps
    from engine.edits.apply import LEGACY_TEST_TRACK

    assert steps.TEST_NAME == LEGACY_TEST_TRACK
