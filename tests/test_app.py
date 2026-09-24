"""화면 라이브러리가 실제로 불러와지는지 확인한다.

윈도우에서 pip PySide6와 conda-forge FFmpeg의 DLL이 충돌하면 앱 창이 뜨지 않는데,
엔진 테스트만으로는 이 문제를 잡지 못했다.
"""

from __future__ import annotations

import os

import pytest


def test_qtwidgets_loads():
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication  # noqa: F401


def test_main_window_opens():
    pytest.importorskip("PySide6")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from app.main_window import MainWindow

    app = QApplication.instance() or QApplication([])
    window = MainWindow(interactive=False)
    window.show()
    app.processEvents()
    assert window.run_btn.text().startswith("③")
    window.close()
