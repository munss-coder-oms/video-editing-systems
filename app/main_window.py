"""데스크톱 앱 첫 화면.

화면은 엔진(engine 패키지)의 process_video 하나만 부른다.
처리 로직은 여기 두지 않는다 (PRD 7.4 원칙 1).
"""

from __future__ import annotations

import traceback
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QObject, QSettings, Qt, QThread, QUrl, Signal, Slot
from PySide6.QtGui import QDesktopServices, QDragEnterEvent, QDropEvent, QFont
from PySide6.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from engine import __version__
from engine.commands import STRENGTH_LABELS, STRENGTHS
from engine.ffmpeg import Cancelled, FFmpegError, find_tool
from engine.job import JobResult, default_output_dir, process_video
from engine.probe import MediaInfo, probe

VIDEO_FILTER = "영상 파일 (*.mp4 *.mov *.mkv *.m4v *.avi *.mts *.m2ts *.webm);;모든 파일 (*.*)"
VIDEO_SUFFIXES = {".mp4", ".mov", ".mkv", ".m4v", ".avi", ".mts", ".m2ts", ".webm", ".wav", ".mp3", ".m4a"}


class Worker(QObject):
    progress = Signal(str, float)
    finished = Signal(object)
    failed = Signal(str)
    cancelled = Signal()

    def __init__(self, video: str, output_dir: str, strength: str, target: float):
        super().__init__()
        self.video = video
        self.output_dir = output_dir
        self.strength = strength
        self.target = target
        self._cancel = False

    def cancel(self) -> None:
        self._cancel = True

    @Slot()
    def run(self) -> None:
        try:
            result = process_video(
                self.video,
                output_dir=self.output_dir,
                strength=self.strength,
                target_lufs=self.target,
                on_stage=lambda stage, value: self.progress.emit(stage, value),
                is_cancelled=lambda: self._cancel,
            )
        except Cancelled:
            self.cancelled.emit()
        except (FFmpegError, FileNotFoundError, ValueError) as exc:
            self.failed.emit(str(exc))
        except Exception:  # 예상 못 한 오류도 화면에 보여준다
            self.failed.emit(traceback.format_exc())
        else:
            self.finished.emit(result)


class DropArea(QFrame):
    fileDropped = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setFrameShape(QFrame.StyledPanel)
        self.setMinimumHeight(110)
        self.setObjectName("dropArea")
        layout = QVBoxLayout(self)
        self.label = QLabel("① 편집할 영상 파일을 여기에 끌어다 놓으세요\n(또는 아래 [영상 열기] 버튼)")
        self.label.setAlignment(Qt.AlignCenter)
        self.label.setWordWrap(True)
        layout.addWidget(self.label)

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        urls = event.mimeData().urls()
        if urls and Path(urls[0].toLocalFile()).suffix.lower() in VIDEO_SUFFIXES:
            event.acceptProposedAction()

    def dropEvent(self, event: QDropEvent) -> None:
        urls = event.mimeData().urls()
        if urls:
            self.fileDropped.emit(urls[0].toLocalFile())


class MainWindow(QMainWindow):
    def __init__(self, interactive: bool = True):
        super().__init__()
        # interactive=False(자동 검사)이면 알림 창을 띄우지 않는다.
        self.interactive = interactive
        self.setWindowTitle(f"영상 편집 자동화 v{__version__} - 음량 정리 + 다빈치 리졸브 내보내기")
        self.resize(760, 680)
        self.settings = QSettings("munss-coder-oms", "video-editing-systems")

        self.media: Optional[MediaInfo] = None
        self.output_dir: Optional[str] = None
        self.result: Optional[JobResult] = None
        self.thread: Optional[QThread] = None
        self.worker: Optional[Worker] = None

        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)

        intro = QLabel(
            "이 프로그램은 영상의 음량을 정리한 파일을 만듭니다. 다빈치 리졸브와 직접 연결되지는 않고, "
            "여기서 만든 파일을 리졸브에서 [파일 → 가져오기 → 타임라인]으로 불러옵니다."
        )
        intro.setWordWrap(True)
        root.addWidget(intro)

        # 1. 영상 열기
        self.drop = DropArea()
        self.drop.fileDropped.connect(self.load_video)
        root.addWidget(self.drop)

        open_row = QHBoxLayout()
        self.open_btn = QPushButton("영상 열기...")
        self.open_btn.clicked.connect(self.choose_video)
        open_row.addWidget(self.open_btn)
        open_row.addStretch(1)
        root.addLayout(open_row)

        # 2. 설정
        form = QFormLayout()
        self.strength = QComboBox()
        for key in STRENGTHS:
            self.strength.addItem(STRENGTH_LABELS[key], key)
        saved = self.settings.value("strength", "medium")
        self.strength.setCurrentIndex(max(0, list(STRENGTHS).index(saved) if saved in STRENGTHS else 1))
        self.strength.setToolTip(
            "튀는 소리를 얼마나 누르고, 작은 목소리를 얼마나 끌어올릴지 정합니다.\n"
            "목소리가 답답하게 들리면 '약하게', 들쭉날쭉하면 '강하게'."
        )
        form.addRow("② 정리 강도", self.strength)

        self.target = QDoubleSpinBox()
        self.target.setRange(-30.0, -5.0)
        self.target.setSingleStep(0.5)
        self.target.setDecimals(1)
        self.target.setSuffix(" LUFS")
        self.target.setValue(float(self.settings.value("target", -14.0)))
        self.target.setToolTip("유튜브 기준은 -14 LUFS입니다.")
        form.addRow("목표 음량", self.target)

        out_row = QHBoxLayout()
        self.out_label = QLabel("영상을 열면 영상 옆에 '<이름>_resolve' 폴더를 만듭니다")
        self.out_label.setWordWrap(True)
        self.out_btn = QPushButton("바꾸기...")
        self.out_btn.clicked.connect(self.choose_output)
        out_row.addWidget(self.out_label, 1)
        out_row.addWidget(self.out_btn)
        form.addRow("결과 폴더", out_row)
        root.addLayout(form)

        # 3. 실행
        run_row = QHBoxLayout()
        self.run_btn = QPushButton("③ 음량 정리하고 리졸브용으로 내보내기")
        self.run_btn.setMinimumHeight(40)
        self.run_btn.setEnabled(False)
        self.run_btn.clicked.connect(self.start)
        self.cancel_btn = QPushButton("취소")
        self.cancel_btn.setEnabled(False)
        self.cancel_btn.clicked.connect(self.cancel)
        run_row.addWidget(self.run_btn, 1)
        run_row.addWidget(self.cancel_btn)
        root.addLayout(run_row)

        self.progress = QProgressBar()
        self.progress.setRange(0, 1000)
        self.progress.setTextVisible(False)
        self.stage = QLabel("")
        root.addWidget(self.progress)
        root.addWidget(self.stage)

        # 4. 결과
        self.report = QPlainTextEdit()
        self.report.setReadOnly(True)
        mono = QFont("Consolas")
        mono.setStyleHint(QFont.Monospace)
        self.report.setFont(mono)
        self.report.setPlaceholderText("처리 결과(처리 전/후 음량 비교)가 여기에 나옵니다.")
        root.addWidget(self.report, 1)

        done_row = QHBoxLayout()
        self.open_out_btn = QPushButton("④ 결과 폴더 열기")
        self.open_out_btn.setEnabled(False)
        self.open_out_btn.clicked.connect(self.open_output)
        self.guide_btn = QPushButton("리졸브에서 불러오는 방법")
        self.guide_btn.clicked.connect(self.show_guide)
        done_row.addWidget(self.open_out_btn)
        done_row.addWidget(self.guide_btn)
        done_row.addStretch(1)
        root.addLayout(done_row)

        self.check_ffmpeg()

    # ---------- 영상/폴더 고르기 ----------

    def check_ffmpeg(self) -> None:
        try:
            find_tool("ffmpeg")
            find_tool("ffprobe")
        except FFmpegError as exc:
            self.stage.setText(f"FFmpeg 없음: {exc}")
            if self.interactive:
                QMessageBox.warning(self, "FFmpeg 없음", str(exc))

    def choose_video(self) -> None:
        start = self.settings.value("last_dir", str(Path.home()))
        path, _ = QFileDialog.getOpenFileName(self, "영상 열기", start, VIDEO_FILTER)
        if path:
            self.load_video(path)

    @Slot(str)
    def load_video(self, path: str) -> None:
        if self.thread is not None:
            return
        try:
            media = probe(path)
        except (FFmpegError, FileNotFoundError) as exc:
            self.stage.setText(f"영상을 열 수 없음: {exc}")
            if self.interactive:
                QMessageBox.warning(self, "영상을 열 수 없음", str(exc))
            return
        if not media.has_audio:
            self.stage.setText("이 파일에는 오디오 트랙이 없습니다.")
            if self.interactive:
                QMessageBox.warning(self, "오디오 없음", "이 파일에는 오디오 트랙이 없습니다.")
            return
        self.media = media
        self.result = None
        self.settings.setValue("last_dir", str(Path(path).parent))
        self.output_dir = str(default_output_dir(path))
        self.drop.label.setText(f"{Path(path).name}\n{media.summary()}")
        self.out_label.setText(self.output_dir)
        self.run_btn.setEnabled(True)
        self.open_out_btn.setEnabled(False)
        self.report.clear()
        self.progress.setValue(0)
        self.stage.setText("")

    def choose_output(self) -> None:
        start = self.output_dir or self.settings.value("last_dir", str(Path.home()))
        folder = QFileDialog.getExistingDirectory(self, "결과 폴더 고르기", start)
        if folder:
            self.output_dir = folder
            self.out_label.setText(folder)

    # ---------- 실행 ----------

    def start(self) -> None:
        if not self.media or self.thread is not None:
            return
        strength = self.strength.currentData()
        target = self.target.value()
        self.settings.setValue("strength", strength)
        self.settings.setValue("target", target)

        self.worker = Worker(self.media.path, self.output_dir, strength, target)
        self.thread = QThread(self)
        self.worker.moveToThread(self.thread)
        self.thread.started.connect(self.worker.run)
        self.worker.progress.connect(self.on_progress)
        self.worker.finished.connect(self.on_finished)
        self.worker.failed.connect(self.on_failed)
        self.worker.cancelled.connect(self.on_cancelled)
        for sig in (self.worker.finished, self.worker.failed, self.worker.cancelled):
            sig.connect(self.thread.quit)
        self.thread.finished.connect(self.on_thread_done)

        self.set_busy(True)
        self.report.setPlainText("처리 중입니다. 영상 길이에 따라 몇 분 걸릴 수 있습니다.")
        self.thread.start()

    def cancel(self) -> None:
        if self.worker:
            self.worker.cancel()
            self.stage.setText("취소하는 중...")
            self.cancel_btn.setEnabled(False)

    def set_busy(self, busy: bool) -> None:
        self.run_btn.setEnabled(not busy and self.media is not None)
        self.cancel_btn.setEnabled(busy)
        self.open_btn.setEnabled(not busy)
        self.out_btn.setEnabled(not busy)
        self.strength.setEnabled(not busy)
        self.target.setEnabled(not busy)
        self.drop.setAcceptDrops(not busy)

    @Slot(str, float)
    def on_progress(self, stage: str, value: float) -> None:
        self.progress.setValue(int(value * 1000))
        self.stage.setText(f"{stage}... {value * 100:.0f}%")

    @Slot(object)
    def on_finished(self, result: JobResult) -> None:
        self.result = result
        self.progress.setValue(1000)
        self.stage.setText("완료! 결과 폴더의 .fcpxml 파일을 다빈치 리졸브에서 불러오세요.")
        self.report.setPlainText(result.summary() + f"\n\n결과 폴더: {result.output_dir}")
        self.open_out_btn.setEnabled(True)

    @Slot(str)
    def on_failed(self, message: str) -> None:
        self.stage.setText("실패했습니다.")
        self.report.setPlainText(f"오류가 났습니다.\n\n{message}")
        if self.interactive:
            QMessageBox.critical(self, "처리 실패", message[-1500:])

    @Slot()
    def on_cancelled(self) -> None:
        self.progress.setValue(0)
        self.stage.setText("취소했습니다.")
        self.report.setPlainText("취소했습니다. 원본 영상은 바뀌지 않았습니다.")

    @Slot()
    def on_thread_done(self) -> None:
        if self.thread:
            self.thread.deleteLater()
        if self.worker:
            self.worker.deleteLater()
        self.thread = None
        self.worker = None
        self.set_busy(False)

    # ---------- 결과 ----------

    def open_output(self) -> None:
        if self.result:
            QDesktopServices.openUrl(QUrl.fromLocalFile(self.result.output_dir))

    def show_guide(self) -> None:
        from engine.job import RESOLVE_GUIDE

        fcpxml = Path(self.result.fcpxml).name if self.result and self.result.fcpxml else "<이름>_timeline.fcpxml"
        wav = Path(self.result.balance.output_wav).name if self.result else "<이름>_balanced.wav"
        QMessageBox.information(self, "리졸브에서 불러오는 방법", RESOLVE_GUIDE.format(fcpxml=fcpxml, wav=wav))

    def closeEvent(self, event) -> None:
        if self.thread is not None:
            answer = QMessageBox.question(self, "처리 중", "처리 중입니다. 취소하고 닫을까요?")
            if answer != QMessageBox.Yes:
                event.ignore()
                return
            if self.worker:
                self.worker.cancel()
            self.thread.quit()
            self.thread.wait(5000)
        event.accept()
