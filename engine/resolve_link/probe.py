"""기능 점검 순서: probe_read(읽기만)와 probe_copy C1~C8(복사본에서만).

- C1이 지금 타임라인의 복사본("AI 도우미 점검용 hhmmss")을 만들고, C2~C7은 그 복사본에서만 시험한다.
- 단계마다 받은 복사본 지문을 last_fp로 기억하고 caps\\probe_state.json에 적는다 (창을 다시 켜도 남게).
- C8(정리)은 중간에 무엇이 실패해도 늘 부른다. Lua는 복사본의 지문이 last_fp와 같을 때만 지운다.
- 남은 복사본은 사용자가 확인한 뒤에만, 지문이 같을 때만 지운다. probe_state.json이 없으면 지우지 않는다.
"""

from __future__ import annotations

import json
import math
import os
import sys
import threading
import time
import wave
from array import array
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from .bridge import LinkError
from .ops import PROBE_PREFIX, ProbeResult, ResolveOps, TimelineInfo
from .paths import files_dir, state_dir
from .timecode import frames_to_tc, parse_fps, tc_to_frames

PROBE_STATE_VERSION = 1
PROBE_RATE = 48000
PROBE_SAMPLES = 96000  # 2.0초
PROBE_CHANNELS = 2
MUTATING_STAGES = ("C2", "C3", "C4", "C5", "C6", "C7")


def probe_frames(samples: int, rate: int, fps: float) -> int:
    """WAV 샘플 수로 센 타임라인 프레임 수 (버림: 파일보다 길게 달라고 하지 않게)."""
    return int(math.floor(samples * fps / rate + 1e-9))


def make_probe_wav(folder: Optional[Path] = None, now: Optional[float] = None) -> Path:
    """files\\probe\\probe_<hhmmss>.wav: 정확히 96,000샘플(48kHz 2초), 조용한 1kHz. 이름이 겹치면 _2, _3."""
    folder = Path(folder) if folder is not None else files_dir() / "probe"
    folder.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%H%M%S", time.localtime(now if now is not None else time.time()))
    path = folder / f"probe_{stamp}.wav"
    n = 2
    while path.exists():
        path = folder / f"probe_{stamp}_{n}.wav"
        n += 1
    amp = 10 ** (-30 / 20) * 32767
    cycle = array("h")
    for i in range(48):  # 1kHz는 48샘플에 한 바퀴
        v = int(round(amp * math.sin(2 * math.pi * i / 48)))
        cycle.extend([v] * PROBE_CHANNELS)
    samples = array("h")
    for _ in range(PROBE_SAMPLES // 48):
        samples.extend(cycle)
    if sys.byteorder == "big":
        samples.byteswap()
    with wave.open(str(path), "wb") as w:
        w.setnchannels(PROBE_CHANNELS)
        w.setsampwidth(2)
        w.setframerate(PROBE_RATE)
        w.writeframes(samples.tobytes())
    return path


def wav_samples(path: Path) -> int:
    with wave.open(str(path), "rb") as w:
        return w.getnframes()


@dataclass
class ProbeState:
    """caps\\probe_state.json: 점검 중이거나 남은 복사본의 기록."""

    original_uid: Optional[str] = None
    original_name: Optional[str] = None
    copy_uid: Optional[str] = None
    copy_name: Optional[str] = None
    last_fp: Optional[str] = None
    last_stage: Optional[str] = None
    clip_path: Optional[str] = None
    clip_imported: bool = False
    started_at: Optional[str] = None
    updated_at: Optional[str] = None
    probe_version: int = PROBE_STATE_VERSION

    def to_dict(self) -> Dict[str, Any]:
        return dict(self.__dict__)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "ProbeState":
        known = {k: d.get(k) for k in cls().__dict__ if k in d}
        state = cls(**known)
        state.clip_imported = bool(state.clip_imported)
        return state

    def c8_args(self) -> Dict[str, Any]:
        """스크립트를 다시 눌러 Lua 기록이 없을 때 C8에 알려 줄 번호·이름."""
        return {
            "original_uid": self.original_uid, "original_name": self.original_name,
            "copy_uid": self.copy_uid, "copy_name": self.copy_name,
            "clip_path": self.clip_path, "clip_imported": self.clip_imported or None,
        }


class ProbeStateStore:
    def __init__(self, path: Optional[Path] = None) -> None:
        self._path = Path(path) if path is not None else None

    @property
    def path(self) -> Path:
        return self._path if self._path is not None else state_dir() / "caps" / "probe_state.json"

    def load(self) -> Optional[ProbeState]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if not isinstance(data, dict) or data.get("probe_version") != PROBE_STATE_VERSION:
            return None
        return ProbeState.from_dict(data)

    def save(self, state: ProbeState) -> None:
        state.updated_at = time.strftime("%Y-%m-%dT%H:%M:%S")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(self.path.name + ".tmp")
        tmp.write_text(json.dumps(state.to_dict(), ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, self.path)

    def clear(self) -> None:
        try:
            self.path.unlink()
        except OSError:
            pass


@dataclass
class ProbeRun:
    """점검 한 번의 결과 (결과 파일에 그대로 적는다)."""

    started_at: str
    read: Optional[Dict[str, Any]] = None
    stages: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    last_fp: Optional[str] = None
    cleanup: Optional[Dict[str, Any]] = None
    leftover: bool = False
    refused: Optional[str] = None
    wav: Optional[Dict[str, Any]] = None
    seconds: Dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return dict(self.__dict__)


def _error_dict(exc: BaseException) -> Dict[str, Any]:
    return {"ok": False, "error": getattr(exc, "error", None) or type(exc).__name__, "message": str(exc),
            "payload": getattr(exc, "payload", None)}


def c5_timecode(info: TimelineInfo, seconds: int = 10) -> Optional[str]:
    """C5에서 옮겨 볼 위치: 시작 + 10초 (타임라인이 짧으면 가운데)."""
    if not info.start_tc or not info.fps:
        return None
    try:
        fps, nominal = parse_fps(info.fps)
        start = tc_to_frames(info.start_tc, info.fps, info.drop_frame)
    except ValueError:
        return None
    step = int(round(seconds * fps))
    if info.length_frames is not None and step >= info.length_frames:
        step = max(0, info.length_frames // 2)
    tc = frames_to_tc(start + step, info.fps, bool(info.drop_frame))
    return tc if len(tc) == 11 else None


class ProbeRunner:
    """기능 점검 전체. 부르는 쪽(작업 스레드)에서 run()을 부른다."""

    def __init__(
        self,
        ops: ResolveOps,
        store: Optional[ProbeStateStore] = None,
        *,
        wav_maker: Callable[[], Path] = make_probe_wav,
        on_stage: Optional[Callable[[str, Dict[str, Any]], None]] = None,
        cancel: Optional[threading.Event] = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.ops = ops
        self.store = store or ProbeStateStore()
        self.wav_maker = wav_maker
        self.on_stage = on_stage
        self.cancel = cancel
        self.clock = clock

    def _report(self, stage: str, data: Dict[str, Any]) -> None:
        if self.on_stage is not None:
            try:
                self.on_stage(stage, data)
            except Exception:  # noqa: BLE001 - 화면 알림이 실패해도 점검은 계속
                pass

    def run(self, suffix: Optional[str] = None) -> ProbeRun:
        run = ProbeRun(started_at=time.strftime("%Y-%m-%dT%H:%M:%S"))
        t0 = self.clock()
        try:
            run.read = self.ops.probe_read(cancel=self.cancel)
        except LinkError as exc:
            run.read = _error_dict(exc)
        run.seconds["read"] = round(self.clock() - t0, 3)
        self._report("read", run.read)

        info = self.ops.timeline_info(cancel=self.cancel)
        if not info.has_timeline:
            run.refused = "no_timeline"
            return run
        if info.is_probe_copy:
            run.refused = "on_probe_copy"
            return run
        old = self.store.load()
        if old is not None and old.copy_uid is not None:
            run.refused = "leftover"  # 지난 점검의 복사본 기록이 남아 있다: 먼저 정리한다
            run.leftover = True
            return run

        suffix = suffix or time.strftime("%H%M%S")
        state = ProbeState(original_uid=info.timeline_uid, original_name=info.timeline,
                           started_at=run.started_at)
        sent_c1 = False
        try:
            sent_c1 = True
            r1 = self._stage(run, "C1", suffix=suffix)
            state.copy_uid = r1.probe.get("copy_uid") if r1.probe else r1.detail.get("copy_uid")
            state.copy_name = (r1.probe.get("copy_name") if r1.probe else None) or r1.detail.get("copy_name")
            self._remember(run, state, r1)
            if not r1.ok:
                return run
            fps = info.fps_value or 30.0
            wav = self.wav_maker()
            samples = wav_samples(wav)
            frames = probe_frames(samples, PROBE_RATE, fps)
            run.wav = {"path": str(wav), "samples": samples, "rate": PROBE_RATE, "frames": frames, "fps": fps}
            args = {
                "C5": {"tc": c5_timecode(info)},
                "C7": {"path": str(wav), "frames": frames},
            }
            for stage in MUTATING_STAGES:
                if self.cancel is not None and self.cancel.is_set():
                    run.stages[stage] = {"ok": False, "error": "cancelled"}
                    break
                if stage == "C5" and not args["C5"]["tc"]:
                    run.stages[stage] = {"ok": False, "error": "no_timecode"}
                    continue
                r = self._stage(run, stage, **args.get(stage, {}))
                if stage == "C7":
                    state.clip_path = str(wav)
                    state.clip_imported = r.detail.get("imported") is True
                self._remember(run, state, r)
        except LinkError as exc:
            stage = next((s for s in ("C1",) + MUTATING_STAGES if s not in run.stages), "C?")
            run.stages[stage] = _error_dict(exc)
            self._report(stage, run.stages[stage])
        finally:
            if sent_c1:
                self._cleanup(run, state)
        return run

    def _stage(self, run: ProbeRun, stage: str, **args: Any) -> ProbeResult:
        t0 = self.clock()
        r = self.ops.probe_copy(stage, **args)  # 점검 단계는 멈추기로 끊지 않는다 (C8까지 가야 한다)
        run.seconds[stage] = round(self.clock() - t0, 3)
        run.stages[stage] = r.to_dict()
        self._report(stage, run.stages[stage])
        return r

    def _remember(self, run: ProbeRun, state: ProbeState, r: ProbeResult) -> None:
        if r.fingerprint:
            run.last_fp = r.fingerprint
            state.last_fp = r.fingerprint
            state.last_stage = r.stage
        if state.copy_uid is not None or state.copy_name is not None:
            try:
                self.store.save(state)
            except OSError:
                pass

    def _cleanup(self, run: ProbeRun, state: ProbeState) -> None:
        t0 = self.clock()
        try:
            r = self.ops.probe_copy("C8", expect_fingerprint=state.last_fp or "", **state.c8_args())
            run.cleanup = r.to_dict()
            deleted = r.detail.get("deleted") is True
            gone = r.detail.get("copy_found") is False
            if deleted or gone:
                self.store.clear()
            run.leftover = not (deleted or gone)
        except LinkError as exc:
            run.cleanup = _error_dict(exc)
            run.leftover = state.copy_uid is not None or state.copy_name is not None
        run.seconds["C8"] = round(self.clock() - t0, 3)
        self._report("C8", run.cleanup or {})


@dataclass
class LeftoverResult:
    deleted: bool
    reason: Optional[str]
    detail: Dict[str, Any]


def leftover_name(info: Optional[TimelineInfo], state: Optional[ProbeState]) -> Optional[str]:
    """지금 열린 타임라인이 점검용 복사본이면 그 이름 (머리말 경고용)."""
    if info is not None and info.timeline and info.timeline.startswith(PROBE_PREFIX):
        return info.timeline
    return None


def delete_leftover(ops: ResolveOps, store: ProbeStateStore, confirmed: bool) -> LeftoverResult:
    """남은 점검용 복사본을 지운다: 사용자가 확인했고, probe_state.json이 있고, 지문이 같을 때만."""
    if not confirmed:
        return LeftoverResult(False, "not_confirmed", {})
    state = store.load()
    if state is None or (state.copy_uid is None and state.copy_name is None):
        return LeftoverResult(False, "no_state", {})
    if not state.last_fp:
        return LeftoverResult(False, "no_fingerprint", {})
    r = ops.probe_copy("C8", expect_fingerprint=state.last_fp, **state.c8_args())
    deleted = r.detail.get("deleted") is True
    gone = r.detail.get("copy_found") is False
    if deleted or gone:
        store.clear()
    reason = None if deleted else (r.detail.get("reason") or "unknown")
    return LeftoverResult(deleted, reason, r.to_dict())
