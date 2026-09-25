"""앱 ↔ Lua 우체통의 글 형식.

요청(request.lua)은 `return {v=1,id=..,t=..,op="..",a={...}}` 한 줄이다.
문자열은 모두 UTF-8 바이트의 16진수(0-9a-f)로 적는다. 그래서 따옴표, ]], 역슬래시,
줄바꿈, 한글이 무엇이 들어와도 Lua 문법을 깨뜨리거나 코드로 실행될 수 없다.

답은 Fusion.prefs 안에 `AIH1:<owner>:<id>:<JSON의 16진수>`로 적힌다.
16진수라서 Fusion이 문자열을 어떻게 이스케이프하든 신경 쓰지 않아도 된다.
"""

from __future__ import annotations

import json
import math
import re
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Mapping, Optional

PROTOCOL_VERSION = 1
REQUEST_FILENAME = "request.lua"

# Lua가 처리하는 작업 목록 (이 밖의 이름은 보내지 않는다).
# Lua 스크립트의 OPS_ALLOWED와 같아야 한다 (tests/test_lua_script.py가 맞춰 본다).
OPS = (
    "ping",
    "state",
    "timeline_info",
    "timeline_items",
    "scope",
    "probe_read",
    "probe_copy",
    "switch_timeline",
    "add_marker",
    "add_markers",
    "get_markers",
    "delete_markers",
    "place_audio",
    "remove_audio",
    "stop",
)

# 1.0.0 스크립트(1차 시험판)가 아는 작업. ping 답에 ops 목록이 없으면 이것으로 본다.
LEGACY_OPS = (
    "ping",
    "state",
    "add_marker",
    "get_markers",
    "delete_markers",
    "place_audio",
    "remove_audio",
    "stop",
)

# 요청 한 개의 최대 크기. 이보다 크면 보내지 않는다 (Lua가 한 번에 읽는 파일이 너무 커지지 않게).
MAX_REQUEST_BYTES = 256 * 1024

RESPONSE_RE = re.compile(r"AIH1:([0-9A-Za-z_]+):(\d+):([0-9a-f]*)")

_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_LUA_KEYWORDS = frozenset(
    "and break do else elseif end false for function if in local nil not or repeat "
    "return then true until while goto".split()
)
# Lua(LuaJIT) 숫자는 double이라 이보다 큰 정수는 정확히 전해지지 않는다.
_MAX_EXACT_INT = 2 ** 53
_MAX_DEPTH = 20


def to_hex(text: str) -> str:
    """문자열 → UTF-8 바이트의 16진수 (소문자)."""
    return text.encode("utf-8").hex()


def from_hex(hex_text: str, errors: str = "strict") -> str:
    """16진수 → 문자열. 길이가 홀수이거나 16진수가 아니면 ValueError."""
    if len(hex_text) % 2:
        raise ValueError("16진수 길이가 홀수입니다")
    return bytes.fromhex(hex_text).decode("utf-8", errors)


def _lua_value(value: Any, depth: int) -> str:
    if depth > _MAX_DEPTH:
        raise ValueError("요청 안의 표가 너무 깊습니다")
    # bool은 int의 한 종류이므로 먼저 본다.
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        if abs(value) > _MAX_EXACT_INT:
            raise ValueError(f"정수가 너무 큽니다: {value}")
        return repr(int(value))
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"보낼 수 없는 숫자입니다: {value}")
        return format(value, ".17g")
    if isinstance(value, str):
        return '"' + to_hex(value) + '"'
    if isinstance(value, Mapping):
        parts = []
        for key, item in value.items():
            if not isinstance(key, str) or not _IDENT_RE.match(key):
                raise ValueError(f"요청의 이름으로 쓸 수 없습니다: {key!r}")
            if item is None:
                continue  # Lua에서 nil은 '없음'과 같다
            # end 같은 Lua 예약어는 ["end"]= 꼴로 적어야 문법 오류가 나지 않는다.
            name = f'["{key}"]' if key in _LUA_KEYWORDS else key
            parts.append(f"{name}={_lua_value(item, depth + 1)}")
        return "{" + ",".join(parts) + "}"
    if isinstance(value, (list, tuple)):
        items = []
        for item in value:
            if item is None:
                raise ValueError("목록 안에 빈 값(None)은 보낼 수 없습니다")
            items.append(_lua_value(item, depth + 1))
        return "{" + ",".join(items) + "}"
    raise TypeError(f"요청에 넣을 수 없는 값입니다: {type(value).__name__}")


def encode_request(
    req_id: int,
    op: str,
    args: Optional[Mapping[str, Any]] = None,
    *,
    t: Optional[int] = None,
) -> str:
    """request.lua 내용 한 줄을 만든다.

    t는 보낸 시각(유닉스 초). Lua는 처음 켜질 때 이 값으로 오래된 요청을 거른다.
    """
    if op not in OPS:
        raise ValueError(f"알 수 없는 작업입니다: {op!r}")
    if isinstance(req_id, bool) or not isinstance(req_id, int) or req_id <= 0:
        raise ValueError(f"요청 번호가 잘못되었습니다: {req_id!r}")
    if t is None:
        t = int(time.time())
    body = _lua_value(dict(args or {}), 0)
    text = (
        f"return {{v={PROTOCOL_VERSION},id={_lua_value(req_id, 0)},t={_lua_value(int(t), 0)},"
        f'op="{op}",a={body}}}'
    )
    if len(text.encode("utf-8")) > MAX_REQUEST_BYTES:
        raise ValueError(f"요청이 너무 큽니다: {len(text)}바이트 (최대 {MAX_REQUEST_BYTES})")
    return text


@dataclass(frozen=True)
class Response:
    """Fusion.prefs에서 읽은 답 하나."""

    owner: str  # 답한 Lua 루프 (메뉴를 누를 때마다 바뀜)
    id: int
    data: Dict[str, Any]  # {"ok": .., "sv": .., "result"/"error"/"func": ..}


def decode_payload(hex_text: str) -> Dict[str, Any]:
    """답의 16진수 부분 → JSON 사전. 반쯤 쓰인 파일이면 ValueError."""
    text = from_hex(hex_text, errors="replace")
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError("답이 JSON 객체가 아닙니다")
    return data


def parse_responses(text: str) -> List[Response]:
    """Fusion.prefs 내용에서 온전한 답을 모두 찾는다.

    16진수 길이가 홀수이거나 JSON이 깨진 것(아직 쓰는 중인 파일)은 건너뛴다.
    """
    found: List[Response] = []
    for owner, id_text, hex_text in RESPONSE_RE.findall(text):
        try:
            data = decode_payload(hex_text)
        except ValueError:
            continue
        found.append(Response(owner=owner, id=int(id_text), data=data))
    return found


class IdGenerator:
    """요청 번호. 앱을 다시 켜도 계속 커지도록 밀리초 시각을 바탕으로 한다."""

    def __init__(self, last: int = 0, clock_ns: Callable[[], int] = time.time_ns) -> None:
        self.last = int(last)
        self._clock_ns = clock_ns
        self._lock = threading.Lock()

    def next(self) -> int:
        with self._lock:
            self.last = max(self.last + 1, self._clock_ns() // 1_000_000)
            return self.last

    def ensure_above(self, value: int) -> None:
        """다음 번호가 value보다 크게 한다 (예전에 쓴 번호가 지금 시각보다 클 때)."""
        with self._lock:
            self.last = max(self.last, int(value))

    __next__ = next

    def __iter__(self) -> "IdGenerator":
        return self
