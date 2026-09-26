"""대화 기록 (설계 B4.1 "last 6 chat turns", B9 chat.jsonl).

- 최근 대화 몇 줄 (두뇌에 넘기는 turns)과 마지막 대화 카드 (이대로 저장, 방금 거 취소가 가리키는 것).
- chat.jsonl: 한 줄에 한 번 (v, 시각, 누가, 글, 두뇌, 결과). 타임라인을 알면 그 타임라인 폴더
  (…\\timelines\\<tl_key>\\chat.jsonl), 모르면 앱 기록 폴더 바로 아래. 쓰지 못해도 대화는 그대로 된다.
- 어느 두뇌가 답했는지 적어 둔다 (나중에 AI 두뇌가 생기면 무료로 끝난 몫을 셀 수 있게).
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

VERSION = 1
MAX_TURNS = 6
FILE_NAME = "chat.jsonl"


class ChatSession:
    def __init__(self, root: Optional[Path] = None, brain: str = "rules") -> None:
        self.root = Path(root) if root is not None else None
        self.brain = brain
        self.turns: List[Dict[str, Any]] = []
        self.last_card: Optional[Dict[str, Any]] = None
        self.write_errors = 0

    def path_for(self, tl_key: Optional[str]) -> Optional[Path]:
        if self.root is None:
            return None
        if tl_key:
            return self.root / "timelines" / tl_key / FILE_NAME
        return self.root / FILE_NAME

    def add(self, who: str, text: str, *, tl_key: Optional[str] = None, **extra: Any) -> Dict[str, Any]:
        """who: "me"(사용자) / "helper"(도우미). extra: result, code, commands, proposal_id ..."""
        row: Dict[str, Any] = {"v": VERSION, "at": time.strftime("%Y-%m-%dT%H:%M:%S"), "who": who, "text": text,
                               "brain": self.brain}
        if tl_key:
            row["tl_key"] = tl_key
        row.update({k: v for k, v in extra.items() if v is not None})
        self.turns.append(row)
        del self.turns[:-50]
        path = self.path_for(tl_key)
        if path is not None:
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                with open(path, "a", encoding="utf-8", newline="\n") as f:
                    f.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
            except OSError:
                self.write_errors += 1
        return row

    def recent(self, n: int = MAX_TURNS) -> List[Dict[str, Any]]:
        return [dict(t) for t in self.turns[-n:]]

    def remember_card(self, **card: Any) -> None:
        """마지막 대화 카드 (kind, params, proposal_id, scope, slot_kind ...)."""
        self.last_card = dict(card)
