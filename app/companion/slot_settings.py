"""⚙ 버튼 설정 쪽 (설계 B2.1, B2.2, 사용자 설명 2 "설정 바꾸기").

- 할 일(종류)과 설정 항목은 engine/automation/kinds.py의 표로 그린다 (새 자동화 = 종류 하나 더하기).
- 순서 바꾸기(▲ 위로 / ▼ 아래로)는 바로 설정 파일에 쓴다 (리졸브에 묻지 않음).
- [이전 설정으로 되돌리기]는 바로 전 설정(previous)이 있을 때만 보인다 (한 단계).
- 적용 범위 In~Out은 기능 점검에서 In~Out 읽기가 확인된 뒤에만 고를 수 있다 ("점검 뒤 켜져요").
- 값을 바꾸면 아래 "버튼 아래 요약" 줄이 바로 바뀐다. [저장]을 눌러야 버튼에 들어간다.
작업 중에도 열 수 있다 (설계 B10).
"""

from __future__ import annotations

import copy
from typing import Any, Dict, Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from engine.automation.kinds import KIND_ORDER, Param, kind as get_kind
from engine.resolve_link.ops import MARKER_COLORS

from . import fmt
from . import strings_ko as S
from .automation_view import kind_name, slot_summary


def _decimals(step: Optional[float]) -> int:
    if not step:
        return 1
    text = fmt.num(step)
    return len(text.split(".")[1]) if "." in text else 0


class SlotSettingsPage(QWidget):
    back_clicked = Signal()
    saved = Signal(int, str, str, dict)  # 버튼 번호, 할 일, 이름, 설정
    move_requested = Signal(int, int)  # 버튼 번호, -1(위로) / +1(아래로)
    restore_requested = Signal(int)

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("settingsPage")
        self.number: Optional[int] = None
        self.slot: Dict[str, Any] = {}
        self.in_out_ok: Optional[bool] = None
        self.fields: Dict[str, QWidget] = {}
        self._kind: Optional[str] = None
        self._params_by_kind: Dict[str, Dict[str, Any]] = {}

        root = QVBoxLayout(self)
        root.setSpacing(6)
        top = QHBoxLayout()
        self.back_btn = QPushButton(S.BTN_BACK)
        self.back_btn.clicked.connect(self.back_clicked)
        self.title = QLabel("")
        self.title.setProperty("role", "slot-title")
        self.title.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        top.addWidget(self.back_btn)
        top.addWidget(self.title, 1)
        root.addLayout(top)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        inner = QWidget()
        inner.setObjectName("settingsInner")
        self.inner_box = QVBoxLayout(inner)
        self.inner_box.setContentsMargins(0, 0, 4, 0)
        self.inner_box.setSpacing(6)
        self.form = QFormLayout()
        self.form.setRowWrapPolicy(QFormLayout.WrapLongRows)
        self.form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        self.kind_box = QComboBox()
        for key in KIND_ORDER:
            k = get_kind(key)
            label = kind_name(key) if k and k.ready else S.KIND_NOT_READY.format(name=kind_name(key))
            self.kind_box.addItem(label, key)
        self.kind_box.currentIndexChanged.connect(self._kind_changed)
        self.name_edit = QLineEdit()
        self.name_edit.setMaxLength(20)
        self.name_edit.textChanged.connect(self._update_preview)
        self.form.addRow(S.SETTINGS_KIND, self.kind_box)
        self.form.addRow(S.SETTINGS_NAME, self.name_edit)
        self.inner_box.addLayout(self.form)
        self.not_ready = QLabel(S.SETTINGS_NOT_READY)
        self.not_ready.setWordWrap(True)
        self.not_ready.setProperty("role", "warning")
        self.inner_box.addWidget(self.not_ready)
        self.params_host = QWidget()
        self.params_form = QFormLayout(self.params_host)
        self.params_form.setContentsMargins(0, 0, 0, 0)
        self.params_form.setRowWrapPolicy(QFormLayout.WrapLongRows)
        self.params_form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        self.inner_box.addWidget(self.params_host)
        self.inner_box.addStretch(1)
        self.scroll.setWidget(inner)
        root.addWidget(self.scroll, 1)

        self.preview = QLabel("")
        self.preview.setWordWrap(True)
        self.preview.setProperty("role", "secondary")
        root.addWidget(self.preview)

        order = QHBoxLayout()
        order_label = QLabel(S.SETTINGS_ORDER)
        order_label.setProperty("role", "secondary")
        self.up_btn = QPushButton(S.BTN_MOVE_UP)
        self.down_btn = QPushButton(S.BTN_MOVE_DOWN)
        self.up_btn.clicked.connect(lambda: self._move(-1))
        self.down_btn.clicked.connect(lambda: self._move(+1))
        order.addWidget(order_label, 1)
        order.addWidget(self.up_btn)
        order.addWidget(self.down_btn)
        root.addLayout(order)

        self.restore_btn = QPushButton(S.BTN_RESTORE_PREVIOUS)
        self.restore_btn.clicked.connect(self._restore)
        root.addWidget(self.restore_btn)

        bottom = QHBoxLayout()
        self.save_btn = QPushButton(S.BTN_SAVE)
        self.save_btn.setProperty("kind", "primary")
        self.save_btn.clicked.connect(self._save)
        self.cancel_btn = QPushButton(S.BTN_CANCEL)
        self.cancel_btn.clicked.connect(self.back_clicked)
        bottom.addStretch(1)
        bottom.addWidget(self.cancel_btn)
        bottom.addWidget(self.save_btn)
        root.addLayout(bottom)

    # ── 열기 ──────────────────────────────────────────────────────────

    def open(self, slot: Dict[str, Any], index: int, count: int, in_out_ok: Optional[bool]) -> None:
        self.slot = copy.deepcopy(slot)
        self.number = int(slot["slot"])
        self.in_out_ok = in_out_ok
        self._params_by_kind = {slot.get("kind"): dict(slot.get("params") or {})}
        self.title.setText(S.SETTINGS_TITLE.format(name=slot.get("name") or ""))
        self.name_edit.blockSignals(True)
        self.name_edit.setText(slot.get("name") or "")
        self.name_edit.blockSignals(False)
        i = self.kind_box.findData(slot.get("kind"))
        self._kind = None
        self.kind_box.blockSignals(True)
        self.kind_box.setCurrentIndex(max(0, i))
        self.kind_box.blockSignals(False)
        self._build_params(self.kind_box.currentData())
        self.set_order(index, count)
        self.restore_btn.setVisible(bool(slot.get("previous")))

    def set_order(self, index: int, count: int) -> None:
        self.up_btn.setEnabled(index > 0)
        self.down_btn.setEnabled(index < count - 1)

    # ── 설정 항목 ─────────────────────────────────────────────────────

    def _kind_changed(self, _i: int) -> None:
        old_kind = self._kind
        if old_kind is not None:
            self._params_by_kind[old_kind] = self.values()
        new_kind = self.kind_box.currentData()
        # 이름이 예전 할 일의 기본 이름 그대로면 새 할 일 이름으로 바꿔 준다
        if self.name_edit.text().strip() in ("", kind_name(old_kind)):
            self.name_edit.setText(kind_name(new_kind))
        self._build_params(new_kind)

    def _build_params(self, key: str) -> None:
        while self.params_form.rowCount():
            self.params_form.removeRow(0)
        self.fields = {}
        self._kind = key
        k = get_kind(key)
        self.not_ready.setVisible(not (k and k.ready))
        if k is None:
            self._update_preview()
            return
        current = k.normalize(self._params_by_kind.get(key))
        for p in k.params:
            widget = self._field(p, current.get(p.key))
            label = S.PARAM_LABELS.get(p.key, p.key)
            help_text = S.PARAM_HELP.get(p.key)
            if help_text:
                widget.setToolTip(help_text)
            if p.type == "bool":
                self.params_form.addRow(widget)
            else:
                self.params_form.addRow(label, widget)
            if help_text:
                note = QLabel(help_text)
                note.setWordWrap(True)
                note.setProperty("role", "secondary")
                self.params_form.addRow(note)
            self.fields[p.key] = widget
        self._update_preview()

    def _field(self, p: Param, value: Any) -> QWidget:
        unit = S.PARAM_UNITS.get(p.key, "")
        if p.type == "float":
            w = QDoubleSpinBox()
            w.setDecimals(_decimals(p.step))
            w.setRange(p.lo if p.lo is not None else -1e6, p.hi if p.hi is not None else 1e6)
            w.setSingleStep(p.step or 0.1)
            w.setSuffix(unit)
            w.setValue(float(value))
            w.valueChanged.connect(self._update_preview)
            return w
        if p.type == "int":
            w = QSpinBox()
            w.setRange(int(p.lo if p.lo is not None else 0), int(p.hi if p.hi is not None else 10 ** 6))
            w.setSingleStep(int(p.step or 1))
            w.setSuffix(unit)
            w.setValue(int(value))
            w.valueChanged.connect(self._update_preview)
            return w
        if p.type == "bool":
            w = QCheckBox(S.PARAM_LABELS.get(p.key, p.key))
            w.setChecked(bool(value))
            w.toggled.connect(self._update_preview)
            return w
        if p.type == "color":
            w = QComboBox()
            for c in MARKER_COLORS:
                w.addItem(fmt.color_word(c), c)
            w.setCurrentIndex(max(0, w.findData(value)))
            w.currentIndexChanged.connect(self._update_preview)
            return w
        if p.type == "choice":
            w = QComboBox()
            labels = S.CHOICE_LABELS.get(p.key, {})
            for c in p.choices:
                text = labels.get(c, c)
                locked = p.requires_cap == "in_out" and c == "in_out" and self.in_out_ok is not True
                if locked:
                    text = f"{text} ({S.IN_OUT_LOCKED})"
                w.addItem(text, c)
                if locked and value != c:
                    item = w.model().item(w.count() - 1)
                    if item is not None:
                        item.setEnabled(False)
                        item.setToolTip(S.TIP_IN_OUT_LOCKED)
            w.setCurrentIndex(max(0, w.findData(value)))
            w.currentIndexChanged.connect(self._update_preview)
            return w
        w = QLineEdit()
        w.setMaxLength(p.max_len)
        w.setText(str(value or ""))
        w.textChanged.connect(self._update_preview)
        return w

    def set_value(self, key: str, value: Any) -> None:
        """시험과 대화(2.1c)에서: 항목 하나를 바꾼다."""
        w = self.fields[key]
        if isinstance(w, (QDoubleSpinBox, QSpinBox)):
            w.setValue(value)
        elif isinstance(w, QCheckBox):
            w.setChecked(bool(value))
        elif isinstance(w, QComboBox):
            w.setCurrentIndex(max(0, w.findData(value)))
        elif isinstance(w, QLineEdit):
            w.setText(str(value))

    def values(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {}
        for key, w in self.fields.items():
            if isinstance(w, QDoubleSpinBox):
                out[key] = round(w.value(), 6)
            elif isinstance(w, QSpinBox):
                out[key] = int(w.value())
            elif isinstance(w, QCheckBox):
                out[key] = w.isChecked()
            elif isinstance(w, QComboBox):
                out[key] = w.currentData()
            elif isinstance(w, QLineEdit):
                out[key] = w.text()
        k = get_kind(self._kind)
        return k.normalize(out) if k is not None else out

    def current(self) -> Dict[str, Any]:
        return {"slot": self.number, "kind": self._kind, "name": self.name_edit.text().strip() or kind_name(self._kind),
                "params": self.values()}

    def _update_preview(self, *_args) -> None:
        self.preview.setText(S.SETTINGS_PREVIEW.format(summary=slot_summary(self.current())))

    # ── 단추 ──────────────────────────────────────────────────────────

    def _save(self) -> None:
        if self.number is None or self._kind is None:
            return
        c = self.current()
        self.saved.emit(self.number, c["kind"], c["name"], c["params"])

    def _move(self, delta: int) -> None:
        if self.number is not None:
            self.move_requested.emit(self.number, delta)

    def _restore(self) -> None:
        if self.number is not None:
            self.restore_requested.emit(self.number)
