"""설치 상태 점검: python -m app.diagnose [결과 파일]

앱이 안 켜질 때 무엇이 빠졌는지 한 번에 확인하고, 결과를 텍스트 파일로 남긴다.
check_setup.bat이 이 모듈을 부른다.
"""

from __future__ import annotations

import os
import platform
import subprocess
import sys
import tempfile
import traceback
from pathlib import Path
from typing import Callable, List, Tuple

Result = Tuple[str, bool, str]


def _check(name: str, fn: Callable[[], str]) -> Result:
    try:
        return name, True, fn()
    except Exception as exc:  # 점검은 어떤 오류에서도 계속 진행한다
        detail = "".join(traceback.format_exception_only(type(exc), exc)).strip()
        return name, False, detail


def check_python() -> str:
    return f"{sys.version.split()[0]} ({sys.executable})"


def _pyside_inproc() -> str:
    import PySide6
    from PySide6.QtWidgets import QApplication  # noqa: F401  (DLL까지 실제로 불러오는지 확인)

    return f"PySide6 {PySide6.__version__}"


def _qt_window_inproc() -> str:
    from PySide6.QtWidgets import QApplication, QLabel

    app = QApplication.instance() or QApplication([])
    label = QLabel("점검")
    label.show()
    app.processEvents()
    label.close()
    return f"창 만들기 성공 (플랫폼: {app.platformName()})"


def _in_subprocess(func_name: str) -> Callable[[], str]:
    """화면 라이브러리 점검은 따로 띄운 파이썬에서 한다.

    DLL 충돌은 파이썬 오류가 아니라 프로세스가 통째로 죽는 경우가 있어서,
    같은 프로세스에서 하면 결과 파일조차 못 남긴다.
    """

    def run() -> str:
        env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
        proc = subprocess.run(
            [sys.executable, "-c", f"from app.diagnose import {func_name} as f; print(f())"],
            capture_output=True, text=True, stdin=subprocess.DEVNULL, encoding="utf-8",
            errors="replace", timeout=120, env=env, cwd=str(Path(__file__).resolve().parent.parent),
        )
        out = proc.stdout.strip().splitlines()
        if proc.returncode == 0 and out:
            return out[-1]
        err = (proc.stderr or "").strip().splitlines()
        detail = err[-1] if err else "출력 없음"
        raise RuntimeError(f"종료 코드 {proc.returncode}: {detail}")

    return run


check_pyside = _in_subprocess("_pyside_inproc")
check_qt_window = _in_subprocess("_qt_window_inproc")


def check_tool(name: str) -> Callable[[], str]:
    def run() -> str:
        from engine.ffmpeg import find_tool

        path = find_tool(name)
        out = subprocess.run(
            [path, "-version"], capture_output=True, text=True, stdin=subprocess.DEVNULL,
            encoding="utf-8", errors="replace", timeout=30,
        ).stdout
        return f"{out.splitlines()[0] if out else '?'} ({path})"

    return run


def check_engine() -> str:
    """5초짜리 시험 영상을 만들어 실제로 음량 정리까지 해 본다."""
    from engine.ffmpeg import find_tool
    from engine.job import process_video

    with tempfile.TemporaryDirectory() as tmp:
        video = Path(tmp) / "점검 영상.mp4"
        subprocess.run(
            [
                find_tool("ffmpeg"), "-v", "error", "-y", "-nostdin",
                "-f", "lavfi", "-i", "testsrc2=s=320x240:r=30:d=5",
                "-f", "lavfi", "-i", "anoisesrc=c=pink:a=0.05:d=5:r=48000",
                "-map", "0:v", "-map", "1:a", "-c:v", "mpeg4", "-c:a", "aac", "-shortest", str(video),
            ],
            check=True, capture_output=True, stdin=subprocess.DEVNULL,
        )
        result = process_video(video)
        after = result.balance.after
        return f"시험 영상 처리 성공: {after.integrated:.1f} LUFS, 최대치 {after.true_peak:.1f} dBTP"


CHECKS: List[Tuple[str, Callable[[], str]]] = [
    ("파이썬", check_python),
    ("화면 라이브러리(PySide6)", check_pyside),
    ("창 띄우기", check_qt_window),
    ("FFmpeg", check_tool("ffmpeg")),
    ("FFprobe", check_tool("ffprobe")),
    ("음량 엔진", check_engine),
]


def run_all(on_result: Callable[[List[Result]], None] | None = None) -> List[Result]:
    results: List[Result] = []
    for name, fn in CHECKS:
        results.append(_check(name, fn))
        if on_result:
            on_result(results)
    return results


def format_results(results: List[Result], finished: bool = True) -> str:
    lines = [
        "영상 편집 자동화 - 설치 점검 결과",
        f"윈도우: {platform.platform()}",
        f"폴더: {os.getcwd()}",
        "",
    ]
    for name, ok, detail in results:
        lines.append(f"[{'정상' if ok else '문제'}] {name}: {detail}")
    lines.append("")
    if not finished:
        lines.append("[문제] 점검이 끝나기 전에 멈췄습니다. 위 목록 다음 항목에서 문제가 난 것입니다.")
        lines.append("이 파일 내용을 그대로 보내 주세요.")
    elif all(ok for _, ok, _ in results):
        lines.append("모두 정상입니다. run_app.bat 또는 바탕화면 아이콘으로 앱을 실행하세요.")
    else:
        lines.append("[문제]로 표시된 줄이 있습니다. 이 파일 내용을 그대로 보내 주세요.")
    return "\n".join(lines)


def _extra_lines() -> List[str]:
    """문제 파악에 필요한 추가 정보: conda 위치와 앱 실행 기록의 마지막 부분."""
    lines = ["", f"conda: {os.environ.get('CONDA_BAT') or os.environ.get('CONDA_EXE') or '?'}"]
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / ".local" / "share")
    for name in ("app.log", "launch.log"):
        log = Path(base) / "video-editing-systems" / name
        try:
            tail = log.read_text(encoding="utf-8", errors="replace").splitlines()[-30:]
        except OSError:  # 파일이 없거나 다른 프로그램이 잡고 있음
            continue
        lines += ["", f"---- {name} 마지막 부분 ----", *tail]
    return lines


def main(argv: List[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass
    out = Path(argv[0]) if argv else None
    extra = "\n".join(_extra_lines())

    def save(results: List[Result], finished: bool) -> str:
        text = format_results(results, finished) + "\n" + extra
        if out:
            # 항목마다 바로 저장해서, 점검 도중 멈춰도 어디까지 됐는지 남는다.
            # 메모장이 인코딩을 헷갈리지 않게 BOM을 붙인 UTF-8로 저장한다.
            try:
                out.write_text(text, encoding="utf-8-sig")
            except OSError as exc:
                print(f"결과 파일을 저장하지 못했습니다: {exc}", file=sys.stderr)
        return text

    save([], finished=False)
    results = run_all(on_result=lambda r: save(r, finished=False))
    print(save(results, finished=True))
    return 0 if all(ok for _, ok, _ in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
