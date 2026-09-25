"""리졸브 Scripts 메뉴에 AI_Helper_Connect.lua를 넣는다.

    python -m engine.resolve_link.install

틀(resolve_scripts/AI_Helper_Connect.lua)의 @@MAILBOX_HEX@@(우체통 경로),
@@MAILBOX_LONG_HEX@@(같은 우체통의 긴 경로, 비교용)과 @@SCRIPT_VERSION@@을 채워
%APPDATA%의 Utility 폴더에 쓴다. 내용이 같으면 건드리지 않는다.
리졸브가 아직 없어도 파일은 미리 넣어 둔다 (나중에 설치하면 바로 메뉴에 보인다).
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

from . import SCRIPT_FILENAME, SCRIPT_NAME, SCRIPT_VERSION
from .paths import long_path, mailbox_dir, resolve_installed, resolve_utility_dirs

MAILBOX_PLACEHOLDER = "@@MAILBOX_HEX@@"
VERSION_PLACEHOLDER = "@@SCRIPT_VERSION@@"
# 없어도 되는 자리 (예전 틀에는 없다)
MAILBOX_LONG_PLACEHOLDER = "@@MAILBOX_LONG_HEX@@"

_UTF8_BOM = b"\xef\xbb\xbf"


def default_template() -> Path:
    return Path(__file__).resolve().parent.parent.parent / "resolve_scripts" / SCRIPT_FILENAME


@dataclass
class InstallResult:
    paths_written: List[Path]  # 이번에 새로 쓰거나 바꾼 파일
    mailbox: Path
    version: str
    message: str
    unchanged: List[Path] = field(default_factory=list)  # 이미 같은 내용이라 그대로 둔 파일

    @property
    def paths(self) -> List[Path]:
        """설치되어 있는 스크립트 파일 전체."""
        return self.paths_written + self.unchanged


def mailbox_hex(mailbox: Path) -> str:
    """Lua에 넣을 우체통 경로. LuaJIT는 윈도우에서 ANSI 코드 페이지로 파일을 열므로 그 인코딩으로 바꾼다."""
    text = str(mailbox)
    encoding = "mbcs" if sys.platform == "win32" else "utf-8"
    return text.encode(encoding, "strict").hex()


def mailbox_long_hex(mailbox: Path) -> str:
    """같은 우체통의 긴 경로 (UTF-8의 16진수). 짧은 이름(8.3) 우체통이 아니면 빈 글자.

    Lua는 이 경로로 파일을 열지 않는다. 리졸브가 알려 주는 파일 경로(UTF-8)가
    긴 이름일 때 "이 창이 넣은 파일"인지 맞춰 보는 데만 쓴다.
    """
    long = long_path(Path(mailbox))
    return long.encode("utf-8").hex() if long else ""


def render_script(template_text: str, mailbox: Path, version: str = SCRIPT_VERSION) -> str:
    if MAILBOX_PLACEHOLDER not in template_text or VERSION_PLACEHOLDER not in template_text:
        raise ValueError("Lua 스크립트 틀에 @@MAILBOX_HEX@@ 또는 @@SCRIPT_VERSION@@ 자리가 없습니다.")
    return (template_text
            .replace(MAILBOX_LONG_PLACEHOLDER, mailbox_long_hex(mailbox))
            .replace(MAILBOX_PLACEHOLDER, mailbox_hex(mailbox))
            .replace(VERSION_PLACEHOLDER, version))


def _replace_with_retry(tmp: Path, target: Path) -> None:
    # 리졸브가 스크립트를 읽는 순간에는 윈도우가 바꾸기를 잠깐 막을 수 있다.
    deadline = time.monotonic() + 1.0
    while True:
        try:
            os.replace(tmp, target)
            return
        except PermissionError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(0.05)


def install_script(
    template: Optional[Path] = None,
    target_dir: Optional[Path] = None,
    mailbox: Optional[Path] = None,
) -> InstallResult:
    """스크립트를 설치한다. 같은 내용이 이미 있으면 그대로 둔다 (여러 번 불러도 됨)."""
    template = Path(template) if template is not None else default_template()
    target_dir = Path(target_dir) if target_dir is not None else resolve_utility_dirs()[0]
    mailbox = Path(mailbox) if mailbox is not None else mailbox_dir()
    had_resolve = resolve_installed()

    raw = template.read_bytes()
    if raw.startswith(_UTF8_BOM):
        raw = raw[len(_UTF8_BOM):]  # BOM이 있으면 LuaJIT가 문법 오류를 낸다
    data = render_script(raw.decode("utf-8"), mailbox).encode("utf-8")

    # Lua가 가져올 파일을 두는 곳도 미리 만든다.
    (mailbox / "files").mkdir(parents=True, exist_ok=True)
    target = target_dir / SCRIPT_FILENAME
    try:
        same = target.read_bytes() == data
    except OSError:
        same = False

    written: List[Path] = []
    unchanged: List[Path] = []
    if same:
        unchanged.append(target)
    else:
        target_dir.mkdir(parents=True, exist_ok=True)
        tmp = target.with_name(target.name + ".tmp")
        tmp.write_bytes(data)
        try:
            _replace_with_retry(tmp, target)
        finally:
            if tmp.exists():
                try:
                    tmp.unlink()
                except OSError:
                    pass
        written.append(target)

    if same:
        message = f"리졸브 연결 스크립트가 이미 설치되어 있습니다: {target}"
    else:
        message = f"리졸브 연결 스크립트를 설치했습니다: {target}"
    if had_resolve:
        message += f" (리졸브에서 Workspace → Scripts → {SCRIPT_NAME})"
    else:
        message += " (다빈치 리졸브가 아직 없는 것 같습니다. 파일은 미리 넣어 두었으니 리졸브를 설치하면 바로 쓸 수 있습니다.)"
    return InstallResult(written, mailbox, SCRIPT_VERSION, message, unchanged)


def main(argv=None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass
    parser = argparse.ArgumentParser(
        prog="python -m engine.resolve_link.install",
        description="리졸브 Scripts 메뉴에 AI_Helper_Connect 스크립트를 넣습니다.",
    )
    parser.parse_args(argv)
    try:
        result = install_script()
    except (OSError, ValueError, UnicodeError) as exc:
        print(f"리졸브 연결 스크립트를 설치하지 못했습니다: {exc}")
        return 1
    print(result.message)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
