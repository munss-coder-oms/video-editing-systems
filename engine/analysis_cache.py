"""소리 스트림마다 음량 분석 결과를 저장해 두기 (설계 B2.4 1).

%LOCALAPPDATA%\\video-editing-systems\\cache\\analysis\\<sha1>.json
열쇠: (파일의 절대 경로, 크기, 고친 시각, 스트림 번호, ENGINE_ANALYSIS_VERSION).
파일이 바뀌거나 분석 방법이 바뀌면(버전) 열쇠가 달라져 다시 잰다. 한 번 재 두면 같은 녹화에서
다시 누르거나 설정을 바꿔 다시 계산할 때 1초도 걸리지 않는다.

- 0.1초마다의 0.4초 음량(M)과 3초 음량(S)을 적는다 (18분 46초면 줄마다 약 11,300개, 수백 KB).
- FFmpeg는 낮은 우선순위로 돌린다 (리졸브 재생을 덜 방해하게).
- numpy는 쓰지 않는다.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from . import ffmpeg
from .loudness import SILENCE, analyze
from .probe import MediaInfo, probe

ENGINE_ANALYSIS_VERSION = 1
MAX_FILES = 300  # 이보다 많이 쌓이면 오래된 것부터 지운다
ACTIVE_ABOVE_FLOOR = 10.0  # 바닥 소음보다 이만큼 큰 0.1초를 "소리가 나는 중"으로 센다


def _state_folder() -> Path:
    from .resolve_link.paths import state_dir

    return state_dir() / "cache" / "analysis"


@dataclass
class StreamAnalysis:
    """파일 하나의 소리 스트림 하나를 잰 결과. 시각(t)은 영상 첫 프레임을 0초로 센 원본 시각."""

    path: str
    stream: int
    duration: float
    integrated: float
    true_peak: float
    lra: float
    typical: float
    noise_floor: Optional[float]
    t: List[float] = field(default_factory=list, repr=False)
    m: List[float] = field(default_factory=list, repr=False)  # 0.4초 음량 (LUFS)
    s: List[float] = field(default_factory=list, repr=False)  # 3초 음량 (LUFS)
    elapsed_s: float = 0.0  # 재는 데 걸린 시간 (캐시에서 읽었으면 처음 잴 때 걸린 시간)
    version: int = ENGINE_ANALYSIS_VERSION

    @property
    def momentary(self) -> List[Tuple[float, float]]:
        return list(zip(self.t, self.m))

    @property
    def short_term(self) -> List[Tuple[float, float]]:
        return list(zip(self.t, self.s))

    def presence_floor(self) -> float:
        """0.4초 음량의 하위 10% (디지털 무음도 넣고 -70으로 자른 값).

        noise_floor(loudness)는 디지털 무음을 빼서, OBS 소음 제거(게이트)처럼 쉬는 동안 완전히 0이 되는
        마이크에서는 말소리 크기가 바닥이 되어 버린다. 여기서는 무음도 바닥으로 센다.
        """
        if not self.m:
            return SILENCE
        vals = sorted(max(SILENCE, v) for v in self.m)
        return vals[len(vals) // 10]

    def active_ratio(self) -> float:
        """0.1초 가운데 바닥보다 10dB 넘게 큰 것의 비율 (말하는 시간의 비율에 가깝다)."""
        if not self.m:
            return 0.0
        limit = self.presence_floor() + ACTIVE_ABOVE_FLOOR
        return sum(1 for v in self.m if v > limit) / len(self.m)

    def profile(self) -> Dict[str, Any]:
        """목소리를 고를 때 같이 기억하는 소리 모양 (다음 녹화에서 많이 다르면 다시 묻는다)."""
        return {
            "typical": round(self.typical, 1),
            "noise_floor": None if self.noise_floor is None else round(self.noise_floor, 1),
            "active_ratio": round(self.active_ratio(), 3),
            "integrated": round(self.integrated, 1),
        }

    def to_json(self) -> Dict[str, Any]:
        return {
            "version": self.version, "path": self.path, "stream": self.stream, "duration": self.duration,
            "integrated": self.integrated, "true_peak": self.true_peak, "lra": self.lra, "typical": self.typical,
            "noise_floor": self.noise_floor, "elapsed_s": round(self.elapsed_s, 3),
            "t": [round(x, 3) for x in self.t], "m": [round(x, 1) for x in self.m], "s": [round(x, 1) for x in self.s],
        }

    @classmethod
    def from_json(cls, d: Dict[str, Any]) -> "StreamAnalysis":
        t, m, s = list(d["t"]), list(d["m"]), list(d["s"])
        if not (len(t) == len(m) == len(s)):
            raise ValueError("series length")
        return cls(
            path=str(d["path"]), stream=int(d["stream"]), duration=float(d["duration"]),
            integrated=float(d["integrated"]), true_peak=float(d["true_peak"]), lra=float(d["lra"]),
            typical=float(d["typical"]),
            noise_floor=None if d.get("noise_floor") is None else float(d["noise_floor"]),
            t=[float(x) for x in t], m=[float(x) for x in m], s=[float(x) for x in s],
            elapsed_s=float(d.get("elapsed_s") or 0.0), version=int(d.get("version", 0)),
        )


def analyze_stream(
    path: str,
    stream: int,
    media: Optional[MediaInfo] = None,
    *,
    progress: Optional[ffmpeg.ProgressCallback] = None,
    is_cancelled: Optional[Callable[[], bool]] = None,
) -> StreamAnalysis:
    """스트림 하나를 잰다 (캐시 없이). 영상 첫 프레임이 0초가 되게 꺼낸다 (balance.source_graph)."""
    from .balance import source_graph

    if media is None:
        media = probe(path)
    t0 = time.monotonic()
    report = analyze(
        path, source=source_graph(media, [stream]), duration=media.duration or None, progress=progress,
        is_cancelled=is_cancelled, keep_frames=True, low_priority=True,
    )
    frames = report.momentary or []
    short = report.short_term or []
    return StreamAnalysis(
        path=str(path), stream=int(stream), duration=float(media.duration or 0.0),
        integrated=report.integrated, true_peak=report.true_peak, lra=report.lra, typical=report.typical,
        noise_floor=report.noise_floor, t=[t for t, _ in frames], m=[v for _, v in frames],
        s=[v for _, v in short], elapsed_s=time.monotonic() - t0,
    )


class AnalysisCache:
    """분석 결과 저장소. get()은 없으면 None, get_or_analyze()는 없으면 재서 저장한다."""

    def __init__(self, folder: Optional[Path] = None) -> None:
        self._folder = Path(folder) if folder is not None else None
        self.hits = 0
        self.misses = 0

    @property
    def folder(self) -> Path:
        return self._folder if self._folder is not None else _state_folder()

    @staticmethod
    def key(path: str, stream: int) -> Optional[str]:
        """캐시 열쇠. 파일이 없으면 None."""
        try:
            st = os.stat(path)
        except OSError:
            return None
        full = os.path.normcase(os.path.abspath(path))
        raw = f"{full}|{st.st_size}|{st.st_mtime_ns}|{int(stream)}|{ENGINE_ANALYSIS_VERSION}"
        return hashlib.sha1(raw.encode("utf-8")).hexdigest()

    def _file(self, key: str) -> Path:
        return self.folder / f"{key}.json"

    def get(self, path: str, stream: int) -> Optional[StreamAnalysis]:
        key = self.key(path, stream)
        if key is None:
            return None
        try:
            data = json.loads(self._file(key).read_text(encoding="utf-8"))
            a = StreamAnalysis.from_json(data)
        except (OSError, ValueError, KeyError, TypeError):
            return None
        if a.version != ENGINE_ANALYSIS_VERSION or a.stream != int(stream):
            return None
        return a

    def put(self, analysis: StreamAnalysis) -> Optional[Path]:
        key = self.key(analysis.path, analysis.stream)
        if key is None:
            return None
        target = self._file(key)
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            tmp = target.with_name(target.name + ".tmp")
            tmp.write_text(json.dumps(analysis.to_json(), ensure_ascii=False, separators=(",", ":")),
                           encoding="utf-8")
            os.replace(tmp, target)
        except OSError:
            return None  # 저장을 못 해도 이번 계산은 그대로 쓴다
        self._prune()
        return target

    def _prune(self) -> None:
        try:
            files = sorted(self.folder.glob("*.json"), key=lambda p: p.stat().st_mtime)
        except OSError:
            return
        for old in files[:-MAX_FILES] if len(files) > MAX_FILES else []:
            try:
                old.unlink()
            except OSError:
                pass

    def get_or_analyze(
        self,
        path: str,
        stream: int,
        media: Optional[MediaInfo] = None,
        *,
        progress: Optional[ffmpeg.ProgressCallback] = None,
        is_cancelled: Optional[Callable[[], bool]] = None,
    ) -> Tuple[StreamAnalysis, bool]:
        """(분석, 캐시에서 읽었는지)."""
        found = self.get(path, stream)
        if found is not None:
            self.hits += 1
            if progress is not None:
                progress(1.0)
            return found, True
        self.misses += 1
        a = analyze_stream(path, stream, media, progress=progress, is_cancelled=is_cancelled)
        self.put(a)
        return a, False
