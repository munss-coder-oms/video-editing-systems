"""앱 실행: python -m app

pythonw로 실행하면 콘솔이 없어 오류가 보이지 않으므로,
예상 못 한 오류는 로그 파일에 남기고 창으로도 알린다 (PRD 7.4 원칙 6).
"""

from __future__ import annotations

import os
import sys
import traceback
from pathlib import Path


def _log_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / ".local" / "share")
    path = Path(base) / "video-editing-systems"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _report_crash(exc_type, exc, tb) -> None:
    text = "".join(traceback.format_exception(exc_type, exc, tb))
    log_file = _log_dir() / "crash.log"
    try:
        with log_file.open("a", encoding="utf-8") as f:
            f.write(text + "\n")
    except OSError:
        pass
    try:
        from PySide6.QtWidgets import QApplication, QMessageBox

        if QApplication.instance():
            QMessageBox.critical(
                None, "예상 못 한 오류", f"{text[-1500:]}\n\n기록 위치: {log_file}"
            )
    except Exception:
        pass


def main() -> int:
    sys.excepthook = _report_crash
    try:
        from PySide6.QtWidgets import QApplication

        from .main_window import MainWindow
    except Exception:
        _report_crash(*sys.exc_info())
        return 1

    app = QApplication(sys.argv)
    app.setApplicationName("영상 편집 자동화")
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
