"""타임라인마다 도우미가 넣은 것의 일지 (%LOCALAPPDATA%\\video-editing-systems\\timelines\\<tl_key>\\journal.json).

- 넣기 전에 "applying"으로 먼저 적고(무엇을 넣을지), 끝나면 결과(applied / partial)를 적는다.
  넣는 중에 앱이 꺼지거나 연결이 끊기면 "applying"이 남는다. 연결할 때마다 꼬리표를 찾아 맞춰 본다
  (startup reconciliation): 다 있으면 applied, 일부면 partial, 하나도 없으면 undone.
- 되돌리기는 그 일지의 타임라인에서만 한다. 다른 타임라인이 열려 있으면 거절한다 (꼬리표가 같아도).
- 표시 꼬리표는 "aih:<P>:<n>" (P = 제안 번호). Lua는 이 밖의 꼬리표 표시는 지우지 않는다.
- 여러 스레드(넣기 일과 연결 확인)가 같은 일지를 쓸 수 있다. 쓸 때는 한 자물쇠 안에서 파일을 다시 읽어
  이 쪽이 바꾼 줄만 덮어쓴다 (다른 쪽이 적은 영수증을 지우지 않게). 임시 파일 이름도 쓰는 쪽마다 다르다.
- 넣는 중인 제안(in_flight)은 맞춰 보기가 건드리지 않는다 (앱이 꺼져 남은 "넣는 중"만 맞춰 본다).
"""

from __future__ import annotations

import contextlib
import copy
import hashlib
import json
import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional

SCHEMA_VERSION = 1
FILE_NAME = "journal.json"
STATUSES = ("applying", "applied", "partial", "undone", "undo_failed")

MIGRATIONS: Dict[int, Callable[[Dict[str, Any]], Dict[str, Any]]] = {}

OTHER_TIMELINE_MESSAGE = "이 되돌리기는 {name}에서 할 수 있어요"
RECONCILED_UNDONE_MESSAGE = "넣다가 멈춘 것은 들어가지 않았어요"

_LOCK = threading.RLock()  # 일지 파일 쓰기는 이 앱 안에서 한 번에 하나
_IN_FLIGHT: Dict[str, int] = {}  # 지금 넣거나 지우는 중인 제안 번호 (맞춰 보기가 건드리지 않는다)


@contextlib.contextmanager
def in_flight(proposal_id: str):
    """넣기·지우기 일이 도는 동안: 연결 확인의 맞춰 보기가 이 제안을 "앱이 꺼져 남은 것"으로 보지 않게."""
    with _LOCK:
        _IN_FLIGHT[proposal_id] = _IN_FLIGHT.get(proposal_id, 0) + 1
    try:
        yield
    finally:
        with _LOCK:
            n = _IN_FLIGHT.get(proposal_id, 1) - 1
            if n > 0:
                _IN_FLIGHT[proposal_id] = n
            else:
                _IN_FLIGHT.pop(proposal_id, None)


def is_in_flight(proposal_id: Any) -> bool:
    with _LOCK:
        return proposal_id in _IN_FLIGHT


def timeline_key(
    project_uid: Optional[str],
    timeline_uid: Optional[str],
    project_name: Optional[str] = None,
    timeline_name: Optional[str] = None,
    start_frame: Optional[int] = None,
    fps: Optional[str] = None,
) -> str:
    """타임라인 번호(uid)가 있으면 sha1(프로젝트 uid|타임라인 uid), 없으면 이름·시작·속도로 만든 열 글자."""
    if project_uid and timeline_uid:
        raw = f"{project_uid}|{timeline_uid}"
    else:
        raw = f"{project_name or ''}|{timeline_name or ''}|{start_frame if start_frame is not None else ''}|{fps or ''}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:10]


def key_for(info: Any) -> str:
    """ops.TimelineInfo(또는 같은 이름의 속성을 가진 것)의 tl_key."""
    return timeline_key(
        getattr(info, "project_uid", None), getattr(info, "timeline_uid", None),
        getattr(info, "project", None), getattr(info, "timeline", None),
        getattr(info, "start_frame", None), getattr(info, "fps", None),
    )


def marker_prefix(proposal_id: str) -> str:
    return f"aih:{proposal_id}:"


def entry_op(entry: Dict[str, Any]) -> Optional[str]:
    """일지 한 줄의 일 이름 (mark_pauses, mark, clear_marks ...)."""
    cmds = entry.get("commands") or []
    return cmds[0].get("op") if cmds and isinstance(cmds[0], dict) else None


def proposal_of(custom: Any) -> Optional[str]:
    """꼬리표 "aih:<P>:<n>"의 제안 번호 P."""
    if not isinstance(custom, str) or not custom.startswith("aih:"):
        return None
    parts = custom.split(":")
    return parts[1] if len(parts) >= 3 and parts[1] else None


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


@dataclass
class Reconciled:
    proposal_id: str
    status: str
    expected: int
    found: int
    message: Optional[str] = None
    op: Optional[str] = None  # 일지 줄의 일 (clear_marks면 found는 없어진 표시 수)


class Journal:
    """타임라인 하나의 일지."""

    def __init__(self, root: Optional[Path], key: str, timeline: Optional[Dict[str, Any]] = None) -> None:
        if root is None:
            from ..resolve_link.paths import state_dir

            root = state_dir()
        self.key = key
        self.path = Path(root) / "timelines" / key / FILE_NAME
        self.data: Dict[str, Any] = {"schema_version": SCHEMA_VERSION, "timeline": dict(timeline or {}), "entries": []}
        self.moved_bad: Optional[Path] = None
        self._base: Dict[str, Dict[str, Any]] = {}  # 읽었을(썼을) 때의 줄: 이 쪽이 바꾼 줄을 알아낸다
        self.load()
        if timeline:
            # 이름은 바뀔 수 있으니 새로 본 이름으로 덮는다 (번호는 그대로)
            self.data["timeline"].update({k: v for k, v in timeline.items() if v is not None})

    @classmethod
    def for_timeline(cls, info: Any, root: Optional[Path] = None) -> "Journal":
        timeline = {
            "project_uid": getattr(info, "project_uid", None),
            "timeline_uid": getattr(info, "timeline_uid", None),
            "name": getattr(info, "timeline", None),
            "project": getattr(info, "project", None),
            "key": key_for(info),
        }
        return cls(root, key_for(info), timeline)

    # --- 파일 ---

    def _read(self) -> Optional[Dict[str, Any]]:
        """파일을 읽는다. 없으면 None, 깨졌으면 ValueError (UTF-8이 아닌 글자도)."""
        try:
            raw = self.path.read_bytes()
        except OSError:
            return None
        data = json.loads(raw.decode("utf-8"))
        if not isinstance(data, dict) or not isinstance(data.get("entries"), list):
            raise ValueError("shape")
        version = data.get("schema_version")
        if not isinstance(version, int) or version > SCHEMA_VERSION:
            raise ValueError("version")
        while version < SCHEMA_VERSION:
            data = MIGRATIONS[version](data)
            version = data["schema_version"]
        if not all(isinstance(e, dict) for e in data["entries"]):
            raise ValueError("entry")
        data.setdefault("timeline", {})
        if not isinstance(data["timeline"], dict):
            raise ValueError("timeline")
        return data

    def _remember_base(self) -> None:
        self._base = {str(e.get("proposal_id")): copy.deepcopy(e) for e in self.entries}

    def load(self) -> None:
        with _LOCK:
            try:
                data = self._read()
            except (ValueError, KeyError):
                stamp = time.strftime("%Y%m%d-%H%M%S")
                target = self.path.with_name(f"{FILE_NAME}.bad-{stamp}")
                try:
                    os.replace(self.path, target)
                    self.moved_bad = target
                except OSError:
                    pass
                return
            if data is None:
                return
            self.data = data
            self._remember_base()

    def refresh(self) -> None:
        """파일을 다시 읽어 다른 쪽(다른 스레드)이 그사이 적은 줄을 받는다. 이 쪽이 바꾼 줄은 그대로."""
        with _LOCK:
            try:
                disk = self._read()
            except (ValueError, KeyError):
                return
            if disk is None:
                return
            ours = {str(e.get("proposal_id")): e for e in self.entries}
            dirty = {pid for pid, e in ours.items() if self._base.get(pid) != e}
            self.data = self._merge(disk)
            for e in self.entries:
                pid = str(e.get("proposal_id"))
                if pid not in dirty:
                    self._base[pid] = copy.deepcopy(e)

    def _merge(self, disk: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        """파일에 있는 줄 + 이 쪽이 바꾸거나 새로 적은 줄. 이 쪽이 바꾸지 않은 줄은 파일의 것을 따른다."""
        if disk is None:
            return self.data
        ours = {str(e.get("proposal_id")): e for e in self.entries}
        dirty = {pid for pid, e in ours.items() if self._base.get(pid) != e}
        entries: List[Dict[str, Any]] = []
        on_disk = set()
        for e in disk["entries"]:
            pid = str(e.get("proposal_id"))
            on_disk.add(pid)
            mine = ours.get(pid)
            if mine is None:
                entries.append(e)
            elif pid in dirty:
                entries.append(mine)
            else:
                mine.clear()
                mine.update(e)  # 다른 쪽이 적은 것을 받는다 (이 쪽이 들고 있는 같은 줄도 바뀐다)
                entries.append(mine)
        entries += [e for pid, e in ours.items() if pid not in on_disk]
        timeline = dict(disk.get("timeline") or {})
        timeline.update(self.data.get("timeline") or {})
        merged = dict(disk)
        merged.update({"schema_version": SCHEMA_VERSION, "timeline": timeline, "entries": entries})
        return merged

    def save(self) -> None:
        """자물쇠 안에서 파일을 다시 읽어 합친 뒤 쓴다 (다른 스레드가 그사이 적은 줄을 지우지 않게)."""
        with _LOCK:
            try:
                disk = self._read()
            except (ValueError, KeyError):
                disk = None  # 깨진 파일은 이 쪽 것으로 덮는다
            self.data = self._merge(disk)
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_name(f"{FILE_NAME}.{os.getpid()}-{threading.get_ident()}.tmp")
            tmp.write_text(json.dumps(self.data, ensure_ascii=False, indent=1), encoding="utf-8")
            os.replace(tmp, self.path)
            self._remember_base()

    # --- 적기 ---

    @property
    def entries(self) -> List[Dict[str, Any]]:
        return self.data["entries"]

    @property
    def timeline(self) -> Dict[str, Any]:
        return self.data["timeline"]

    def entry(self, proposal_id: str) -> Optional[Dict[str, Any]]:
        for e in self.entries:
            if e.get("proposal_id") == proposal_id:
                return e
        return None

    def begin(
        self,
        proposal_id: str,
        *,
        origin: str,
        request: str,
        commands: List[Dict[str, Any]],
        expected_markers: Iterable[Dict[str, Any]] = (),
        card_rows: Optional[List[Any]] = None,
        plan_digest: Optional[str] = None,
    ) -> Dict[str, Any]:
        """넣기 전에 적는다 (앞서 쓰기). 이 뒤에 앱이 꺼지면 applying으로 남는다."""
        if self.entry(proposal_id) is not None:
            raise ValueError(f"이미 있는 제안 번호: {proposal_id}")
        markers = [dict(m) for m in expected_markers]
        prefix = marker_prefix(proposal_id)
        for m in markers:
            if not str(m.get("custom", "")).startswith(prefix):
                raise ValueError(f"꼬리표가 제안 번호와 맞지 않습니다: {m.get('custom')!r}")
        e = {
            "proposal_id": proposal_id, "at": _now(), "origin": origin, "request": request,
            "commands": copy.deepcopy(commands), "card_rows": card_rows or [], "plan_digest": plan_digest,
            "status": "applying", "depends_on": None,
            "expected": {"markers": markers},
            "created": {"markers": [], "tracks": [], "items": [], "media": [], "files": []},
            "changed": [], "deleted_snapshot": [], "after_fingerprint": None, "receipt": None,
        }
        self.entries.append(e)
        self.save()
        return e

    def finish(
        self,
        proposal_id: str,
        *,
        created_markers: Iterable[Dict[str, Any]] = (),
        receipt: Optional[Dict[str, Any]] = None,
        after_fingerprint: Optional[str] = None,
    ) -> Dict[str, Any]:
        e = self.entry(proposal_id)
        if e is None:
            raise KeyError(proposal_id)
        e["created"]["markers"] = [dict(m) for m in created_markers]
        expected = len(e["expected"]["markers"])
        placed = len(e["created"]["markers"])
        e["status"] = "applied" if placed >= expected else ("partial" if placed else "undone")
        e["receipt"] = receipt
        e["after_fingerprint"] = after_fingerprint
        self.save()
        return e

    # --- 지우기 (clear_marks): 넣은 것이 아니라 지운 것을 적는다 ---

    def begin_clear(self, proposal_id: str, *, origin: str, request: str, commands: List[Dict[str, Any]],
                    expected_deleted: Iterable[str], snapshot: Iterable[Dict[str, Any]] = (),
                    plan_digest: Optional[str] = None) -> Dict[str, Any]:
        """지우기 전에 적는다. expected_deleted: 지울 표시의 꼬리표 (모두 aih:로 시작).

        snapshot: 계산할 때 읽은 그 표시들 (지우다 앱이 꺼져도 되돌릴 수 있게 먼저 적어 둔다).
        """
        customs = [str(c) for c in expected_deleted]
        if any(not c.startswith("aih:") for c in customs):
            raise ValueError("도우미 꼬리표가 아닌 표시는 지우지 않습니다")
        e = self.begin(proposal_id, origin=origin, request=request, commands=commands, plan_digest=plan_digest)
        e["expected_deleted"] = customs
        e["deleted"] = []
        e["deleted_snapshot"] = [dict(r) for r in snapshot if str(r.get("custom", "")) in set(customs)]
        self.save()
        return e

    def finish_clear(self, proposal_id: str, *, gone: Iterable[str], snapshot: Iterable[Dict[str, Any]] = (),
                     receipt: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """지운 뒤: 다시 읽어 없어진 꼬리표(gone)와 되돌리기용 스냅숏(deleted_snapshot).

        snapshot은 리졸브가 지우면서 돌려준 줄. 거기 없는 꼬리표는 계산할 때 적어 둔 줄을 그대로 쓴다.
        """
        e = self.entry(proposal_id)
        if e is None:
            raise KeyError(proposal_id)
        gone = [str(c) for c in gone]
        expected = len(e.get("expected_deleted") or [])
        rows = {str(r.get("custom")): dict(r) for r in e.get("deleted_snapshot") or []}
        for r in snapshot:
            rows[str(r.get("custom"))] = dict(r)
        e["deleted"] = gone
        e["deleted_snapshot"] = [rows[c] for c in gone if c in rows]
        e["status"] = "applied" if len(gone) >= expected else ("partial" if gone else "undone")
        e["receipt"] = receipt
        self.save()
        return e

    def hold(self, proposal_id: str, *, receipt: Optional[Dict[str, Any]] = None,
             snapshot: Iterable[Dict[str, Any]] = (), after_fingerprint: Optional[str] = None) -> Dict[str, Any]:
        """넣기(지우기) 뒤 결과를 알 수 없음 (답이 끊기고 다시 읽기도 못 함): "넣는 중"으로 둔다.

        다음 연결 확인이 꼬리표로 맞춰 본다. 알게 된 것(영수증, 지우며 받은 표시 모양)은 적어 둔다.
        """
        e = self.entry(proposal_id)
        if e is None:
            raise KeyError(proposal_id)
        e["status"] = "applying"
        e["receipt"] = receipt
        if after_fingerprint is not None:
            e["after_fingerprint"] = after_fingerprint
        rows = {str(r.get("custom")): dict(r) for r in e.get("deleted_snapshot") or []}
        for r in snapshot:
            rows[str(r.get("custom"))] = dict(r)
        if rows:
            e["deleted_snapshot"] = list(rows.values())
        self.save()
        return e

    def mark(self, proposal_id: str, status: str) -> None:
        if status not in STATUSES:
            raise ValueError(status)
        e = self.entry(proposal_id)
        if e is None:
            raise KeyError(proposal_id)
        e["status"] = status
        self.save()

    # --- 연결할 때 맞춰 보기 ---

    def pending(self, include_in_flight: bool = False) -> List[Dict[str, Any]]:
        """"넣는 중"으로 남은 줄. 지금 이 앱이 넣고 있는 제안은 빼고 (include_in_flight면 넣는다)."""
        return [e for e in self.entries if e.get("status") == "applying"
                and (include_in_flight or not is_in_flight(e.get("proposal_id")))]

    def reconcile(self, markers: Iterable[Dict[str, Any]]) -> List[Reconciled]:
        """applying으로 남은 것을 타임라인의 꼬리표로 맞춰 본다. markers = get_markers 답 (그 줄들의 꼬리표를 빠짐없이).

        자물쇠 안에서 파일을 다시 읽은 뒤 본다: 표시를 읽는 사이 넣기 일이 시작했거나 끝낸 줄은 건드리지 않는다.
        """
        present: Dict[str, Dict[str, Any]] = {}
        for m in markers:
            c = m.get("custom") if isinstance(m, dict) else None
            if isinstance(c, str) and c.startswith("aih:"):
                present[c] = m
        with _LOCK:
            self.refresh()
            out = self._reconcile(present)
            if out:
                self.save()
        return out

    def _reconcile(self, present: Dict[str, Dict[str, Any]]) -> List[Reconciled]:
        out: List[Reconciled] = []
        for e in self.pending():
            pid = e["proposal_id"]
            if entry_op(e) == "clear_marks":
                # 지우다 멈춤: 지울 꼬리표 가운데 없어진 것을 센다
                want = [str(c) for c in e.get("expected_deleted") or []]
                gone = [c for c in want if c not in present]
                status = "applied" if want and len(gone) >= len(want) else ("partial" if gone else "undone")
                e["status"] = status
                e["deleted"] = gone
                e["reconciled_at"] = _now()
                out.append(Reconciled(pid, status, len(want), len(gone),
                                      RECONCILED_UNDONE_MESSAGE if status == "undone" else None, "clear_marks"))
                continue
            expected = [m.get("custom") for m in e["expected"]["markers"]]
            prefix = marker_prefix(pid)
            found_customs = [c for c in present if c.startswith(prefix)]
            if expected:
                found = [c for c in expected if c in present]
                n_expected = len(expected)
            else:
                found = found_customs
                n_expected = len(found_customs)
            if n_expected and len(found) >= n_expected:
                status = "applied"
            elif found:
                status = "partial"
            else:
                status = "undone"
            e["status"] = status
            e["created"]["markers"] = [
                {"frame": present[c].get("frame"), "custom": c} for c in found_customs
            ]
            e["reconciled_at"] = _now()
            out.append(Reconciled(pid, status, n_expected, len(found),
                                  RECONCILED_UNDONE_MESSAGE if status == "undone" else None, entry_op(e)))
        return out

    def pending_prefixes(self, limit: int = 10) -> List[str]:
        """맞춰 보기에 읽을 꼬리표 앞부분: 넣는 중인 제안마다 "aih:<P>:", 지우는 중이면 지울 표시의 제안들.

        너무 많으면(limit) "aih:" 하나로 (도우미 표시만 읽는다. 사용자 표시는 읽지 않는다).
        """
        prefixes: List[str] = []
        for e in self.pending():
            if entry_op(e) == "clear_marks":
                pids = [proposal_of(c) for c in e.get("expected_deleted") or []]
            else:
                pids = [str(e.get("proposal_id"))]
            for pid in pids:
                prefix = marker_prefix(pid) if pid else "aih:"
                if prefix not in prefixes:
                    prefixes.append(prefix)
        if len(prefixes) > limit or "aih:" in prefixes:
            return ["aih:"]
        return prefixes

    # --- 되돌리기 전 확인 ---

    def undo_blocker(self, current: Any) -> Optional[str]:
        """지금 열린 타임라인이 이 일지의 타임라인이 아니면 안내 글, 같으면 None."""
        want_uid = self.timeline.get("timeline_uid")
        cur_uid = getattr(current, "timeline_uid", None)
        if want_uid and cur_uid:
            same = want_uid == cur_uid
        else:
            same = key_for(current) == self.key
        if same:
            return None
        return OTHER_TIMELINE_MESSAGE.format(name=self.timeline.get("name") or "처음 넣은 타임라인")

    def summaries(self, n: int = 20) -> List[Dict[str, Any]]:
        out = []
        for e in self.entries[-n:]:
            receipt = e.get("receipt") or {}
            out.append({
                "proposal_id": e.get("proposal_id"), "at": e.get("at"), "origin": e.get("origin"),
                "request": e.get("request"), "status": e.get("status"), "op": entry_op(e),
                "markers": len(e.get("created", {}).get("markers", [])),
                "deleted": len(e.get("deleted") or []),
                "calls": receipt.get("calls") if isinstance(receipt, dict) else None,
            })
        return out


def all_journals(root: Optional[Path] = None) -> List[Journal]:
    """저장된 모든 일지 (결과 파일용). 폴더 이름이 tl_key다."""
    if root is None:
        from ..resolve_link.paths import state_dir

        root = state_dir()
    base = Path(root) / "timelines"
    out = []
    if base.is_dir():
        for folder in sorted(base.iterdir()):
            if (folder / FILE_NAME).is_file():
                out.append(Journal(root, folder.name))
    return out
