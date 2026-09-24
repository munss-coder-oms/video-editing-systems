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


def check_pyside() -> str:
    import PySide6
    from PySide6.QtWidgets import QApplication  # noqa: F401  (DLL까지 실제로 불러오는지 확인)

    return f"PySide6 {PySide6.__version__}"


def check_qt_window() -> str:
    from PySide6.QtWidgets import QApplication, QLabel

    app = QApplication.instance() or QApplication([])
    label = QLabel("점검")
    label.show()
    app.processEvents()
    label.close()
    return f"창 만들기 성공 (플랫폼: {app.platformName()})"


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


def run_all() -> List[Result]:
    return [
        _check("파이썬", check_python),
        _check("화면 라이브러리(PySide6)", check_pyside),
        _check("창 띄우기", check_qt_window),
        _check("FFmpeg", check_tool("ffmpeg")),
        _check("FFprobe", check_tool("ffprobe")),
        _check("음량 엔진", check_engine),
    ]


def format_results(results: List[Result]) -> str:
    lines = [
        "영상 편집 자동화 - 설치 점검 결과",
        f"윈도우: {platform.platform()}",
        f"폴더: {os.getcwd()}",
        "",
    ]
    for name, ok, detail in results:
        lines.append(f"[{'정상' if ok else '문제'}] {name}: {detail}")
    lines.append("")
    if all(ok for _, ok, _ in results):
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
        if log.is_file():
            tail = log.read_text(encoding="utf-8", errors="replace").splitlines()[-30:]
            lines += ["", f"---- {name} 마지막 부분 ----", *tail]
    return lines


def main(argv: List[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass
    results = run_all()
    text = format_results(results) + "\n" + "\n".join(_extra_lines())
    print(text)
    if argv:
        # 메모장이 인코딩을 헷갈리지 않게 BOM을 붙인 UTF-8로 저장한다.
        Path(argv[0]).write_text(text, encoding="utf-8-sig")
    return 0 if all(ok for _, ok, _ in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
