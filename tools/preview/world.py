"""미리보기의 가짜 리졸브: tests/fakes.py의 FakeLuaBridge + FakeResolve를 사용자의 시험 상황처럼 채운다.

- '테스트 영상' 프로젝트의 Timeline 1 (01:00:00:00부터, 60fps, 7분). 소리 트랙 A1~A4에 OBS 녹화 한 개.
- 지난번 연결 시험이 남긴 것: 옛 시험 트랙 'AI 도우미 시험'(3초 '삐' 클립)과 노란 시험 표시 두 개.
- 사용자가 직접 찍은 표시 두 개 (초록, 파란): 도우미가 지우지 않는 것을 보여 주려고.
- 기능 점검(probe_copy)은 단계마다 "됨"으로 답한다. 도는 모습을 찍을 수 있게 한 단계에서 잠깐 기다릴 수 있다.
- 리졸브가 켜져 있는지(윈도우 작업 목록 흉내)는 running으로 바꾼다.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any, Dict, Optional

from engine.edits.apply import LEGACY_TEST_TRACK
from tests.fakes import FakeLuaBridge, FakeResolve, audio_item, obs_items, timeline_info

FPS = 60
TL_START = 216000  # 01:00:00:00
SECONDS = 420
LENGTH = SECONDS * FPS
PROJECT = "테스트 영상"
TEST_TAG = "aih_test"  # 1차 시험판 [표시 찍기 시험]의 꼬리표 (app/companion/steps.TEST_CUSTOM)
TEST_NAME = LEGACY_TEST_TRACK
TONE_AT = 10 * FPS  # 옛 시험 '삐' 소리 자리 (타임라인 시작부터 센 프레임)
TONE_FRAMES = 3 * FPS
MISSING_MEDIA = "D:/녹화/2026-09-24 21-05-11.mp4"  # 짧은 미리보기(소리 계산 없음)에서 쓰는 이름뿐인 경로

# 사용자가 직접 찍은 표시와 옛 시험 표시 (타임라인 시작부터 센 프레임, 색, 이름, 꼬리표)
USER_MARKERS = ((90 * FPS, "Green", "내 표시", ""), (150 * FPS, "Blue", "내 파란 표시", ""))
LEGACY_MARKERS = ((10 * FPS, "Yellow", TEST_NAME, TEST_TAG), (70 * FPS, "Yellow", TEST_NAME, TEST_TAG))


class World:
    """가짜 리졸브 한 벌. fake(브리지)를 창에 넘기고, resolve(표시·트랙)를 그림과 확인에 쓴다."""

    def __init__(self, base: Path, media: Optional[Path] = None, tone: Optional[Path] = None) -> None:
        self.fake = FakeLuaBridge(base)
        info = timeline_info(project=PROJECT, start_frame=TL_START, end_frame=TL_START + LENGTH, fps=str(FPS),
                             current_tc="01:00:10:00")
        info["tracks"]["audio"].append({"index": 5, "name": TEST_NAME, "enabled": True, "locked": False,
                                        "subtype": "stereo", "count": 1})
        self.fake.info = info
        path = str(media) if media is not None else MISSING_MEDIA
        items = obs_items(path, TL_START, LENGTH, clip_fps=str(FPS))
        self.tone_path = str(tone) if tone is not None else str(Path(base) / "test_tone.wav")
        items.append(audio_item("t1", 5, TL_START + TONE_AT, TONE_FRAMES, self.tone_path))
        self.resolve = FakeResolve(info, items)
        for frame, color, name, custom in USER_MARKERS + LEGACY_MARKERS:
            self.resolve.add_user_marker(frame, custom=custom, color=color, name=name)
        self.fake.resolve = self.resolve
        self.fake.online = False  # 스크립트를 누르기 전
        self.running = True  # 리졸브(Resolve.exe)가 켜져 있음
        self.probe_hold: Optional[str] = None  # 기능 점검이 이 단계에서 기다린다
        self.probe_waiting = threading.Event()
        self.probe_release = threading.Event()
        self._fp = 0
        self.fake.handlers["probe_copy"] = self._probe_copy

    # 창의 process_check (연결 뒤 5초마다 윈도우 작업 목록에서 Resolve.exe를 보는 것의 흉내)
    def is_running(self) -> Optional[bool]:
        return self.running

    def _probe_copy(self, args: Dict[str, Any]) -> Dict[str, Any]:
        stage = str(args.get("stage") or "")
        if stage == self.probe_hold:
            self.probe_waiting.set()
            self.probe_release.wait(60)
        if stage == "C8":
            return {"ok": True, "stage": "C8", "detail": {"deleted": True, "copy_found": True}, "calls": {}}
        self._fp += 1
        out: Dict[str, Any] = {"ok": True, "stage": stage, "fingerprint": f"4:3:{self._fp}", "detail": {},
                               "calls": {}}
        if stage == "C1":
            copy = {"copy_uid": "tl-probe", "copy_name": "AI 도우미 점검용 140211"}
            out["probe"] = dict(copy, original_uid="tl-1", original_name="Timeline 1")
            out["detail"] = dict(copy)
        if stage == "C7":
            out["detail"] = {"imported": True}
        return out

    def restore_legacy(self) -> None:
        """지난번 시험 흔적(노란 시험 표시, 'AI 도우미 시험' 트랙과 '삐' 클립)을 다시 둔다 ('이럴 때는' 단계)."""
        for frame, color, name, custom in LEGACY_MARKERS:
            self.resolve.add_user_marker(frame, custom=custom, color=color, name=name)
        tracks = self.info["tracks"]["audio"]
        if not any(t.get("name") == TEST_NAME for t in tracks):
            index = len(tracks) + 1
            tracks.append({"index": index, "name": TEST_NAME, "enabled": True, "locked": False, "subtype": "stereo",
                           "count": 1})
            self.resolve.items.append(audio_item("t1", index, TL_START + TONE_AT, TONE_FRAMES, self.tone_path))

    # 그림과 확인에 쓰는 것
    @property
    def info(self) -> Dict[str, Any]:
        return self.resolve.info

    def ours(self, color: Optional[str] = None) -> Dict[int, Dict[str, Any]]:
        """도우미가 넣은 표시 (aih: 꼬리표)."""
        return {f: m for f, m in self.resolve.markers.items()
                if str(m.get("custom") or "").startswith("aih:") and (color is None or m.get("color") == color)}

    def user_markers(self) -> Dict[int, Dict[str, Any]]:
        return {f: m for f, m in self.resolve.markers.items() if not str(m.get("custom") or "").startswith("aih")}
