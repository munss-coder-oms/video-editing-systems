"""앱 실행: python -m app

pythonw로 실행하면 콘솔이 없어 오류가 보이지 않으므로,
실행 과정과 오류를 로그 파일에 남기고 창으로도 알린다 (PRD 7.4 원칙 6).
Qt(PySide6)를 불러오지 못한 경우에도 윈도우 기본 알림 창으로 알린다.

    python -m app                      AI 편집 도우미 창 (자동화 버튼 + 대화 칸, ⋯ > 연결 점검)
    python -m app --legacy             예전 창 (영상을 넣어 음량 정리 → 리졸브용 파일 내보내기)
    python -m app --smoke-test 영상    예전 창을 띄우고 영상을 처리한 뒤 스스로 종료 (자동 검사용)
    python -m app --helper-smoke-test  스크립트를 설치하고 AI 도우미 창을 띄운 뒤 스스로 종료 (자동 검사용)
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
    mode = argv[0] if argv[:1] in (["--smoke-test"], ["--legacy"], ["--helper-smoke-test"]) else ""
    legacy = mode in ("--smoke-test", "--legacy")
    try:
        from PySide6 import __version__ as pyside_version
        from PySide6.QtCore import QTimer
        from PySide6.QtWidgets import QApplication

        if legacy:
            from .main_window import MainWindow
        else:
            from .companion.window import HelperWindow
    except Exception:
        report_crash(*sys.exc_info())
        return 1
    startup_log(f"PySide6 {pyside_version} 불러옴")

    smoke_video = None
    if mode == "--smoke-test":
        if len(argv) < 2:
            native_message("영상 편집 자동화", "--smoke-test 다음에 영상 경로가 필요합니다.")
            return 2
        smoke_video = argv[1]
    helper_smoke = mode == "--helper-smoke-test"
    if smoke_video or helper_smoke:
        # 자동 검사에서는 알림 창을 띄우면 멈추므로 오류를 출력만 하고, 한글이 깨지지 않게 한다.
        for stream in (sys.stdout, sys.stderr):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except (AttributeError, ValueError):
                pass
        sys.excepthook = lambda *exc: (
            startup_log("오류\n" + "".join(traceback.format_exception(*exc))),
            traceback.print_exception(*exc),
        )

    install = None
    try:
        app = QApplication(sys.argv[:1])
        if legacy:
            app.setApplicationName("영상 편집 자동화")
            window = MainWindow(interactive=smoke_video is None)
        else:
            app.setApplicationName("AI 도우미")
            install = install_resolve_script()
            window = HelperWindow(
                interactive=not helper_smoke,
                install=install,
                report_dir=log_dir() if helper_smoke else None,
            )
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
    if helper_smoke:
        return _run_helper_smoke_test(app, window, install, QTimer)
    return app.exec()


def install_resolve_script() -> dict:
    """켤 때마다 리졸브 Scripts 메뉴의 스크립트를 최신으로 맞춘다 (내용이 같으면 그대로 둔다).

    우체통 폴더가 바뀌었을 수도 있어서, 스크립트 안의 경로를 늘 이 앱과 같게 해 둔다.
    실패해도 창은 띄운다 (결과 파일에 이유가 남는다).
    """
    try:
        from engine.resolve_link.install import install_script

        result = install_script()
    except Exception as exc:
        startup_log(f"리졸브 스크립트 설치 실패: {exc!r}")
        return {"error": f"{type(exc).__name__}: {exc}"}
    startup_log(result.message)
    return {"message": result.message, "paths": [str(p) for p in result.paths],
            "mailbox": str(result.mailbox), "version": result.version}


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
            print("SMOKE OK", flush=True)
            print(window.report.toPlainText(), flush=True)
            outcome["code"] = 0
            app.quit()
        elif window.thread is None and window.stage.text() == "실패했습니다.":
            print("SMOKE FAIL:", window.report.toPlainText())
            app.quit()

    QTimer.singleShot(500, start)
    timer = QTimer()
    timer.timeout.connect(poll)
    timer.start(300)
    QTimer.singleShot(300_000, lambda: (print("SMOKE FAIL: 5분 안에 끝나지 않음"), app.quit()))
    app.exec()
    return outcome["code"]


def _run_helper_smoke_test(app, window, install, QTimer) -> int:
    """리졸브 없이 AI 도우미 창을 확인한다: 스크립트 설치, 창이 멈추지 않는지, 결과 파일 저장."""
    import time

    outcome = {"code": 1, "done": False}
    started = time.monotonic()
    # 첫 틱부터 잰다: 창을 처음 그리는 시간은 리졸브의 답을 기다리는 동안 멈추는지와 상관없다.
    ticks = {"last": None, "worst": 0.0, "at": 0.0, "doing": ""}

    def finish(code: int, message: str) -> None:
        if outcome["done"]:
            return
        outcome["done"] = True
        outcome["code"] = code
        print(message, flush=True)
        window.close()
        app.quit()

    def tick():
        # 리졸브의 답을 기다리는 동안에도 창이 계속 움직이는지 (0.1초마다 불려야 함)
        now = time.monotonic()
        if ticks["last"] is not None and now - ticks["last"] > ticks["worst"]:
            # 다시 실패하면 언제, 무엇을 하던 중이었는지 알 수 있게 남긴다
            ticks["worst"] = now - ticks["last"]
            ticks["at"] = ticks["last"] - started
            ticks["doing"] = window.message.text() if hasattr(window, "message") else ""
        ticks["last"] = now

    def check_install():
        paths = [Path(p) for p in (install or {}).get("paths", [])]
        if not paths or not all(p.is_file() for p in paths):
            finish(1, f"HELPER SMOKE FAIL: 스크립트가 설치되지 않음 {install}")
            return
        print(f"스크립트: {paths[0]}", flush=True)

    def check_panel():
        # 자동화 버튼 3개, 대화 입력 칸, ⋯ 메뉴 항목, 아래쪽 버튼이 잘리지 않고 보이는지 (QT_SCALE_FACTOR 1.0/1.5)
        problems = window.smoke_check()
        metrics = window.screen_metrics()
        print(f"화면: {metrics.get('available')} 배율 {metrics.get('device_pixel_ratio')} "
              f"창 {metrics.get('window')} 모양 {metrics.get('layout')}", flush=True)
        if problems:
            finish(1, f"HELPER SMOKE FAIL: 창이 제대로 보이지 않음 {problems}")
            return
        print(f"창 모양: 3 slots, chat input, menu {window.menu_entries()}, footer - 보임", flush=True)

    def save_report():
        if window.session.auto_attempts < 1:
            finish(1, "HELPER SMOKE FAIL: 리졸브 연결 확인을 한 번도 하지 않음")
            return
        window.on_report()

    def poll():
        if window.last_report is None:
            return
        tick()  # 결과 저장 바로 뒤에 멈춘 시간도 빠짐없이 센다
        if not window.last_report.is_file():
            finish(1, f"HELPER SMOKE FAIL: 결과 파일 없음 {window.last_report}")
        elif ticks["worst"] > 1.0:
            finish(1, f"HELPER SMOKE FAIL: 창이 {ticks['worst']:.1f}초 멈춤 "
                      f"(시작 후 {ticks['at']:.1f}초부터, 그 뒤 화면 문구: {ticks['doing']!r})")
        else:
            print(f"결과 파일: {window.last_report}", flush=True)
            print(f"가장 길게 멈춘 시간: {ticks['worst']:.2f}초", flush=True)
            finish(0, "HELPER SMOKE OK")

    tick_timer = QTimer()
    tick_timer.timeout.connect(tick)
    tick_timer.start(100)
    QTimer.singleShot(500, check_install)
    QTimer.singleShot(1500, check_panel)
    QTimer.singleShot(3500, save_report)
    timer = QTimer()
    timer.timeout.connect(poll)
    timer.start(200)
    QTimer.singleShot(20_000, lambda: finish(1, "HELPER SMOKE FAIL: 20초 안에 끝나지 않음"))
    app.exec()
    return outcome["code"]


if __name__ == "__main__":
    raise SystemExit(main())
