"""FFmpeg 호출을 한곳에 모은 모듈 (PRD 7.4 원칙 4: 교체 가능한 외부 의존).

다른 모듈은 FFmpeg 실행 파일 위치나 호출 방식을 몰라도 된다.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Callable, Optional, Sequence

# 윈도우에서 FFmpeg 콘솔 창이 깜빡이지 않게 한다.
_CREATE_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0

ProgressCallback = Callable[[float], None]
"""0.0~1.0 사이 진행률을 받는 함수."""


class FFmpegError(RuntimeError):
    """FFmpeg 실행 실패."""


class Cancelled(RuntimeError):
    """사용자가 작업을 취소함."""


def _project_root() -> Path:
    return Path(__file__).resolve().parent.parent


def find_tool(name: str) -> str:
    """ffmpeg / ffprobe 실행 파일 경로를 찾는다.

    찾는 순서:
    1. 환경 변수 VES_FFMPEG_DIR 폴더
    2. 저장소 안 tools/ffmpeg/bin 폴더 (직접 받아 넣은 경우)
    3. PATH (Miniconda 환경에 설치한 ffmpeg 포함)
    """
    exe = name + (".exe" if sys.platform == "win32" else "")
    candidates = []
    env_dir = os.environ.get("VES_FFMPEG_DIR")
    if env_dir:
        candidates.append(Path(env_dir) / exe)
    candidates.append(_project_root() / "tools" / "ffmpeg" / "bin" / exe)
    for path in candidates:
        if path.is_file():
            return str(path)
    found = shutil.which(name)
    if found:
        return found
    raise FFmpegError(
        f"{name}을(를) 찾을 수 없습니다. setup_windows.bat을 다시 실행하거나 "
        "'conda install -c conda-forge ffmpeg'로 설치하세요."
    )


def run(
    args: Sequence[str],
    *,
    tool: str = "ffmpeg",
    duration: Optional[float] = None,
    progress: Optional[ProgressCallback] = None,
    is_cancelled: Optional[Callable[[], bool]] = None,
) -> subprocess.CompletedProcess:
    """FFmpeg/ffprobe를 실행하고 stdout·stderr를 문자열로 돌려준다.

    duration과 progress를 주면 FFmpeg의 -progress 출력을 읽어 진행률을 알린다.
    is_cancelled가 True를 돌려주면 프로세스를 끝내고 Cancelled를 던진다.
    """
    cmd = [find_tool(tool)]
    if tool == "ffmpeg":
        cmd += ["-hide_banner", "-nostdin"]
        if progress and duration:
            cmd += ["-progress", "pipe:1", "-nostats"]
    cmd += list(args)

    proc = subprocess.Popen(
        cmd,
        # pythonw(콘솔 없는 실행)에서는 표준 입력이 없어 그대로 두면 실행이 실패할 수 있다.
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        creationflags=_CREATE_NO_WINDOW,
    )

    # stderr는 분석 결과(ebur128 등)가 길게 나오므로 별도 스레드 없이
    # communicate로 모으고, 진행률이 필요할 때만 stdout을 줄 단위로 읽는다.
    stdout_lines: list[bytes] = []
    if progress and duration:
        import threading

        stderr_chunks: list[bytes] = []
        t = threading.Thread(
            target=lambda: stderr_chunks.append(proc.stderr.read()), daemon=True
        )
        t.start()
        assert proc.stdout is not None
        for raw in proc.stdout:
            stdout_lines.append(raw)
            if is_cancelled and is_cancelled():
                proc.kill()
                proc.wait()
                raise Cancelled()
            line = raw.decode("utf-8", "replace").strip()
            if line.startswith("out_time_us=") or line.startswith("out_time_ms="):
                try:
                    us = int(line.split("=", 1)[1])
                except ValueError:
                    continue
                progress(max(0.0, min(1.0, us / 1_000_000 / duration)))
        proc.wait()
        t.join()
        stdout = b"".join(stdout_lines)
        stderr = b"".join(stderr_chunks)
    else:
        stdout, stderr = proc.communicate()
        if is_cancelled and is_cancelled():
            raise Cancelled()

    out = stdout.decode("utf-8", "replace")
    err = stderr.decode("utf-8", "replace")
    if proc.returncode != 0:
        tail = "\n".join(err.strip().splitlines()[-15:])
        raise FFmpegError(f"{tool} 실행 실패 (코드 {proc.returncode}):\n{tail}")
    return subprocess.CompletedProcess(cmd, proc.returncode, out, err)
