"""CapabilityStore: 이 리졸브 판에서 무엇이 되는지 (기능 점검 결과) 기억하기.

%LOCALAPPDATA%\\video-editing-systems\\caps\\resolve-<제품>-<판>.json 에 판마다 따로 둔다.
리졸브를 새 판으로 올리면 파일 이름이 달라지므로 읽기 점검(probe_read)을 다시 한다.
점검으로 확인되지 않은 기능은 "모름"(None)이고, 화면은 모르는 기능을 켜지 않는다.
"""

from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from .paths import state_dir

PROBE_VERSION = 1

# 기능 이름 → (근거, 설명). 설명은 결과 파일에 쓴다.
CAPS = {
    "unique_id": "타임라인·클립 번호(GetUniqueId)",
    "in_out": "In/Out 읽기(GetMarkInOut)",
    "selected_clips": "고른 클립 읽기(GetSelectedClips)",
    "track_enable": "트랙 끄기/켜기(C2)",
    "clip_enable": "클립 끄기/켜기(C3)",
    "range_markers": "길이 있는 표시(C4)",
    "marker_delete": "표시 지우기(C4)",
    "jump": "재생 위치 옮기기(C5)",
    "multi_stream_append": "여러 소리 트랙 영상 다시 넣기(C6)",
    "fresh_import": "새 소리 파일 가져오기(C7)",
    "exact_placement": "정확한 길이로 넣기(C7)",
    "delete_media_clip": "미디어 풀에서 점검 파일 지우기(C8)",
}


def _slug(text: Optional[str]) -> str:
    s = re.sub(r"[^0-9A-Za-z._-]+", "_", (text or "unknown").strip())
    return s.strip("_")[:60] or "unknown"


def _now_text() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def _atomic_write(path: Path, data: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, path)


def summarize_read(result: Dict[str, Any]) -> Dict[str, Any]:
    """probe_read 답에서 기억할 것만 (calls, 긴 연결 정보 글은 뺀다)."""
    exists = result.get("exists") if isinstance(result.get("exists"), dict) else {}
    return {
        "exists": {k: v for k, v in exists.items() if isinstance(v, bool) or v is None},
        "existence_reliable": result.get("existence_reliable") is True,
        "props_keys": result.get("props_keys"),
        "project_uid": isinstance(result.get("project_uid"), str),
        "timeline_uid": isinstance(result.get("timeline_uid"), str),
        "mapping_available": any(
            isinstance(m, dict) and isinstance(m.get("mapping"), str)
            for m in (result.get("source_audio_mapping") or [])
        ),
    }


def summarize_stage(result: Dict[str, Any]) -> Dict[str, Any]:
    """probe_copy 한 단계에서 기억할 것 (ok, detail)."""
    detail = result.get("detail") if isinstance(result.get("detail"), dict) else {}
    return {"ok": result.get("ok") is True, "detail": detail, "error": result.get("error")}


class Capabilities:
    """한 리졸브 판의 점검 기록."""

    def __init__(self, path: Path, product: Optional[str], version: Optional[str], data: Dict[str, Any]) -> None:
        self.path = path
        self.product = product
        self.version = version
        self.data = data

    @property
    def read(self) -> Optional[Dict[str, Any]]:
        r = self.data.get("read")
        return r if isinstance(r, dict) else None

    @property
    def copy(self) -> Dict[str, Dict[str, Any]]:
        c = self.data.get("copy")
        return c if isinstance(c, dict) else {}

    @property
    def manual(self) -> Dict[str, Any]:
        m = self.data.get("manual")
        return m if isinstance(m, dict) else {}

    def needs_probe_read(self, script_version: Optional[str]) -> bool:
        return (
            self.read is None
            or self.data.get("probe_version") != PROBE_VERSION
            or self.data.get("script_version") != script_version
        )

    def needs_probe_copy(self) -> bool:
        return not self.copy

    def _exists(self, name: str) -> Optional[bool]:
        read = self.read
        if read is None or not read.get("existence_reliable"):
            return None
        v = read.get("exists", {}).get(name)
        return v if isinstance(v, bool) else None

    def _stage(self, stage: str) -> Optional[Dict[str, Any]]:
        s = self.copy.get(stage)
        return s if isinstance(s, dict) else None

    def has(self, cap: str) -> Optional[bool]:
        """True = 점검으로 됨을 확인, False = 안 됨을 확인, None = 모름."""
        if cap not in CAPS:
            raise KeyError(cap)
        if cap == "unique_id":
            read = self.read
            return None if read is None else bool(read.get("timeline_uid"))
        if cap == "in_out":
            return self._exists("Timeline.GetMarkInOut")
        if cap == "selected_clips":
            return self._exists("Timeline.GetSelectedClips")
        stage_of = {
            "track_enable": "C2", "clip_enable": "C3", "range_markers": "C4", "marker_delete": "C4",
            "jump": "C5", "multi_stream_append": "C6", "fresh_import": "C7", "exact_placement": "C7",
            "delete_media_clip": "C8",
        }
        s = self._stage(stage_of[cap])
        if s is None:
            return None
        d = s.get("detail") or {}
        if cap == "range_markers":
            return d.get("range_ok") if isinstance(d.get("range_ok"), bool) else None
        if cap == "marker_delete":
            return d.get("left") == 0 if "left" in d else None
        if cap == "fresh_import":
            return d.get("imported") if isinstance(d.get("imported"), bool) else None
        if cap == "exact_placement":
            return d.get("length_ok") if isinstance(d.get("length_ok"), bool) else None
        if cap == "delete_media_clip":
            return d.get("clip_deleted") if isinstance(d.get("clip_deleted"), bool) else None
        if s.get("error"):
            return None  # 단계가 끝나지 않았다: 모름
        return bool(s.get("ok"))

    def allowed(self, cap: str) -> bool:
        """이 기능을 켜도 되는지: 점검으로 확인된 것만."""
        return self.has(cap) is True

    def missing(self) -> List[str]:
        return [c for c in CAPS if self.has(c) is not True]

    def summary_lines(self) -> List[str]:
        out = []
        for cap, label in CAPS.items():
            v = self.has(cap)
            out.append(f"{label}: {'됨' if v is True else '안 됨' if v is False else '모름'}")
        return out


class CapabilityStore:
    def __init__(self, folder: Optional[Path] = None) -> None:
        self._folder = Path(folder) if folder is not None else None

    @property
    def folder(self) -> Path:
        return self._folder if self._folder is not None else state_dir() / "caps"

    def path_for(self, product: Optional[str], version: Optional[str]) -> Path:
        return self.folder / f"resolve-{_slug(product)}-{_slug(version)}.json"

    def load(self, product: Optional[str], version: Optional[str]) -> Capabilities:
        path = self.path_for(product, version)
        data: Dict[str, Any] = {}
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                data = loaded
        except (OSError, ValueError):
            data = {}
        return Capabilities(path, product, version, data)

    def _save(self, caps: Capabilities, script_version: Optional[str]) -> None:
        caps.data.update({
            "probe_version": PROBE_VERSION, "product": caps.product, "version": caps.version,
            "script_version": script_version, "measured_at": _now_text(),
        })
        try:
            _atomic_write(caps.path, caps.data)
        except OSError:
            pass  # 기록을 못 남겨도 점검 결과는 이번 실행과 결과 파일에 있다

    def record_read(self, product, version, script_version, result: Dict[str, Any]) -> Capabilities:
        caps = self.load(product, version)
        caps.data["read"] = summarize_read(result)
        self._save(caps, script_version)
        return caps

    def record_copy(self, product, version, script_version, stages: Dict[str, Dict[str, Any]]) -> Capabilities:
        caps = self.load(product, version)
        caps.data["copy"] = {k: summarize_stage(v) for k, v in stages.items()}
        self._save(caps, script_version)
        return caps

    def record_manual(self, product, version, script_version, key: str, answer: Any) -> Capabilities:
        caps = self.load(product, version)
        manual = dict(caps.manual)
        manual[key] = {"answer": answer, "at": _now_text()}
        caps.data["manual"] = manual
        self._save(caps, script_version)
        return caps
