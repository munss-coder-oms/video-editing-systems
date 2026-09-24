"""앱 실행: python -m app

pythonw로 실행하면 콘솔이 없어 오류가 보이지 않으므로,
실행 과정과 오류를 로그 파일에 남기고 창으로도 알린다 (PRD 7.4 원칙 6).
Qt(PySide6)를 불러오지 못한 경우에도 윈도우 기본 알림 창으로 알린다.

    python -m app                    앱 실행
    python -m app --smoke-test 영상  창을 띄우고 영상을 처리한 뒤 스스로 종료 (자동 검사용)
"""

from __future__ import annotations

import datetime
import os
import sys
import traceback
from pathlib import Path


def log_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / ".local" / "share")
    path = Path(base) / "video-editing-systems"
    path.mkdir(parents=True, exist_ok=True)
    return path


def startup_log(message: str) -> None:
    try:
        with (log_dir() / "app.log").open("a", encoding="utf-8") as f:
            f.write(f"{datetime.datetime.now():%Y-%m-%d %H:%M:%S} {message}\n")
    except OSError:
        pass


def native_message(title: str, text: str) -> None:
    """Qt 없이도 뜨는 윈도우 기본 알림 창. 윈도우가 아니면 표준 오류로 출력."""
    if sys.platform == "win32":
        try:
            import ctypes

            ctypes.windll.user32.MessageBoxW(None, text, title, 0x10)  # MB_ICONERROR
            return
        except Exception:
            pass
    if sys.stderr:
        sys.stderr.write(f"{title}\n{text}\n")


def report_crash(exc_type, exc, tb) -> None:
    text = "".join(traceback.format_exception(exc_type, exc, tb))
    startup_log("오류\n" + text)
    log_file = log_dir() / "app.log"
    message = (
        "앱을 실행하는 중에 오류가 났습니다.\n\n"
        f"{text[-1200:]}\n"
        f"기록 파일: {log_file}\n\n"
        "이 창의 내용이나 기록 파일을 그대로 보내 주세요."
    )
    try:
        from PySide6.QtWidgets import QApplication, QMessageBox

        if QApplication.instance():
            QMessageBox.critical(None, "영상 편집 자동화 - 오류", message)
            return
    except Exception:
        pass
    native_message("영상 편집 자동화 - 오류", message)


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    sys.excepthook = report_crash
    startup_log(f"시작: python {sys.version.split()[0]} ({sys.executable}), 폴더 {os.getcwd()}")
    try:
        from PySide6 import __version__ as pyside_version
        from PySide6.QtCore import QTimer
        from PySide6.QtWidgets import QApplication

        from .main_window import MainWindow
    except Exception:
        report_crash(*sys.exc_info())
        return 1
    startup_log(f"PySide6 {pyside_version} 불러옴")

    smoke_video = None
    if argv[:1] == ["--smoke-test"]:
        if len(argv) < 2:
            native_message("영상 편집 자동화", "--smoke-test 다음에 영상 경로가 필요합니다.")
            return 2
        smoke_video = argv[1]

    try:
        app = QApplication(sys.argv[:1])
        app.setApplicationName("영상 편집 자동화")
        window = MainWindow(interactive=smoke_video is None)
        window.show()
        window.raise_()
        window.activateWindow()
        startup_log("창 띄움")
        # run_app.bat이 창이 정말 떴는지 확인하는 표시 파일
        try:
            (log_dir() / "window_ok.flag").write_text("ok", encoding="utf-8")
        except OSError:
            pass
    except Exception:
        report_crash(*sys.exc_info())
        return 1

    if smoke_video:
        return _run_smoke_test(app, window, smoke_video, QTimer)
    return app.exec()


def _run_smoke_test(app, window, video: str, QTimer) -> int:
    """창에서 실제 버튼 흐름대로 영상을 처리해 보고, 성공하면 0으로 끝낸다."""
    outcome = {"code": 1}

    def start():
        window.load_video(video)
        if not window.run_btn.isEnabled():
            print("SMOKE FAIL: 영상을 열지 못함")
            app.quit()
            return
        window.start()

    def poll():
        if window.thread is None and window.result is not None:
            print("SMOKE OK")
            print(window.report.toPlainText())
            outcome["code"] = 0
            app.quit()
        elif window.thread is None and window.stage.text() == "실패했습니다.":
            print("SMOKE FAIL:", window.report.toPlainText())
            app.quit()

    QTimer.singleShot(500, start)
    timer = QTimer()
    timer.timeout.connect(poll)
    timer.start(300)
    QTimer.singleShot(300_000, app.quit)
    app.exec()
    return outcome["code"]


if __name__ == "__main__":
    raise SystemExit(main())
