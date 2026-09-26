"""두뇌가 보는 것 (설계 B4.1 AssistContext). 화면이 연결 확인 뒤에 만들어 넘긴다.

RuleBrain은 여기서 시간(TimeContext), 대화 색 기본값, 버튼 설정, 마지막 카드만 쓴다.
AI 두뇌(2.2 이후)에 보내는 것은 이것의 일부(설계 B5.5 여섯 가지)뿐이다. 2.1에는 AI 두뇌가 없다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .timeparse import TimeContext


@dataclass
class AssistContext:
    time: Optional[TimeContext] = None  # None = 아직 타임라인을 모름 (연결 확인 전)
    info: Any = None  # ops.TimelineInfo
    caps: Any = None  # Capabilities (in_out, range_markers ...)
    default_color: str = "Green"  # settings chat.default_marker_color
    slots: List[Dict[str, Any]] = field(default_factory=list)  # 자동화 버튼 설정 (설정값 출처)
    last_card: Optional[Dict[str, Any]] = None  # 마지막 대화 카드 {kind, params, proposal_id, scope, state}
    journal: List[Dict[str, Any]] = field(default_factory=list)  # 이 타임라인의 최근 일지 (되돌리기)
    turns: List[Dict[str, Any]] = field(default_factory=list)  # 최근 대화 (6줄)

    def cap(self, name: str) -> Optional[bool]:
        try:
            return self.caps.has(name) if self.caps is not None else None
        except (KeyError, AttributeError):
            return None

    def slot_for(self, kind: str) -> Optional[Dict[str, Any]]:
        """이 종류(쉬는 곳/튀는 소리)를 하는 첫 버튼 (보이는 순서). 대화의 값이 비면 이 버튼의 설정을 쓴다."""
        for s in self.slots:
            if s.get("kind") == kind:
                return s
        return None
