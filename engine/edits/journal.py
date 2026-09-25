"""타임라인마다 도우미가 넣은 것의 일지 (%LOCALAPPDATA%\\video-editing-systems\\timelines\\<tl_key>\\journal.json).

- 넣기 전에 "applying"으로 먼저 적고(무엇을 넣을지), 끝나면 결과(applied / partial)를 적는다.
  넣는 중에 앱이 꺼지거나 연결이 끊기면 "applying"이 남는다. 연결할 때마다 꼬리표를 찾아 맞춰 본다
  (startup reconciliation): 다 있으면 applied, 일부면 partial, 하나도 없으면 undone.
- 되돌리기는 그 일지의 타임라인에서만 한다. 다른 타임라인이 열려 있으면 거절한다 (꼬리표가 같아도).
- 표시 꼬리표는 "aih:<P>:<n>" (P = 제안 번호). Lua는 이 밖의 꼬리표 표시는 지우지 않는다.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
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


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


@dataclass
class Reconciled:
    proposal_id: str
    status: str
    expected: int
    found: int
    message: Optional[str] = None


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

    def load(self) -> None:
        try:
            raw = self.path.read_text(encoding="utf-8")
        except OSError:
            return
        try:
            data = json.loads(raw)
            if not isinstance(data, dict) or not isinstance(data.get("entries"), list):
                raise ValueError("shape")
            version = data.get("schema_version")
            if not isinstance(version, int) or version > SCHEMA_VERSION:
                raise ValueError("version")
            while version < SCHEMA_VERSION:
                data = MIGRATIONS[version](data)
                version = data["schema_version"]
        except (ValueError, KeyError):
            stamp = time.strftime("%Y%m%d-%H%M%S")
            target = self.path.with_name(f"{FILE_NAME}.bad-{stamp}")
            try:
                os.replace(self.path, target)
                self.moved_bad = target
            except OSError:
                pass
            return
        data.setdefault("timeline", {})
        self.data = data

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(FILE_NAME + ".tmp")
        tmp.write_text(json.dumps(self.data, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, self.path)

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

    def mark(self, proposal_id: str, status: str) -> None:
        if status not in STATUSES:
            raise ValueError(status)
        e = self.entry(proposal_id)
        if e is None:
            raise KeyError(proposal_id)
        e["status"] = status
        self.save()

    # --- 연결할 때 맞춰 보기 ---

    def pending(self) -> List[Dict[str, Any]]:
        return [e for e in self.entries if e.get("status") == "applying"]

    def reconcile(self, markers: Iterable[Dict[str, Any]]) -> List[Reconciled]:
        """applying으로 남은 것을 타임라인의 꼬리표로 맞춰 본다. markers = get_markers 답."""
        present: Dict[str, Dict[str, Any]] = {}
        for m in markers:
            c = m.get("custom") if isinstance(m, dict) else None
            if isinstance(c, str) and c.startswith("aih:"):
                present[c] = m
        out: List[Reconciled] = []
        for e in self.pending():
            pid = e["proposal_id"]
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
                                  RECONCILED_UNDONE_MESSAGE if status == "undone" else None))
        if out:
            self.save()
        return out

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
                "request": e.get("request"), "status": e.get("status"),
                "markers": len(e.get("created", {}).get("markers", [])),
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
