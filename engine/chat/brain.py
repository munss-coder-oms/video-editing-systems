"""두뇌의 모양 (설계 B5.1)과 두뇌가 돌려주는 것 (설계 B4.1).

두뇌는 한국어 부탁 한 줄을 받아 네 가지 가운데 하나를 돌려준다.
- Reply: 답만 (도움말, 상태, 못 하는 부탁). 리졸브에 넣을 것이 없다.
- Question: 되묻기 (시간이 두 가지로 읽힘, "빼고", 무엇을 지울지 모름 ...). chips로 다시 말할 글을 보여 준다.
- ProposalDraft: 할 일 목록 (Command). 계획(planner) → 범위 지킴이(ScopeGuard) → 카드 → [리졸브에 넣기].
- NotUnderstood: 아무것도 못 알아들음 → "이 말은 아직 못 알아들어요. 이렇게 말해 보세요:" + 예문 3개.
- NeedTimeline: 타임라인(길이, 재생 위치)을 알아야 풀 수 있음 → 화면이 먼저 연결을 확인하고 다시 부른다.

두뇌는 글(code + data)만 돌려준다. 화면 글은 app/companion/strings_ko.py가 code로 고른다.
두뇌는 리졸브에 아무것도 묻지 않고, 아무것도 바꾸지 않는다. AI 두뇌(2.2 이후)도 같은 모양으로 붙는다.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Protocol, Tuple, Union, runtime_checkable

# 출처 (설계 B4.4). 화면은 strings_ko.PROVENANCE로 "말씀하신 값 / 기본값 / 설정값 / 도우미가 찾음"을 쓴다.
SAID = "said"
DEFAULT = "default"
SETTING = "setting"
FOUND = "found"
PROVENANCES = (SAID, DEFAULT, SETTING, FOUND)

# 2.1에서 두뇌가 낼 수 있는 일 (설계 B4.2 "2.1 intents"). 이 밖의 일은 두뇌가 무엇이라 해도 받지 않는다.
OPS_2_1 = ("mark", "jump_to", "mark_pauses", "mark_spikes", "clear_marks", "undo", "remove_all_ours", "save_slot",
           "help", "status")
EDIT_OPS = ("mark", "mark_pauses", "mark_spikes", "clear_marks")  # 카드를 거쳐 리졸브에 넣는 일


@dataclass
class Chip:
    """입력 칸을 채우는 말 (누르면 채우기만 하고 보내지 않는다).

    key: "example:<이름>"(예문), "text"(data["text"] 그대로: 사용자가 쓴 말의 일부),
    "tc_elapsed"/"tc_resolve"(시간 말 하나를 풀어 쓴 글: data의 text, raw, seconds, tc).
    """

    key: str
    data: Dict[str, Any] = field(default_factory=dict)


@dataclass
class RequestedScope:
    """사용자가 말한 범위 (설계 B4.1 requested_scope). 범위 지킴이가 이것과 계획의 결과를 비교한다.

    range: 절대 프레임 [lo, hi). None이면 범위를 말하지 않음 (전체).
    range_src: said / around / relative / whole / in_out / point / playhead / none.
    said_whole: "전체"라고 말했음 (전체 경고를 띄우지 않는다).
    tracks: 말한 트랙 번호 (None = 말하지 않음). count_hint: "여기", "한 곳", "N개만"이면 그 수.
    """

    range: Optional[Tuple[int, int]] = None
    range_src: str = "none"
    said_whole: bool = False
    tracks: Optional[List[int]] = None
    count_hint: Optional[int] = None


@dataclass
class Command:
    """할 일 하나 (카탈로그의 op와 인자). provenance는 인자마다 출처."""

    op: str
    params: Dict[str, Any] = field(default_factory=dict)
    provenance: Dict[str, str] = field(default_factory=dict)
    scope: RequestedScope = field(default_factory=RequestedScope)
    clause: str = ""  # 이 일을 만든 말 (사용자가 쓴 그대로)
    span: Tuple[int, int] = (0, 0)
    offer: Optional[str] = None  # 못 하는 부탁 대신 권하는 표시: "cut"(보라) / "audio"(노랑)
    notes: List[Tuple[str, Dict[str, Any]]] = field(default_factory=list)  # 카드에 적을 줄 (code, data)

    def to_dict(self) -> Dict[str, Any]:
        return {"op": self.op, "params": dict(self.params), "provenance": dict(self.provenance),
                "scope": {"range": list(self.scope.range) if self.scope.range else None,
                          "range_src": self.scope.range_src, "said_whole": self.scope.said_whole,
                          "tracks": self.scope.tracks, "count_hint": self.scope.count_hint},
                "clause": self.clause, "offer": self.offer}


@dataclass
class Reply:
    code: str
    data: Dict[str, Any] = field(default_factory=dict)
    chips: List[Chip] = field(default_factory=list)


@dataclass
class NeedTimeline(Reply):
    """타임라인을 알아야 풀 수 있음 (화면은 연결 확인 뒤 같은 글로 다시 부른다)."""


@dataclass
class Question:
    code: str
    data: Dict[str, Any] = field(default_factory=dict)
    chips: List[Chip] = field(default_factory=list)
    draft: Optional["ProposalDraft"] = None  # [예]를 누르면 할 일 (예: 12dB로 할까요?)


@dataclass
class ProposalDraft:
    """알아들은 일 목록. leftovers: 못 알아들은 부분 (사용자가 쓴 말 그대로, 카드에 주황 줄)."""

    commands: List[Command]
    text: str = ""
    leftovers: List[str] = field(default_factory=list)
    notes: List[Tuple[str, Dict[str, Any]]] = field(default_factory=list)

    @property
    def understood_all(self) -> bool:
        return not self.leftovers


@dataclass
class NotUnderstood:
    chips: List[Chip] = field(default_factory=list)
    text: str = ""


BrainResult = Union[Reply, Question, ProposalDraft, NotUnderstood]


@runtime_checkable
class ChatBrain(Protocol):
    """두뇌 (2.1: RuleBrain 하나, name "rules"). 2.2 이후의 AI 두뇌는 저마다 다른 name을 쓴다 (결과 파일·chat.jsonl에 적힘)."""

    name: str

    def handle(self, text: str, ctx: Any, cancel: Optional[threading.Event] = None) -> BrainResult:
        ...


def validate_draft(draft: ProposalDraft, allowed=OPS_2_1) -> List[str]:
    """이 판에서 할 수 없는 일(op)의 목록. 두뇌가 무엇을 냈든 이 목록에 있는 것은 하지 않는다."""
    return [c.op for c in draft.commands if c.op not in allowed]
