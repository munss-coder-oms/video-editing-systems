"""도우미 창 설정 (%LOCALAPPDATA%\\video-editing-systems\\settings.json).

- 판 번호(schema_version)가 있고, 예전 판은 MIGRATIONS로 올린다.
- 쓸 때는 임시 파일에 쓴 뒤 바꿔 넣는다 (쓰다 꺼져도 반쪽 파일이 남지 않게).
- 깨졌거나 더 새 판이 쓴 파일은 settings.json.bad-<시각>으로 옮기고 기본값으로 시작한다. 앱은 멈추지 않는다.
- 자동화 버튼(slots) 목록의 순서가 곧 화면 순서다. 버튼마다 바로 전 설정(previous) 한 단계를 기억한다.

화면(Qt)을 모르는 순수 모듈이다 (QSettings는 판 번호가 없고 Qt 없이 시험할 수 없어 쓰지 않는다).
"""

from __future__ import annotations

import copy
import json
import os
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

SCHEMA_VERSION = 1
FILE_NAME = "settings.json"

# 채팅에서 저장할 때 버리는 값 (구간은 그때 한 번만 쓰는 것이라 버튼에 남기지 않는다)
RANGE_KEYS = ("range", "start_s", "end_s", "range_start", "range_end", "in_s", "out_s")


def default_settings() -> Dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "automations": {"slots": [
            {"slot": 1, "kind": "mark_pauses", "name": "쉬는 곳 표시", "confirm": True,
             "compute_on_connect": False, "previous": None,
             "params": {"min_s": 1.5, "pad_s": 0.2, "below_lu": 25, "as_range": True, "color": "Blue",
                        "name": "쉼", "max": 200, "scope": "whole"}},
            {"slot": 2, "kind": "mark_spikes", "name": "튀는 소리 표시", "confirm": True,
             "compute_on_connect": False, "previous": None,
             "params": {"above_lu": 8, "merge_s": 1.0, "color": "Red", "max": 50, "scope": "whole"}},
            {"slot": 3, "kind": "balance_voice", "name": "소리 고르게", "confirm": True,
             "compute_on_connect": False, "previous": None,
             "params": {"target_lufs": -14, "true_peak": -1, "peaks": "medium", "lift": "medium",
                        "originals": "disable", "mark_tamed": True, "marker_color": "Yellow", "scope": "whole"}},
        ]},
        "voice": {"by_layout": {}, "profiles": {}, "ask_again": True},
        "chat": {"brain": "rules", "range_edit_mode": None, "default_marker_color": "Green",
                 "claude_code": {"enabled": False, "daily_cap": 50, "model": None},
                 "api": {"model": None}},
        "segments": {"max_s": 60, "undo_budget_mb": 2048},
        "behaviour": {"preanalyze": False},
        "perf": {"sec_per_min": {}},
        "ui": {"width": None, "text_scale": 100, "chat_collapsed": False, "always_on_top": True},
    }


# 판 n → n+1로 올리는 함수. 지금은 1판뿐이라 비어 있다 (0판 = 판 번호가 없던 시험용 파일).
def _from_0(data: Dict[str, Any]) -> Dict[str, Any]:
    data = dict(data)
    data["schema_version"] = 1
    return data


MIGRATIONS: Dict[int, Callable[[Dict[str, Any]], Dict[str, Any]]] = {0: _from_0}

TEXT_SCALES = (100, 115, 130)


def _merge_defaults(data: Dict[str, Any], defaults: Dict[str, Any]) -> Dict[str, Any]:
    """없는 키는 기본값으로 채운다 (있는 값은 그대로)."""
    out = dict(data)
    for key, value in defaults.items():
        if key not in out:
            out[key] = copy.deepcopy(value)
        elif isinstance(value, dict) and isinstance(out[key], dict) and key != "params":
            out[key] = _merge_defaults(out[key], value)
    return out


class SettingsError(Exception):
    pass


def _validate(data: Dict[str, Any]) -> None:
    slots = data.get("automations", {}).get("slots")
    if not isinstance(slots, list) or not slots:
        raise SettingsError("slots")
    numbers = set()
    for s in slots:
        if not isinstance(s, dict) or not isinstance(s.get("slot"), int) or not isinstance(s.get("params"), dict):
            raise SettingsError("slot")
        if s["slot"] in numbers:
            raise SettingsError("slot_number")
        numbers.add(s["slot"])
    ui = data.get("ui")
    if not isinstance(ui, dict):
        raise SettingsError("ui")


class Settings:
    """설정 파일 하나. 바꾸는 함수는 바로 저장한다."""

    def __init__(self, path: Optional[Path] = None, clock: Callable[[], float] = time.time) -> None:
        if path is None:
            from .resolve_link.paths import state_dir

            path = state_dir() / FILE_NAME
        self.path = Path(path)
        self._clock = clock
        self.data: Dict[str, Any] = default_settings()
        self.moved_bad: Optional[Path] = None
        self.load()

    # --- 읽기·쓰기 ---

    def _move_bad(self) -> None:
        stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(self._clock()))
        target = self.path.with_name(f"{self.path.name}.bad-{stamp}")
        n = 2
        while target.exists():
            target = self.path.with_name(f"{self.path.name}.bad-{stamp}-{n}")
            n += 1
        try:
            os.replace(self.path, target)
            self.moved_bad = target
        except OSError:
            self.moved_bad = None

    def load(self) -> None:
        self.data = default_settings()
        try:
            raw = self.path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return
        except OSError:
            return
        try:
            data = json.loads(raw)
            if not isinstance(data, dict):
                raise SettingsError("not_object")
            version = data.get("schema_version", 0)
            if not isinstance(version, int) or version > SCHEMA_VERSION or version < 0:
                raise SettingsError("version")  # 더 새 앱이 쓴 파일: 건드리지 않고 옮겨 둔다
            while version < SCHEMA_VERSION:
                data = MIGRATIONS[version](data)
                version = data["schema_version"]
            data = _merge_defaults(data, default_settings())
            _validate(data)
        except (ValueError, SettingsError, KeyError, TypeError):
            self._move_bad()
            self.data = default_settings()
            return
        self.data = data

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(self.path.name + ".tmp")
        tmp.write_text(json.dumps(self.data, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, self.path)

    # --- 자동화 버튼 ---

    @property
    def slots(self) -> List[Dict[str, Any]]:
        """화면에 보이는 순서대로."""
        return self.data["automations"]["slots"]

    def slot(self, number: int) -> Dict[str, Any]:
        for s in self.slots:
            if s["slot"] == number:
                return s
        raise KeyError(number)

    def move_slot(self, number: int, new_index: int) -> None:
        slots = self.slots
        s = self.slot(number)
        slots.remove(s)
        new_index = max(0, min(int(new_index), len(slots)))
        slots.insert(new_index, s)
        self.save()

    def update_slot(self, number: int, params: Optional[Dict[str, Any]] = None, **fields: Any) -> Dict[str, Any]:
        """버튼 설정을 바꾼다. 바꾸기 전 설정은 previous에 한 단계만 남긴다."""
        s = self.slot(number)
        before = {k: copy.deepcopy(v) for k, v in s.items() if k not in ("previous", "slot")}
        changed = False
        if params:
            new_params = dict(s["params"])
            new_params.update(params)
            if new_params != s["params"]:
                s["params"] = new_params
                changed = True
        for key, value in fields.items():
            if key in ("slot", "previous"):
                continue
            if s.get(key) != value:
                s[key] = value
                changed = True
        if changed:
            s["previous"] = before
            self.save()
        return s

    def save_slot(self, number: int, kind: str, name: str, params: Dict[str, Any]) -> Dict[str, Any]:
        """채팅에서 "이대로 자동화 버튼에 저장": 구간 값은 저장하지 않는다."""
        clean = {k: v for k, v in params.items() if k not in RANGE_KEYS}
        s = self.slot(number)
        before = {k: copy.deepcopy(v) for k, v in s.items() if k not in ("previous", "slot")}
        s.update({"kind": kind, "name": name, "params": clean})
        s["previous"] = before
        self.save()
        return s

    def restore_previous(self, number: int) -> bool:
        """[이전 설정으로 되돌리기]. 되돌린 뒤에는 previous가 비어 있다 (한 단계만)."""
        s = self.slot(number)
        prev = s.get("previous")
        if not isinstance(prev, dict):
            return False
        for key, value in prev.items():
            s[key] = copy.deepcopy(value)
        s["previous"] = None
        self.save()
        return True

    # --- 화면 ---

    @property
    def ui(self) -> Dict[str, Any]:
        return self.data["ui"]

    def set_ui(self, key: str, value: Any) -> None:
        if key == "text_scale" and value not in TEXT_SCALES:
            raise ValueError(value)
        self.ui[key] = value
        self.save()
