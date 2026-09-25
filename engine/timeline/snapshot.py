"""계산할 때 본 타임라인 (TimelineSnapshot)과 그 지문.

넣기 직전에 지문을 다시 읽어 계산할 때와 같은지 본다 (설계 B2.5 5.1):
fingerprint = sha1(정렬한 (uid|track|start|end|left_offset|path|enabled) 줄).
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Set

from ..edits.journal import key_for
from ..resolve_link.ops import Item, TimelineInfo
from .map import exact_fps


def _line(item: Item) -> str:
    return "|".join(str(x) for x in (item.uid, item.track, item.start, item.end, item.left_offset, item.path,
                                      item.enabled))


def fingerprint(items: Iterable[Item]) -> str:
    rows = sorted(_line(i) for i in items)
    return hashlib.sha1("\n".join(rows).encode("utf-8")).hexdigest()


@dataclass
class TimelineSnapshot:
    info: TimelineInfo
    items: List[Item] = field(default_factory=list)  # 소리 클립

    @property
    def fps(self) -> Optional[float]:
        return exact_fps(self.info.fps)

    @property
    def start(self) -> int:
        return self.info.start_frame or 0

    @property
    def end(self) -> Optional[int]:
        return self.info.end_frame

    @property
    def key(self) -> str:
        return key_for(self.info)

    def fingerprint(self) -> str:
        return fingerprint(self.items)

    def enabled_tracks(self, kind: str = "audio") -> Set[int]:
        """꺼지지 않은 트랙 번호 (켜짐을 모르는 트랙은 켜진 것으로 본다)."""
        return {t.index for t in self.info.tracks.get(kind, []) if t.enabled is not False}

    def track_name(self, index: int, kind: str = "audio") -> str:
        for t in self.info.tracks.get(kind, []):
            if t.index == index:
                return t.name
        return ""

    def timeline_record(self) -> Dict[str, Any]:
        """일지·결과 파일에 적는 타임라인 정보."""
        i = self.info
        return {"project": i.project, "project_uid": i.project_uid, "timeline": i.timeline,
                "timeline_uid": i.timeline_uid, "start_frame": i.start_frame, "end_frame": i.end_frame,
                "fps": i.fps, "drop_frame": i.drop_frame, "start_tc": i.start_tc, "key": self.key}
