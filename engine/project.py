"""프로젝트 파일 (project.json).

형식이 바뀌어도 예전 프로젝트가 열리도록 schema_version을 넣고,
버전이 낮으면 MIGRATIONS의 변환 함수를 차례로 적용한다 (PRD 7.4 원칙 3).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List

from . import __version__
from .commands import Command, commands_from_list

PROJECT_SCHEMA_VERSION = 1
PROJECT_FILENAME = "project.json"

# {이전 버전: 그 버전의 dict를 다음 버전 dict로 바꾸는 함수}
MIGRATIONS: Dict[int, Callable[[dict], dict]] = {}


class ProjectError(ValueError):
    pass


@dataclass
class Project:
    source: str
    commands: List[Command] = field(default_factory=list)
    media: dict = field(default_factory=dict)
    settings: dict = field(default_factory=dict)  # 쓴 오디오 트랙 등 사용자가 고른 값
    outputs: dict = field(default_factory=dict)
    reports: dict = field(default_factory=dict)
    app_version: str = __version__

    def to_dict(self) -> dict:
        return {
            "schema_version": PROJECT_SCHEMA_VERSION,
            "app_version": self.app_version,
            "source": self.source,
            "media": self.media,
            "commands": [c.to_dict() for c in self.commands],
            "settings": self.settings,
            "outputs": self.outputs,
            "reports": self.reports,
        }

    def save(self, folder: str | Path) -> Path:
        path = Path(folder) / PROJECT_FILENAME
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(self.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)
        return path

    @classmethod
    def from_dict(cls, data: dict) -> "Project":
        data = migrate(data)
        return cls(
            source=data["source"],
            commands=commands_from_list(data.get("commands", [])),
            media=data.get("media", {}),
            settings=data.get("settings", {}),
            outputs=data.get("outputs", {}),
            reports=data.get("reports", {}),
            app_version=data.get("app_version", __version__),
        )

    @classmethod
    def load(cls, folder_or_file: str | Path) -> "Project":
        path = Path(folder_or_file)
        if path.is_dir():
            path = path / PROJECT_FILENAME
        return cls.from_dict(json.loads(path.read_text(encoding="utf-8")))


def migrate(data: dict) -> dict:
    version = data.get("schema_version")
    if not isinstance(version, int):
        raise ProjectError("프로젝트 파일에 schema_version이 없습니다.")
    if version > PROJECT_SCHEMA_VERSION:
        raise ProjectError(
            f"더 새로운 앱에서 만든 프로젝트입니다 (형식 v{version}). 앱을 업데이트하세요."
        )
    while version < PROJECT_SCHEMA_VERSION:
        step = MIGRATIONS.get(version)
        if step is None:
            raise ProjectError(f"형식 v{version}을 변환하는 방법이 없습니다.")
        data = step(data)
        version += 1
        data["schema_version"] = version
    return data
