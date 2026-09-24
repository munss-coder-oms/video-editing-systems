from __future__ import annotations

import json

import pytest

from engine.commands import (
    AudioContext,
    Command,
    CommandError,
    Gain,
    commands_from_list,
    default_balance_commands,
    registered_ops,
)
from engine.project import PROJECT_SCHEMA_VERSION, MIGRATIONS, Project, ProjectError, migrate


def test_registered_ops():
    assert {"gain", "tame_peaks", "lift_quiet", "normalize_loudness"} <= set(registered_ops())


def test_roundtrip_through_json():
    cmds = default_balance_commands("weak") + [Gain(start=128, end=135, db=-6, reason="채팅: 웃음소리")]
    data = json.loads(json.dumps([c.to_dict() for c in cmds], ensure_ascii=False))
    again = commands_from_list(data)
    assert [c.to_dict() for c in again] == [c.to_dict() for c in cmds]


@pytest.mark.parametrize(
    "bad",
    [
        {"op": "nope"},
        {"op": "gain", "start": 5, "end": 2, "db": -3},
        {"op": "gain", "start": 0, "end": 2, "db": -100},
        {"op": "tame_peaks", "strength": "extreme"},
        {"op": "lift_quiet", "volume": 3},
        {"op": "normalize_loudness", "target_lufs": 0},
    ],
)
def test_invalid_commands_rejected(bad):
    with pytest.raises(CommandError):
        Command.from_dict(bad)


def test_filters_are_built():
    ctx = AudioContext(input_lufs=-30.0, duration=60)
    for cmd in default_balance_commands():
        f = cmd.audio_filter(ctx)
        assert f is None or isinstance(f, str)
    assert "volume" in Gain(start=1, end=2, db=-6).audio_filter(ctx)


def test_project_roundtrip(tmp_path):
    p = Project(source="C:/영상/a.mp4", commands=default_balance_commands(), media={"duration": 1.0})
    p.save(tmp_path)
    data = json.loads((tmp_path / "project.json").read_text(encoding="utf-8"))
    assert data["schema_version"] == PROJECT_SCHEMA_VERSION
    loaded = Project.load(tmp_path)
    assert loaded.source == p.source
    assert [c.to_dict() for c in loaded.commands] == [c.to_dict() for c in p.commands]


def test_project_from_newer_app_is_refused():
    with pytest.raises(ProjectError):
        migrate({"schema_version": PROJECT_SCHEMA_VERSION + 1, "source": "x"})


def test_project_migration_chain(monkeypatch):
    """옛 형식(v0)을 열면 변환 함수를 거쳐 현재 형식이 된다."""
    monkeypatch.setitem(MIGRATIONS, 0, lambda d: {**d, "source": d.pop("video")})
    data = migrate({"schema_version": 0, "video": "a.mp4"})
    assert data["schema_version"] == PROJECT_SCHEMA_VERSION
    assert data["source"] == "a.mp4"
