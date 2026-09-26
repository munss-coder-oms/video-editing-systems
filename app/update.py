"""새 판 받기 (⋯ > 새 판 받기): update_windows.bat을 설치 폴더 밖에 복사해 새 검은 창에서 띄운다.

화면(Qt)을 모르는 부분만 여기 둔다. 창(app/companion/window.py)은 확인을 받은 뒤 start_update()를 부르고
스스로 닫힌다. update_windows.bat은 이 앱이 끝나기를 기다렸다가(PID) 새 판을 받아 같은 폴더에 넣고
setup_windows.bat을 빠른 모드(AIH_UPDATE=1, 자동 테스트 건너뜀)로 돌린 뒤 앱을 다시 연다.

- cmd.exe는 돌고 있는 .bat 파일을 바이트 위치로 한 줄씩 읽으므로, 새 판이 덮어쓸 설치 폴더의
  update_windows.bat을 바로 띄우지 않고 늘 임시 폴더(%LOCALAPPDATA%\\video-editing-systems\\update)의 복사본을 띄운다.
- 앱은 conda 환경을 켠 채로 돈다. 검은 창은 바탕화면에서 두 번 누른 것처럼 켜기 전 환경에서 돌게 한다.
- 켤 때마다 지난번에 돈 판 번호를 적어 두고, 바뀌었으면 대화 칸에 한 번 알린다 (remember_version).
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path, PureWindowsPath
from typing import Callable, Dict, Mapping, Optional

UPDATE_BAT = "update_windows.bat"
SOURCE_FILE = "update_source.txt"  # 받을 가지 이름 한 줄 (update_windows.bat이 읽는다)
LAST_VERSION_FILE = "last_version.txt"

# 윈도우 CreateProcess 속성 (subprocess에는 윈도우에서만 있으므로 숫자로 둔다)
CREATE_NEW_CONSOLE = 0x00000010
CREATE_NEW_PROCESS_GROUP = 0x00000200
CREATE_BREAKAWAY_FROM_JOB = 0x01000000

# conda 환경을 켤 때 생기는 값과 이 앱의 설치 스크립트용 값 (검은 창에는 넘기지 않는다)
DROP_ENV = ("CONDA_PREFIX", "CONDA_DEFAULT_ENV", "CONDA_SHLVL", "CONDA_PROMPT_MODIFIER", "NOPAUSE", "AIH_UPDATE")


class UpdateError(Exception):
    """새 판 받기를 띄우지 못함. reason: missing(설치 폴더에 update_windows.bat 없음) / copy / launch."""

    def __init__(self, reason: str, detail: str = "") -> None:
        super().__init__(f"{reason}: {detail}" if detail else reason)
        self.reason = reason
        self.detail = detail


def supported(platform: Optional[str] = None) -> bool:
    """윈도우에서만 띄운다 (update_windows.bat, robocopy, PowerShell)."""
    return (platform or sys.platform) == "win32"


def install_dir() -> Path:
    """run_app.bat이 있는 저장소 폴더. 지금 폴더(cwd)가 아니라 이 패키지가 있는 곳에서 찾는다."""
    return Path(__file__).resolve().parent.parent


def update_dir(state_root: Path) -> Path:
    """검은 창이 쓰는 임시 폴더 (update_windows.bat의 WORK와 같은 곳)."""
    return Path(state_root) / "update"


def prepare(install: Path, work: Path) -> Path:
    """설치 폴더의 update_windows.bat을 임시 폴더로 복사하고 그 복사본의 경로를 돌려준다."""
    source = Path(install) / UPDATE_BAT
    if not source.is_file():
        raise UpdateError("missing", str(source))
    target = Path(work) / UPDATE_BAT
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
    except OSError as exc:
        raise UpdateError("copy", f"{type(exc).__name__}: {exc}") from exc
    return target


def command_line(bat: Path, pid: int, install: Path, comspec: str = "cmd.exe") -> str:
    """새 검은 창에서 돌릴 명령 줄.

    cmd /s /c는 맨 앞과 맨 끝의 따옴표 한 쌍만 벗기므로 전체를 한 번 더 감싼다. 그러면 안쪽 경로에
    빈칸, 괄호, 작은따옴표, 한글이 있어도 따옴표 그대로 배치 파일에 간다. /d는 명령 창 자동 실행
    (conda init이 넣는 것)을 건너뛴다.
    """
    for value in (bat, install, comspec):
        if '"' in str(value):
            raise UpdateError("launch", f"quote in path: {value}")
    return f'"{comspec}" /d /s /c ""{bat}" {int(pid)} "{install}""'


def _same_or_inside(path: str, prefix: str) -> bool:
    p = str(PureWindowsPath(path)).rstrip("\\").lower()
    q = str(PureWindowsPath(prefix)).rstrip("\\").lower()
    return bool(q) and (p == q or p.startswith(q + "\\"))


def child_env(environ: Mapping[str, str], sep: str = ";") -> Dict[str, str]:
    """검은 창에 줄 환경: 앱이 켠 conda 환경을 걷어 바탕화면에서 두 번 누른 것과 같게 한다.

    PATH에서 켠 환경(CONDA_PREFIX) 안의 폴더를 빼고, 켤 때 생긴 값과 NOPAUSE 등을 지운다.
    AIH_UPDATE_ZIP처럼 시험에 쓰는 값은 그대로 둔다. AIH_UPDATE_COPY=1은 띄우는 것이 임시 폴더의
    복사본이라는 표시다 (경로 글자가 조금 달라도 배치 파일이 자기를 또 복사하지 않게).
    """
    env = dict(environ)
    prefixes = []  # 켠 환경들 (conda 안에서 다른 환경을 또 켰으면 CONDA_PREFIX_1 ...)
    for key in list(env):
        upper = key.upper()
        if upper == "CONDA_PREFIX" or upper.startswith("CONDA_PREFIX_"):
            if env[key]:
                prefixes.append(env[key])
        if upper in DROP_ENV or upper.startswith("CONDA_PREFIX_"):
            del env[key]
    for key in [k for k in env if k.upper() == "PATH"]:
        parts = [p for p in env[key].split(sep) if p and not any(_same_or_inside(p, pre) for pre in prefixes)]
        env[key] = sep.join(parts)
    env["AIH_UPDATE_COPY"] = "1"
    return env


def start_update(*, pid: int, install: Path, work: Path,
                 popen: Callable[..., object] = subprocess.Popen,
                 environ: Optional[Mapping[str, str]] = None) -> Path:
    """update_windows.bat을 work로 복사해 새 검은 창에서 띄운다 (PID와 설치 폴더를 넘긴다).

    앱이 끝나도 검은 창은 계속 돌아야 하므로 새 콘솔·새 프로세스 묶음으로 띄우고, 가능하면 작업 묶음(job)
    밖으로 뺀다 (빼지 못하게 막힌 PC에서는 그냥 띄운다). 띄운 복사본의 경로를 돌려준다.
    """
    install = Path(install)
    bat = prepare(install, work)
    source = os.environ if environ is None else environ
    comspec = source.get("ComSpec") or source.get("COMSPEC") or "cmd.exe"
    cmd = command_line(bat, pid, install, comspec)
    env = child_env(source)
    base = CREATE_NEW_CONSOLE | CREATE_NEW_PROCESS_GROUP
    last: Optional[OSError] = None
    for flags in (base | CREATE_BREAKAWAY_FROM_JOB, base):
        try:
            popen(cmd, cwd=str(bat.parent), env=env, creationflags=flags, close_fds=True)
            return bat
        except OSError as exc:
            last = exc
    raise UpdateError("launch", f"{type(last).__name__}: {last}")


def remember_version(state_root: Path, version: str) -> Optional[str]:
    """지난번에 돈 판과 다르면 새 판 번호를 돌려주고 적어 둔다. 그대로면 None.

    적힌 것이 없으면(처음 켬, 또는 이 기록이 없던 예전 판에서 옴) 적기만 한다. 적지 못하면 다음에 또
    알리지 않도록 None (한 번만 알린다).
    """
    path = Path(state_root) / LAST_VERSION_FILE
    try:
        before: Optional[str] = path.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        before = None
    except (OSError, UnicodeDecodeError):
        before = ""
    if before == version:
        return None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(version + "\n", encoding="utf-8")
    except OSError:
        return None
    return version if before is not None else None
