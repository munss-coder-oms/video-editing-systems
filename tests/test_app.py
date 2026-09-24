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


def test_diagnose_keeps_partial_result(tmp_path, monkeypatch):
    """점검 도중 한 항목이 프로세스를 죽여도 결과 파일에 거기까지의 내용이 남는다."""
    from app import diagnose

    crashing = diagnose._in_subprocess("no_such_check")
    monkeypatch.setattr(
        diagnose, "CHECKS", [("파이썬", diagnose.check_python), ("죽는 항목", crashing)]
    )
    out = tmp_path / "result.txt"
    assert diagnose.main([str(out)]) == 1
    text = out.read_text(encoding="utf-8-sig")
    assert "[정상] 파이썬" in text
    assert "[문제] 죽는 항목: RuntimeError: 종료 코드 1" in text
