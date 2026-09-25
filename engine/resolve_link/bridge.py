"""리졸브 안의 Lua 스크립트에 일을 시키고 답을 받는다.

1. 우체통 폴더에 request.lua를 통째로 바꿔 넣는다 (반쯤 쓴 파일을 Lua가 읽지 않게).
2. Lua가 처리하고 답을 Fusion.prefs에 적는다.
3. 0.1초마다 Fusion.prefs를 읽어 같은 요청 번호의 답을 찾는다.

답이 없으면 BridgeTimeout (Scripts 메뉴를 아직 안 눌렀거나 리졸브에 대화 상자가 열려 있음),
Lua가 실패를 알리면 BridgeError. 우체통은 한 칸짜리라 요청은 한 번에 하나씩만 보낸다.

2차(스크립트 1.1.0)에서 더한 것
- 답을 받으면 request.lua를 지운다 (Lua가 0.1초마다 같은 파일을 다시 읽지 않게, S2).
- 20KB가 넘는 답 뒤에는 작은 ping을 한 번 더 보낸다 (Fusion.prefs에 큰 값이 남지 않게, S16).
- 작업마다 기다리는 시간이 다르고(OP_TIMEOUTS), 읽기 작업은 답이 없으면 한 번만 조용히 다시 보낸다.
- cancel(threading.Event)이 켜지면 기다리기를 그만둔다.
- ping 답의 ops 목록에 없는 작업은 보내지 않고 OldScript를 낸다 (예전 스크립트가 도는 중).
- 한가할 때 스스로 보내는 요청은 없다. 요청은 부르는 쪽(연결 관리)이 사용자 동작에 맞춰 보낸다.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

from . import SCRIPT_NAME
from .paths import find_fusion_prefs, mailbox_dir, state_dir
from .protocol import LEGACY_OPS, REQUEST_FILENAME, IdGenerator, Response, encode_request, parse_responses

# Fusion.prefs에 저장된 Lua의 Claim 값 (마지막으로 맡은 요청 번호)
_CLAIM_RE = re.compile(r"\bClaim\s*=\s*\"?(\d{1,16})\"?")
# 이보다 큰 번호는 받아들이지 않는다 (Lua 숫자로 정확히 전해지는 한계 2^53에 여유를 둠)
_MAX_SEED_ID = 2 ** 53 - 2 ** 32
# 답을 못 받은 요청 번호를 몇 개까지 기억할지 (늦게 온 답을 알아보려고)
_TIMED_OUT_KEEP = 200

log = logging.getLogger("engine.resolve_link")

POLL_INTERVAL = 0.1
# Lua가 request.lua를 읽는 동안에는 윈도우가 파일 바꾸기를 막는다. 그동안 다시 시도하는 시간.
_REPLACE_RETRY_SECONDS = 1.0
# 아직 답한 Fusion.prefs를 모를 때 후보를 다시 찾는 간격 (처음 연결 전에는 파일이 없을 수도 있다)
_RESEARCH_SECONDS = 5.0

# 작업마다 기다리는 시간(초). 없는 작업은 DEFAULT_TIMEOUT
DEFAULT_TIMEOUT = 15.0
OP_TIMEOUTS: Dict[str, float] = {
    "ping": 5.0,
    "stop": 5.0,
    "state": 30.0,
    "timeline_info": 30.0,
    "timeline_items": 30.0,
    "scope": 30.0,
    "probe_read": 30.0,
    "get_markers": 30.0,
    "add_marker": 15.0,
    "delete_markers": 30.0,
    "place_audio": 30.0,
    "remove_audio": 30.0,
    "probe_copy": 120.0,
    "switch_timeline": 15.0,
}
# 읽기만 하는 작업: 답이 없으면 한 번 더 보내도 리졸브에 두 번 무엇이 생기지 않는다
READ_OPS = frozenset({"state", "timeline_info", "timeline_items", "scope", "probe_read", "get_markers"})
# 이보다 큰 답 뒤에는 작은 ping을 보내 Fusion.prefs의 답 자리를 작게 바꿔 둔다 (S16)
LARGE_RESPONSE_BYTES = 20 * 1024

TIMEOUT_MESSAGE = (
    "리졸브가 대답하지 않습니다.\n"
    f"리졸브 위 메뉴에서 Workspace(워크스페이스) → Scripts(스크립트) → {SCRIPT_NAME}를 눌러 주세요.\n"
    "이미 눌렀다면 리졸브에 열려 있는 창(대화 상자)이 있는지 보고 닫아 주세요."
)


class LinkError(Exception):
    """리졸브 연결 문제 전체 (BridgeTimeout, BridgeError)."""


class BridgeTimeout(LinkError):
    """정해진 시간 안에 답이 없음.

    prefs_changed: 기다리는 동안 바뀌었는데 우리 답(이번 요청이나 예전에 못 받은 요청)을 찾지 못한
    Fusion.prefs. 비어 있지 않으면 Lua가 답을 썼는데 이 창이 못 읽은 것일 수 있다.
    """

    def __init__(
        self,
        op: str = "",
        message: str = TIMEOUT_MESSAGE,
        *,
        req_id: Optional[int] = None,
        prefs_changed: Optional[List[str]] = None,
    ) -> None:
        super().__init__(message)
        self.op = op
        self.req_id = req_id
        self.prefs_changed = list(prefs_changed or [])


CLOSING_MESSAGE = "앱을 닫는 중이라 리졸브의 답을 기다리지 않았습니다."
CANCEL_MESSAGE = "멈췄어요. 리졸브의 답은 기다리지 않아요."
OLD_SCRIPT_MESSAGE = (
    "리졸브 쪽 스크립트가 예전 판이에요. "
    f"Scripts → {SCRIPT_NAME}를 한 번 더 눌러 주세요"
)


class BridgeCancelled(BridgeTimeout):
    """앱을 닫는 중이거나 사용자가 멈춰서 기다리기를 그만둠."""

    def __init__(self, op: str = "", message: str = CLOSING_MESSAGE) -> None:
        super().__init__(op, message)


class BridgeError(LinkError):
    """Lua가 ok=false로 답함 (또는 요청 파일을 쓸 수 없음).

    payload: Lua가 준 답 전체 (calls: 함수마다 ok/err:내용, detail, size ...). 결과 파일에 남긴다.
    """

    def __init__(
        self,
        error: str,
        func: str = "",
        op: str = "",
        message: Optional[str] = None,
        payload: Optional[Dict[str, Any]] = None,
    ) -> None:
        if message is None:
            where = f" ({func})" if func else ""
            message = f"리졸브에서 작업이 실패했습니다: {error}{where}"
        super().__init__(message)
        self.error = error
        self.func = func
        self.op = op
        self.payload = dict(payload) if isinstance(payload, dict) else None


class OldScript(BridgeError):
    """리졸브에서 도는 스크립트가 이 작업을 모름 (예전 판). Scripts 메뉴를 다시 누르면 새 판이 돈다."""

    def __init__(self, op: str = "", payload: Optional[Dict[str, Any]] = None) -> None:
        super().__init__("unknown_op", "request", op, OLD_SCRIPT_MESSAGE, payload)


FileSig = Optional[Tuple[int, int]]


def _sig(path: Path) -> FileSig:
    try:
        st = path.stat()
    except OSError:
        return None
    return st.st_mtime_ns, st.st_size


class LuaBridge:
    """리졸브 안 Lua 루프와 주고받는 창구. 여러 스레드에서 불러도 요청은 차례대로 나간다."""

    def __init__(
        self,
        mailbox: Optional[Path] = None,
        prefs_files: Optional[Sequence[Path]] = None,
        clock: Callable[[], float] = time.monotonic,
        *,
        backup_dir: Optional[Path] = None,
        poll_interval: float = POLL_INTERVAL,
    ) -> None:
        self.mailbox = Path(mailbox) if mailbox is not None else mailbox_dir()
        self._fixed_prefs = [Path(p) for p in prefs_files] if prefs_files is not None else None
        self._clock = clock
        self._backup_dir = Path(backup_dir) if backup_dir is not None else None
        self._poll = poll_interval
        self._ids = IdGenerator()
        self._lock = threading.Lock()
        self._closed = threading.Event()
        self._prefs_cache: Optional[List[Path]] = None
        self._searched_at = 0.0
        self._backup_done = False
        self._seeded_from: Optional[Tuple[Path, ...]] = None
        # 답을 못 받은 요청 (번호 → 작업 이름)과, 그 가운데 나중에 답이 온 것 (결과 파일용)
        self.timed_out: Dict[int, str] = {}
        self.late_answers: List[Dict[str, Any]] = []
        # 마지막으로 답한 Fusion.prefs, Lua 루프 표시, 스크립트 버전, 원래 답 전체
        self.prefs_path: Optional[Path] = None
        self.owner: Optional[str] = None
        self.script_version: Optional[str] = None
        self.last_response: Optional[Dict[str, Any]] = None
        self.connected = False
        # 지금 도는 스크립트가 아는 작업 (ping 답의 ops). None이면 아직 모름
        self.known_ops: Optional[frozenset] = None
        # 보낸 요청 수, 큰 답 뒤에 보낸 작은 ping 수, 조용히 다시 보낸 읽기 수 (시험·결과 파일용)
        self.request_count = 0
        self.cleanup_pings = 0
        self.read_retries = 0

    @property
    def request_path(self) -> Path:
        return self.mailbox / REQUEST_FILENAME

    # ── Fusion.prefs 찾기와 백업 ───────────────────────────────────────

    def prefs_candidates(self) -> List[Path]:
        if self._fixed_prefs is not None:
            return list(self._fixed_prefs)
        now = self._clock()
        stale = self.prefs_path is None and now - self._searched_at >= _RESEARCH_SECONDS
        if self._prefs_cache is None or stale:
            found = find_fusion_prefs()
            if self.prefs_path is not None and self.prefs_path not in found:
                found.insert(0, self.prefs_path)
            self._prefs_cache = found
            self._searched_at = now
        return list(self._prefs_cache)

    def backup_dir(self) -> Path:
        return self._backup_dir if self._backup_dir is not None else state_dir() / "backup"

    def has_backup(self) -> bool:
        folder = self.backup_dir()
        return folder.is_dir() and any(folder.glob("Fusion.prefs.*.bak"))

    def backup_prefs(self) -> List[Path]:
        """처음 연결하기 전에 Fusion.prefs를 한 번만 복사해 둔다 (이미 백업이 있으면 건너뜀)."""
        if self.has_backup():
            return []
        folder = self.backup_dir()
        sources = [p for p in self.prefs_candidates() if p.is_file()]
        if not sources:
            return []
        written: List[Path] = []
        lines = []
        try:
            folder.mkdir(parents=True, exist_ok=True)
            for n, src in enumerate(sources, start=1):
                dst = folder / f"Fusion.prefs.{n}.bak"
                shutil.copy2(src, dst)
                written.append(dst)
                lines.append(f"{dst.name}\t{src}")
            (folder / "Fusion.prefs.index.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
        except OSError as exc:
            log.warning("Fusion.prefs 백업 실패: %s", exc)
        return written

    # ── 요청 번호 ────────────────────────────────────────────────────

    def _seed_ids(self, candidates: Sequence[Path]) -> None:
        """Fusion.prefs에 남은 가장 큰 요청 번호(답, Claim)보다 큰 번호부터 보낸다.

        번호는 밀리초 시각이라, PC 시계가 뒤로 가면 새 번호가 예전에 저장된 Claim보다 작아진다.
        그러면 Lua는 이미 처리한 요청으로 보고 아무 답도 하지 않는다. 후보 파일이 바뀔 때마다 다시 본다.
        """
        key = tuple(candidates)
        if key == self._seeded_from:
            return
        self._seeded_from = key
        highest = 0
        for path in candidates:
            try:
                text = path.read_bytes().decode("latin-1")
            except OSError:
                continue
            for resp in parse_responses(text):
                highest = max(highest, resp.id)
            for m in _CLAIM_RE.finditer(text):
                highest = max(highest, int(m.group(1)))
        if 0 < highest < _MAX_SEED_ID:
            self._ids.ensure_above(highest)

    def _remember_timeout(self, req_id: int, op: str) -> None:
        self.timed_out[req_id] = op
        while len(self.timed_out) > _TIMED_OUT_KEEP:
            self.timed_out.pop(next(iter(self.timed_out)))

    def note_late_answers(self, path: Path, responses: Iterable[Response]) -> bool:
        """예전에 답을 못 받은 요청의 답이 이제 보이면 기록한다. 하나라도 있으면 True."""
        found = False
        known = {(a["id"], a["owner"]) for a in self.late_answers}
        for resp in responses:
            op = self.timed_out.get(resp.id)
            if op is None:
                continue
            found = True
            if (resp.id, resp.owner) in known:
                continue
            known.add((resp.id, resp.owner))
            self.late_answers.append({
                "id": resp.id, "op": op, "owner": resp.owner, "file": str(path),
                "ok": resp.data.get("ok"), "error": resp.data.get("error"),
                "seen_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            })
        return found

    # ── 요청 보내기 ───────────────────────────────────────────────────

    def _write_request(self, text: str, op: str) -> None:
        self.mailbox.mkdir(parents=True, exist_ok=True)
        tmp = self.mailbox / (REQUEST_FILENAME + ".tmp")
        with open(tmp, "w", encoding="ascii", newline="\n") as f:
            f.write(text)
        deadline = time.monotonic() + _REPLACE_RETRY_SECONDS
        while True:
            try:
                os.replace(tmp, self.request_path)
                return
            except PermissionError as exc:
                if time.monotonic() >= deadline:
                    raise BridgeError(
                        "write_failed", REQUEST_FILENAME, op,
                        f"요청 파일을 쓸 수 없습니다. 다른 프로그램이 파일을 쓰고 있는지 확인해 주세요.\n{exc}",
                    ) from exc
                time.sleep(0.05)

    def _discard_request(self, text: str) -> None:
        """답이 없던 요청은 지운다. 나중에 메뉴를 눌렀을 때 뒤늦게 실행되지 않게."""
        try:
            if self.request_path.read_text(encoding="ascii", errors="replace") == text:
                self.request_path.unlink()
        except OSError:
            pass

    def _scan(
        self,
        req_id: int,
        seen: Dict[Path, FileSig],
        initial: Dict[Path, FileSig],
        unread: Dict[Path, bool],
    ) -> Optional[Tuple[Path, Response]]:
        """바뀐 Fusion.prefs에서 req_id의 답을 찾는다.

        unread에는 요청을 쓴 뒤 바뀌었는데 우리 답이 하나도 없던 파일을 적는다 (답을 못 읽는 문제 알아보기).
        """
        for path in self.prefs_candidates():
            sig = _sig(path)
            if sig is None:
                continue
            # 바뀐 파일만 읽는다. 단 지난번에 답한 파일은 시각이 그대로여도 늘 다시 읽는다.
            if path != self.prefs_path and path in seen and seen[path] == sig:
                continue
            seen[path] = sig
            try:
                text = path.read_bytes().decode("latin-1")
            except OSError:
                seen.pop(path, None)  # 리졸브가 쓰는 중: 다음 차례에 다시 읽는다
                continue
            responses = parse_responses(text)
            for resp in responses:
                if resp.id == req_id:
                    unread.pop(path, None)
                    return path, resp
            late = self.note_late_answers(path, responses)
            if f":{req_id}:" in text:
                seen.pop(path, None)  # 우리 답이 반쯤 쓰인 상태: 시각이 같아도 다음 차례에 다시 읽는다
            if sig != initial.get(path):
                if late:
                    unread.pop(path, None)  # 늦게 온 예전 답: 읽을 수는 있다
                else:
                    unread[path] = True
        return None

    def supports(self, op: str) -> Optional[bool]:
        """지금 스크립트가 op를 아는지. 아직 모르면(ping 전) None."""
        if self.known_ops is None:
            return None
        return op in self.known_ops

    def request(
        self,
        op: str,
        args: Optional[Dict[str, Any]] = None,
        timeout: Optional[float] = None,
        cancel: Optional[threading.Event] = None,
        retry: Optional[bool] = None,
    ) -> Dict[str, Any]:
        """작업 하나를 보내고 답(result 표)을 돌려준다.

        timeout: 없으면 OP_TIMEOUTS. retry: 없으면 읽기 작업(READ_OPS)만 답이 없을 때 한 번 더.
        cancel: 켜지면 BridgeCancelled. 스크립트가 모르는 작업이면 OldScript (보내지 않는다).
        """
        if op not in ("ping", "stop") and self.known_ops is not None and op not in self.known_ops:
            raise OldScript(op)
        wait = OP_TIMEOUTS.get(op, DEFAULT_TIMEOUT) if timeout is None else float(timeout)
        again = (op in READ_OPS) if retry is None else bool(retry)
        with self._lock:
            try:
                result, size = self._request_once(op, args, wait, cancel)
            except BridgeCancelled:
                raise
            except BridgeTimeout:
                if not again:
                    raise
                self.read_retries += 1
                log.info("%s 답이 없어 한 번 더 보냄", op)
                result, size = self._request_once(op, args, wait, cancel)
            if size > LARGE_RESPONSE_BYTES and op not in ("ping", "stop"):
                # 큰 답이 Fusion.prefs에 남아 있지 않게 작은 답으로 덮는다. 실패해도 이번 결과는 그대로
                try:
                    self._request_once("ping", None, OP_TIMEOUTS["ping"], cancel)
                    self.cleanup_pings += 1
                except LinkError as exc:
                    log.info("큰 답 뒤 ping 실패: %s", exc)
            return result

    def _request_once(
        self,
        op: str,
        args: Optional[Dict[str, Any]],
        timeout: float,
        cancel: Optional[threading.Event],
    ) -> Tuple[Dict[str, Any], int]:
        if self._closed.is_set():
            raise BridgeCancelled(op)
        if cancel is not None and cancel.is_set():
            raise BridgeCancelled(op, CANCEL_MESSAGE)
        self._seed_ids(self.prefs_candidates())
        req_id = self._ids.next()
        text = encode_request(req_id, op, args)
        if not self._backup_done:
            # 백업을 만들었거나 이미 있으면 끝. Fusion.prefs를 아직 못 찾았으면 다음 요청 때 다시 해 본다.
            self._backup_done = bool(self.backup_prefs()) or self.has_backup()
        # 요청을 쓰기 전 상태를 기억해 두고, 그 뒤에 바뀐 Fusion.prefs만 읽는다.
        initial: Dict[Path, FileSig] = {p: _sig(p) for p in self.prefs_candidates()}
        seen = dict(initial)
        unread: Dict[Path, bool] = {}
        self._write_request(text, op)
        self.request_count += 1
        deadline = self._clock() + timeout
        while True:
            hit = self._scan(req_id, seen, initial, unread)
            if hit is not None:
                return self._accept(op, text, *hit)
            if self._clock() >= deadline:
                break
            if cancel is not None and cancel.is_set():
                self._discard_request(text)
                raise BridgeCancelled(op, CANCEL_MESSAGE)
            if self._closed.wait(self._poll):
                self._discard_request(text)
                raise BridgeCancelled(op)
        self._discard_request(text)
        self._remember_timeout(req_id, op)
        self.connected = False
        raise BridgeTimeout(op, req_id=req_id, prefs_changed=[str(p) for p in unread])

    def _accept(self, op: str, text: str, path: Path, resp: Response) -> Tuple[Dict[str, Any], int]:
        # 답을 받은 요청 파일은 지운다 (S2). 그사이 다른 요청으로 바뀌었으면 그대로 둔다.
        self._discard_request(text)
        self.prefs_path = path
        if resp.owner != self.owner and op != "ping":
            self.known_ops = None  # 스크립트를 다시 눌렀다: 다음 ping 때 다시 안다
        self.owner = resp.owner
        self.last_response = resp.data
        sv = resp.data.get("sv")
        self.script_version = sv if isinstance(sv, str) else None
        self.connected = True
        try:
            size = len(json.dumps(resp.data, ensure_ascii=False).encode("utf-8"))
        except (TypeError, ValueError):
            size = 0
        if not resp.data.get("ok"):
            error = str(resp.data.get("error") or "unknown")
            if error == "unknown_op":
                raise OldScript(op, payload=resp.data)
            raise BridgeError(error, str(resp.data.get("func") or ""), op, payload=resp.data)
        result = resp.data.get("result")
        result = result if isinstance(result, dict) else {}
        if op == "ping":
            ops = result.get("ops")
            if isinstance(ops, list) and all(isinstance(x, str) for x in ops):
                self.known_ops = frozenset(ops)
            else:
                self.known_ops = frozenset(LEGACY_OPS)  # 1.0.0 스크립트는 목록을 주지 않는다
        return result, size

    def close(self) -> None:
        """앱을 닫을 때: 기다리는 요청을 바로 끝내고 더 보내지 않는다."""
        self._closed.set()

    # ── 작업별 바로가기 ───────────────────────────────────────────────

    def ping(self, timeout: Optional[float] = None) -> Dict[str, Any]:
        return self.request("ping", timeout=timeout)

    def state(self, timeout: Optional[float] = None) -> Dict[str, Any]:
        return self.request("state", timeout=timeout)

    def add_marker(
        self,
        frame: int,
        color: str = "Yellow",
        name: str = "",
        note: str = "",
        custom: str = "",
        duration: int = 1,
        timeout: Optional[float] = None,
    ) -> Dict[str, Any]:
        args = {"frame": int(frame), "color": color, "name": name, "note": note,
                "custom": custom, "duration": max(1, int(duration))}
        return self.request("add_marker", args, timeout=timeout)

    def get_markers(self, timeout: Optional[float] = None) -> Dict[str, Any]:
        return self.request("get_markers", timeout=timeout)

    def delete_markers(self, custom: str, timeout: Optional[float] = None) -> Dict[str, Any]:
        return self.request("delete_markers", {"custom": custom}, timeout=timeout)

    def place_audio(
        self, path, track_name: str, record_frame: int, frames: int, timeout: Optional[float] = None
    ) -> Dict[str, Any]:
        args = {"path": str(path), "track_name": track_name,
                "record_frame": int(record_frame), "frames": int(frames)}
        return self.request("place_audio", args, timeout=timeout)

    def remove_audio(self, track_name: str, timeout: Optional[float] = None) -> Dict[str, Any]:
        return self.request("remove_audio", {"track_name": track_name}, timeout=timeout)

    def stop(self, timeout: Optional[float] = None) -> Dict[str, Any]:
        return self.request("stop", timeout=timeout)


def prefs_backups(folder: Optional[Path] = None) -> Iterable[Path]:
    """만들어 둔 Fusion.prefs 백업 파일 (점검·보고용)."""
    folder = folder if folder is not None else state_dir() / "backup"
    return sorted(folder.glob("Fusion.prefs.*.bak")) if folder.is_dir() else []
