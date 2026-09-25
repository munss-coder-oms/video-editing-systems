"""결과 폴더 규칙: 덮어쓰지 않기, 실패·취소하면 반쯤 만든 파일 지우기, 알림 문구."""

from __future__ import annotations

from pathlib import Path

import pytest

from engine.ffmpeg import Cancelled
from engine.job import default_output_dir, free_output_dir, media_warnings, process_video, short_name
from engine.probe import AudioTrack, MediaInfo

from .conftest import requires_ffmpeg


def test_default_output_dir_and_chosen_parent(tmp_path):
    video = tmp_path / "촬영" / "C0001.MP4"
    assert default_output_dir(video) == tmp_path / "촬영" / "C0001_resolve"
    # 사용자가 결과 폴더를 고르면 그 안에 영상별 폴더를 만든다 (영상마다 덮어쓰지 않게)
    assert default_output_dir(video, parent=tmp_path / "결과") == tmp_path / "결과" / "C0001_resolve"


def test_long_names_are_shortened():
    stem = "아주 긴 제목 " * 20
    assert len(short_name(stem)) <= 40
    assert short_name("...") == "video"


def test_finished_folder_is_never_reused(tmp_path):
    base = tmp_path / "a_resolve"
    assert free_output_dir(base) == base
    base.mkdir()
    assert free_output_dir(base) == base  # 끝난 결과(project.json)가 없으면 그대로 쓴다
    (base / "project.json").write_text("{}", encoding="utf-8")
    assert free_output_dir(base) == tmp_path / "a_resolve_2"
    (tmp_path / "a_resolve_2").mkdir()
    (tmp_path / "a_resolve_2" / "project.json").write_text("{}", encoding="utf-8")
    assert free_output_dir(base) == tmp_path / "a_resolve_3"


@requires_ffmpeg
def test_second_run_goes_to_new_folder(uneven_video, tmp_path):
    first = process_video(uneven_video, output_dir=tmp_path / "out", strength="weak")
    wav1 = Path(first.balance.output_wav)
    stamp = wav1.stat().st_mtime_ns
    second = process_video(uneven_video, output_dir=tmp_path / "out", strength="strong")
    assert Path(second.output_dir) == tmp_path / "out_2"
    assert wav1.stat().st_mtime_ns == stamp  # 리졸브에 불러온 첫 결과는 그대로


@requires_ffmpeg
def test_cancel_removes_the_new_folder(uneven_video, tmp_path):
    calls = {"n": 0}

    def cancel_later() -> bool:
        calls["n"] += 1
        return calls["n"] > 3

    out = tmp_path / "cancelled"
    with pytest.raises(Cancelled):
        process_video(uneven_video, output_dir=out, is_cancelled=cancel_later)
    assert not out.exists()


@requires_ffmpeg
def test_failure_keeps_only_the_log(uneven_video, tmp_path, monkeypatch):
    import engine.job as job

    def broken(*_a, **_k):
        raise ValueError("FCPXML 만들기 실패 (테스트)")

    monkeypatch.setattr(job, "build_fcpxml", broken)
    out = tmp_path / "failed"
    with pytest.raises(ValueError):
        process_video(uneven_video, output_dir=out)
    assert sorted(p.name for p in out.iterdir()) == ["작업로그.log"]
    assert "FCPXML 만들기 실패" in (out / "작업로그.log").read_text(encoding="utf-8")
    # 실패한 폴더는 다음 실행이 그대로 다시 쓴다 (project.json이 없으므로)
    assert free_output_dir(out) == out


def _media(**kw) -> MediaInfo:
    base = dict(
        path="a.mp4", duration=10.0, has_video=True, width=1920, height=1080,
        frame_rate="30/1", native_frame_rate="30/1", has_audio=True,
        audio_tracks=[AudioTrack(index=0, channels=2)],
    )
    base.update(kw)
    return MediaInfo(**base)


def test_no_warnings_for_plain_video():
    assert media_warnings(_media(), [0]) == []


def test_warnings_explain_what_changed():
    notes = media_warnings(
        _media(
            frame_rate="25/1", native_frame_rate="257/10", variable_frame_rate=True,
            width=7680, height=4320,
            audio_tracks=[AudioTrack(index=0), AudioTrack(index=1)],
        ),
        [0, 1],
    )
    text = "\n".join(notes)
    assert "오디오 트랙이 2개" in text
    assert "25.000fps" in text and "25.700fps" in text
    assert "가변 프레임" in text
    assert "3840x2160" in text


def test_track_choice_is_reported():
    notes = media_warnings(_media(audio_tracks=[AudioTrack(index=0), AudioTrack(index=1)]), [1])
    assert notes == ["오디오 트랙 2개 중 2번만 썼습니다."]
