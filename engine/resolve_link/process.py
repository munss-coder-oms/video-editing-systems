"""리졸브(Resolve.exe)가 켜져 있는지: 리졸브에 요청을 보내지 않고 윈도우 작업 목록으로만 본다.

한가할 때 리졸브에 요청을 보내면 Lua가 Fusion.prefs를 다시 쓰는데, 리졸브를 끄는 중에 설정을 쓰다
리졸브가 꺼진 일이 있다(F17). 그래서 연결된 뒤에는 이 방법으로만 "리졸브가 꺼졌는지" 본다.
psutil을 새로 넣지 않으려고 tasklist를 쓴다 (창이 번쩍 뜨지 않게 CREATE_NO_WINDOW).
"""

from __future__ import annotations

import csv
import io
import subprocess
import sys
from typing import Callable, Optional

PROCESS_NAME = "Resolve.exe"
_TIMEOUT = 5.0


def _tasklist_output(name: str) -> Optional[str]:
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        done = subprocess.run(
            ["tasklist", "/FI", f"IMAGENAME eq {name}", "/NH", "/FO", "CSV"],
            capture_output=True,
            timeout=_TIMEOUT,
            creationflags=flags,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if done.returncode != 0:
        return None
    raw = done.stdout or b""
    for enc in ("utf-8", "mbcs", "cp949", "latin-1"):
        try:
            return raw.decode(enc)
        except (LookupError, UnicodeDecodeError):
            continue
    return raw.decode("latin-1", "replace")


def parse_tasklist(text: str, name: str = PROCESS_NAME) -> bool:
    """tasklist /FO CSV /NH 출력에 name 프로세스가 있는지. 없으면 안내 한 줄(따옴표 없음)만 나온다."""
    want = name.lower()
    for row in csv.reader(io.StringIO(text)):
        if row and row[0].strip().lower() == want:
            return True
    return False


def resolve_running(
    name: str = PROCESS_NAME,
    runner: Callable[[str], Optional[str]] = _tasklist_output,
    platform: str = sys.platform,
) -> Optional[bool]:
    """켜져 있으면 True, 없으면 False, 알 수 없으면(윈도우가 아님, tasklist 실패) None."""
    if platform != "win32":
        return None
    text = runner(name)
    if text is None:
        return None
    return parse_tasklist(text, name)
