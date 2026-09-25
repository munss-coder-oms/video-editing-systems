"""우체통 폴더, 리졸브 스크립트 폴더, Fusion.prefs 위치.

리졸브 안의 LuaJIT는 윈도우에서 파일 이름을 ANSI 코드 페이지로 연다.
사용자 이름이 한글이면 %LOCALAPPDATA% 경로를 Lua가 못 열 수 있으므로,
우체통 경로는 반드시 영문(ASCII)으로 고른다 (짧은 이름 → ProgramData 순서).
"""

from __future__ import annotations

import getpass
import hashlib
import os
import sys
import tempfile
from pathlib import Path
from typing import List, Optional, Tuple

from . import SCRIPT_FILENAME

APP_DIR_NAME = "video-editing-systems"
BRIDGE_PATH_FILE = "bridge_path.txt"

# Fusion.prefs를 찾을 때 폴더를 몇 단계까지, 몇 개까지 볼지 (너무 오래 걸리지 않게)
_PREFS_MAX_DEPTH = 8
_PREFS_MAX_DIRS = 20000


def _is_windows() -> bool:
    return sys.platform == "win32"


def _is_ascii(text: str) -> bool:
    try:
        text.encode("ascii")
        return True
    except UnicodeEncodeError:
        return False


def state_dir() -> Path:
    """앱 기록 폴더 (%LOCALAPPDATA%\\video-editing-systems). app 로그와 같은 곳."""
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / ".local" / "share")
    path = Path(base) / APP_DIR_NAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def _short_path(path: Path) -> Optional[str]:
    """윈도우 짧은 이름(8.3). 윈도우가 아니거나 실패하면 None."""
    if not _is_windows():
        return None
    try:
        import ctypes
        from ctypes import wintypes

        func = ctypes.windll.kernel32.GetShortPathNameW
        func.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD]
        func.restype = wintypes.DWORD
        size = func(str(path), None, 0)
        if not size:
            return None
        buf = ctypes.create_unicode_buffer(size)
        if not func(str(path), buf, size):
            return None
        return buf.value or None
    except Exception:
        return None


def _long_path(path: Path) -> Optional[str]:
    """윈도우 긴 이름 (C:\\Users\\ABCDEF~1\\... → C:\\Users\\홍길동\\...). 윈도우가 아니거나 실패하면 None."""
    if not _is_windows():
        return None
    try:
        import ctypes
        from ctypes import wintypes

        func = ctypes.windll.kernel32.GetLongPathNameW
        func.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD]
        func.restype = wintypes.DWORD
        size = func(str(path), None, 0)
        if not size:
            return None
        buf = ctypes.create_unicode_buffer(size)
        if not func(str(path), buf, size):
            return None
        return buf.value or None
    except Exception:
        return None


def long_path(path: Path) -> Optional[str]:
    """우체통의 긴 경로. 짧은 이름(8.3) 우체통으로 가져온 파일을 리졸브가 긴 이름으로 알려 줄 때 비교용.

    긴 이름을 알 수 없거나 경로와 같으면 None.
    """
    got = _long_path(path)
    if not got or got == str(path):
        return None
    return got


def _program_data() -> Path:
    base = os.environ.get("PROGRAMDATA") or os.environ.get("ALLUSERSPROFILE")
    if base:
        return Path(base)
    if _is_windows():
        return Path(r"C:\ProgramData")
    return Path(tempfile.gettempdir())


def _user_tag() -> str:
    """사용자별 폴더 이름에 붙일 영문 8자 (사용자 이름의 sha1 앞 8자리)."""
    name = os.environ.get("USERNAME") or ""
    if not name:
        try:
            name = getpass.getuser()
        except Exception:
            name = "user"
    return hashlib.sha1(name.encode("utf-8")).hexdigest()[:8]


def _public_dir() -> Path:
    base = os.environ.get("PUBLIC")
    if base:
        return Path(base)
    if _is_windows():
        return Path(r"C:\Users\Public")
    return Path(tempfile.gettempdir())


def _choose_mailbox() -> Path:
    candidate = state_dir() / "bridge"
    made = True
    try:
        candidate.mkdir(parents=True, exist_ok=True)
    except OSError:
        made = False
    if made and _is_ascii(str(candidate)):
        return candidate
    # 한글 사용자 이름: 윈도우 짧은 이름(C:\Users\ABCDEF~1\...)이 있으면 그것을 쓴다.
    short = _short_path(candidate) if made else None
    if short and _is_ascii(short):
        return Path(short)
    # 짧은 이름이 꺼진 디스크도 있다. 그때는 모든 사용자 공용 폴더에 사용자별 폴더를 만든다.
    # 회사 PC처럼 ProgramData에 쓸 수 없으면 공용 사용자 폴더(%PUBLIC%)에.
    tag = f"bridge-{_user_tag()}"
    for fallback in (_program_data() / APP_DIR_NAME / tag, _public_dir() / APP_DIR_NAME / tag):
        if not _is_ascii(str(fallback)):
            continue
        try:
            fallback.mkdir(parents=True, exist_ok=True)
        except OSError:
            continue
        return fallback
    # 영문 폴더를 하나도 만들 수 없으면 한글 경로라도 쓴다. 창은 뜨고, 결과 파일에 이유가 남는다
    # (리졸브 안의 Lua가 이 경로를 못 열 수 있다).
    return candidate


def _read_saved_mailbox() -> Optional[Path]:
    try:
        text = (state_dir() / BRIDGE_PATH_FILE).read_text(encoding="utf-8").strip()
    except (OSError, UnicodeDecodeError):
        return None
    if not text or not _is_ascii(text):
        return None
    path = Path(text)
    return path if path.is_dir() else None


def mailbox_dir() -> Path:
    """앱과 Lua가 주고받는 우체통 폴더 (영문 경로).

    한 번 고른 경로는 bridge_path.txt에 적어 두어 앱과 설치 프로그램이 같은 곳을 쓴다.
    기록이 없거나 그 폴더가 사라졌으면 다시 고른다. 환경 변수 AIH_MAILBOX_DIR이 있으면 그것을 쓴다 (테스트용).
    """
    env = os.environ.get("AIH_MAILBOX_DIR")
    if env:
        path = Path(env)
        path.mkdir(parents=True, exist_ok=True)
        return path
    saved = _read_saved_mailbox()
    if saved is not None:
        return saved
    chosen = _choose_mailbox()
    try:
        (state_dir() / BRIDGE_PATH_FILE).write_text(str(chosen), encoding="utf-8")
    except OSError:
        pass  # 기록을 못 남겨도 같은 규칙으로 다시 고르면 같은 경로가 나온다
    return chosen


def files_dir() -> Path:
    """Lua가 리졸브로 가져올 수 있는 유일한 폴더 (우체통\\files)."""
    path = mailbox_dir() / "files"
    path.mkdir(parents=True, exist_ok=True)
    return path


def resolve_utility_dirs() -> List[Path]:
    """리졸브 Scripts → Utility 메뉴 폴더. 첫 번째가 설치할 곳, 나머지는 점검용."""
    dirs: List[Path] = []
    appdata = os.environ.get("APPDATA")
    if appdata:
        dirs.append(Path(appdata) / "Blackmagic Design" / "DaVinci Resolve" / "Support"
                    / "Fusion" / "Scripts" / "Utility")
    elif sys.platform == "darwin":
        dirs.append(Path.home() / "Library" / "Application Support" / "Blackmagic Design"
                    / "DaVinci Resolve" / "Fusion" / "Scripts" / "Utility")
    elif not _is_windows():
        dirs.append(Path.home() / ".local" / "share" / "DaVinciResolve" / "Fusion" / "Scripts" / "Utility")
    else:
        dirs.append(Path.home() / "AppData" / "Roaming" / "Blackmagic Design" / "DaVinci Resolve"
                    / "Support" / "Fusion" / "Scripts" / "Utility")
    if _is_windows() or os.environ.get("PROGRAMDATA"):
        dirs.append(_program_data() / "Blackmagic Design" / "DaVinci Resolve" / "Fusion" / "Scripts" / "Utility")
    return dirs


def installed_scripts() -> List[Path]:
    """Utility 폴더들 가운데 AI_Helper_Connect.lua가 실제로 있는 곳."""
    return [d / SCRIPT_FILENAME for d in resolve_utility_dirs() if (d / SCRIPT_FILENAME).is_file()]


def resolve_installed() -> bool:
    """다빈치 리졸브가 설치된 것 같은지 (안내 문구용 추측)."""
    candidates: List[Path] = []
    for var in ("ProgramFiles", "ProgramW6432"):
        base = os.environ.get(var)
        if base:
            candidates.append(Path(base) / "Blackmagic Design" / "DaVinci Resolve" / "Resolve.exe")
    if _is_windows() or os.environ.get("PROGRAMDATA"):
        candidates.append(_program_data() / "Blackmagic Design" / "DaVinci Resolve")
    if not _is_windows():
        candidates += [Path("/opt/resolve"), Path("/Applications/DaVinci Resolve")]
    return any(p.exists() for p in candidates)


def _file_version(path: Path) -> Optional[str]:
    """윈도우 실행 파일의 판 (예: 21.1.0.5). 윈도우가 아니거나 읽지 못하면 None."""
    if not _is_windows():
        return None
    try:
        import ctypes
        from ctypes import wintypes

        ver = ctypes.windll.version
        ver.GetFileVersionInfoSizeW.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(wintypes.DWORD)]
        ver.GetFileVersionInfoSizeW.restype = wintypes.DWORD
        ver.GetFileVersionInfoW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p]
        ver.GetFileVersionInfoW.restype = wintypes.BOOL
        ver.VerQueryValueW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_void_p),
                                       ctypes.POINTER(wintypes.UINT)]
        ver.VerQueryValueW.restype = wintypes.BOOL
        size = ver.GetFileVersionInfoSizeW(str(path), None)
        if not size:
            return None
        buf = ctypes.create_string_buffer(size)
        if not ver.GetFileVersionInfoW(str(path), 0, size, buf):
            return None
        ptr = ctypes.c_void_p()
        length = wintypes.UINT()
        if not ver.VerQueryValueW(buf, "\\", ctypes.byref(ptr), ctypes.byref(length)) or length.value < 52:
            return None
        # VS_FIXEDFILEINFO: 서명, 구조 판, 파일 판(위 32비트, 아래 32비트) ...
        fields = (wintypes.DWORD * 4).from_address(ptr.value)
        if fields[0] != 0xFEEF04BD:
            return None
        ms, ls = fields[2], fields[3]
        return f"{ms >> 16}.{ms & 0xFFFF}.{ls >> 16}.{ls & 0xFFFF}"
    except Exception:
        return None


def resolve_exe_versions() -> List[Tuple[str, Optional[str]]]:
    """설치된 Resolve.exe와 그 판 (결과 파일용. 연결 전에도 어떤 판이 깔렸는지 알 수 있게)."""
    found: List[Tuple[str, Optional[str]]] = []
    seen = set()
    for var in ("ProgramFiles", "ProgramW6432"):
        base = os.environ.get(var)
        if not base:
            continue
        exe = Path(base) / "Blackmagic Design" / "DaVinci Resolve" / "Resolve.exe"
        key = os.path.normcase(str(exe))
        if key in seen or not exe.is_file():
            continue
        seen.add(key)
        found.append((str(exe), _file_version(exe)))
    return found


def _prefs_roots() -> List[Path]:
    roots: List[Path] = []
    for var in ("APPDATA", "PROGRAMDATA", "LOCALAPPDATA"):
        base = os.environ.get(var)
        if base:
            roots.append(Path(base) / "Blackmagic Design")
    if _is_windows() and not os.environ.get("PROGRAMDATA"):
        roots.append(_program_data() / "Blackmagic Design")
    if not _is_windows():
        roots += [
            Path.home() / ".local" / "share" / "DaVinciResolve",
            Path.home() / "Library" / "Application Support" / "Blackmagic Design",
        ]
    return roots


def find_fusion_prefs() -> List[Path]:
    """리졸브의 Fusion.prefs 후보를 모두 찾는다 (최근에 바뀐 것부터).

    윈도우에서 정확한 위치가 확인되지 않아 한 경로로 정하지 않고 찾아본다.
    환경 변수 AIH_PREFS_FILE이 있으면 그 파일 하나만 쓴다 (테스트용).
    """
    env = os.environ.get("AIH_PREFS_FILE")
    if env:
        return [Path(env)]
    found: List[Path] = []
    seen = set()
    visited = 0
    for root in _prefs_roots():
        if not root.is_dir():
            continue
        root_depth = len(root.parts)
        for dirpath, dirnames, filenames in os.walk(root, onerror=lambda _e: None):
            visited += 1
            if visited > _PREFS_MAX_DIRS:
                break
            if len(Path(dirpath).parts) - root_depth >= _PREFS_MAX_DEPTH:
                dirnames[:] = []
            if "Fusion.prefs" in filenames:
                path = Path(dirpath) / "Fusion.prefs"
                key = os.path.normcase(str(path))
                if key not in seen:
                    seen.add(key)
                    found.append(path)

    def mtime(p: Path) -> float:
        try:
            return p.stat().st_mtime
        except OSError:
            return 0.0

    found.sort(key=mtime, reverse=True)
    return found
