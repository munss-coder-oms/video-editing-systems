"""리졸브와 주고받는 길(Transport).

2.1에는 Lua 스크립트 우체통(LuaTransport) 하나뿐이다. 2.2에서 파일로 주고받는 길(FileTransport)이
같은 모양으로 더해진다. ResolveOps는 이 모양만 알고, 어떤 길로 가는지는 모른다.
"""

from __future__ import annotations

import threading
from typing import Any, Dict, Optional

try:  # Python 3.8+
    from typing import Protocol
except ImportError:  # pragma: no cover
    Protocol = object  # type: ignore

from .bridge import LuaBridge


class Transport(Protocol):
    """리졸브에 작업 하나를 보내고 결과 표를 받는 길."""

    name: str

    def request(
        self,
        op: str,
        args: Optional[Dict[str, Any]] = None,
        *,
        timeout: Optional[float] = None,
        cancel: Optional[threading.Event] = None,
    ) -> Dict[str, Any]:
        ...

    def supports(self, op: str) -> Optional[bool]:
        ...


class LuaTransport:
    """리졸브 안 Lua 스크립트(AI_Helper_Connect)와 우체통으로 주고받는다."""

    name = "lua"

    def __init__(self, bridge: LuaBridge) -> None:
        self.bridge = bridge

    def request(
        self,
        op: str,
        args: Optional[Dict[str, Any]] = None,
        *,
        timeout: Optional[float] = None,
        cancel: Optional[threading.Event] = None,
    ) -> Dict[str, Any]:
        return self.bridge.request(op, args, timeout=timeout, cancel=cancel)

    def supports(self, op: str) -> Optional[bool]:
        return self.bridge.supports(op)
