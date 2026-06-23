"""Field mapping workbench with confidence, risk, and interactive controls."""

from __future__ import annotations

from typing import Any, Callable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSplitter,
    QTableView,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from .app_widgets import HeaderPage, RiskBadge
from .viewmodels import DataFrameTableModel


class EditMappingDialog(QDialog):
    def __init__(self, mapping: dict[str, Any], parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("编辑映射")
        self.setMinimumWidth(380)
        layout = QFormLayout(self)

        self.target_field = QLineEdit(mapping.get("target_field", ""))
        self.target_unit = QLineEdit(mapping.get("target_unit", ""))
        self.save_as_rule = QCheckBox("保存为长期映射规则")

        layout.addRow("目标字段:", self.target_field)
        layout.addRow("目标单位:", self.target_unit)
        layout.addRow(self.save_as_rule)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addRow(buttons)

    def get_values(self) -> dict[str, Any]:
        return {
            "target_field": self.target_field.text().strip(),
            "target_unit": self.target_unit.text().strip(),
            "save_as_rule": self.save_as_rule.isChecked(),
        }


class MappingPage(HeaderPage):
    """Field mapping workbench with table selector, actions, and detail panel."""

    def __init__(self):
        self.run_mapping_requested: Callable[..., None] | None = None
        self.confirm_mapping_requested: Callable[..., None] | None = None
        self.reject_mapping_requested: Callable[..., None] | None = None
        self.filter_changed: Callable[[], None] | None = None

        self._model = DataFrameTableModel(columns=[
            "mapping_id", "source_field", "target_field", "source_unit",
            "target_unit", "mapping_type", "confidence", "risk_level",
            "requires_review", "reason",
        ])
        self._mappings: list[dict[str, Any]] = []
        self._current: dict[str, Any] | None = None

        super().__init__("字段映射与单位换算", "审查原始字段到目标 schema 的映射、单位、风险和置信度，确认或修改后进入标准化。")
        self._build_body()

    def _build_body(self) -> None:
        toolbar = QHBoxLayout()
        toolbar.setSpacing(10)

        toolbar.addWidget(QLabel("候选表:"))
        self.table_selector = QComboBox()
        self.table_selector.setMinimumWidth(200)
        self.table_selector.currentIndexChanged.connect(self._on_table_changed)
        toolbar.addWidget(self.table_selector)

        self._run_btn = QPushButton("运行映射")
        self._run_btn.setObjectName("primaryButton")
        self._run_btn.clicked.connect(self._run_mapping)
        toolbar.addWidget(self._run_btn)

        self._grouped_check = QCheckBox("分组 LLM 补缺")
        toolbar.addWidget(self._grouped_check)

        self._batch_btn = QPushButton("批量确认低风险")
        self._batch_btn.clicked.connect(self._batch_confirm_low_risk)
        toolbar.addWidget(self._batch_btn)

        toolbar.addStretch(1)
        self._source_summary = QLabel("来源队列: 0 项")
        self._source_summary.setStyleSheet(
            "color: #1468d8; font-weight: 650; padding: 4px 8px; "
            "border: 1px solid #b8d7ff; border-radius: 6px; background: #f0f6ff;"
        )
        toolbar.addWidget(self._source_summary)
        self._content_layout.addLayout(toolbar)

        split = QSplitter(Qt.Horizontal)

        table_frame = QFrame()
        table_frame.setProperty("class", "card")
        table_layout = QVBoxLayout(table_frame)
        table_layout.setContentsMargins(0, 0, 0, 0)
        self._table = QTableView()
        self._table.setAlternatingRowColors(True)
        self._table.setModel(self._model)
        self._table.horizontalHeader().setStretchLastSection(True)
        self._table.selectionModel().selectionChanged.connect(self._on_selection_changed) if self._table.selectionModel() else None
        table_layout.addWidget(self._table)
        split.addWidget(table_frame)

        detail_frame = QFrame()
        detail_frame.setProperty("class", "card")
        detail_layout = QVBoxLayout(detail_frame)
        detail_layout.setContentsMargins(14, 14, 14, 14)
        detail_layout.setSpacing(10)

        detail_title = QLabel("映射详情")
        detail_title.setObjectName("sectionTitle")
        detail_layout.addWidget(detail_title)

        self._detail_fields: dict[str, QLabel] = {}
        for field_name in ["源字段", "目标字段", "源单位", "目标单位", "映射类型", "置信度", "风险等级", "需要审核"]:
            row = QHBoxLayout()
            lbl = QLabel(f"{field_name}:")
            lbl.setStyleSheet("color: #53637a; min-width: 70px;")
            val = QLabel("—")
            val.setWordWrap(True)
            row.addWidget(lbl)
            row.addWidget(val, 1)
            detail_layout.addLayout(row)
            self._detail_fields[field_name] = val

        self._reason_text = QTextEdit()
        self._reason_text.setReadOnly(True)
        self._reason_text.setPlaceholderText("选择左侧映射查看原因...")
        self._reason_text.setMaximumHeight(100)
        detail_layout.addWidget(QLabel("映射原因:"))
        detail_layout.addWidget(self._reason_text)

        btn_row = QHBoxLayout()
        self._confirm_btn = QPushButton("确认映射")
        self._confirm_btn.setObjectName("primaryButton")
        self._confirm_btn.clicked.connect(self._do_confirm)
        self._reject_btn = QPushButton("拒绝映射")
        self._reject_btn.setObjectName("dangerButton")
        self._reject_btn.clicked.connect(self._do_reject)
        self._edit_btn = QPushButton("编辑映射")
        self._edit_btn.clicked.connect(self._do_edit)
        for btn in (self._confirm_btn, self._reject_btn, self._edit_btn):
            btn.setEnabled(False)
            btn_row.addWidget(btn)
        detail_layout.addLayout(btn_row)
        detail_layout.addStretch(1)

        split.addWidget(detail_frame)
        split.setSizes([750, 380])
        self._content_layout.addWidget(split, 1)

    def _on_table_changed(self) -> None:
        if self.filter_changed:
            self.filter_changed()

    def get_selected_table_id(self) -> str | None:
        data = self.table_selector.currentData()
        return data if data else None

    def set_table_options(self, tables: list[dict[str, Any]]) -> None:
        self.table_selector.blockSignals(True)
        self.table_selector.clear()
        for t in tables:
            label = f"{t['table_id']}  {t.get('sheet_name') or ''}"
            self.table_selector.addItem(label, t["table_id"])
        self.table_selector.blockSignals(False)

    def set_mappings(self, mappings: list[dict[str, Any]]) -> None:
        self._mappings = mappings
        display = []
        for m in mappings:
            display.append({
                "mapping_id": m.get("mapping_id", ""),
                "source_field": m.get("source_field", ""),
                "target_field": m.get("target_field", ""),
                "source_unit": m.get("source_unit", ""),
                "target_unit": m.get("target_unit", ""),
                "mapping_type": m.get("mapping_type", ""),
                "confidence": f"{m.get('confidence', 0):.2f}",
                "risk_level": m.get("risk_level", ""),
                "requires_review": "是" if m.get("requires_review") else "否",
                "reason": m.get("reason", ""),
            })
        self._model.set_rows(display)
        self._table.resizeColumnsToContents()
        self._clear_detail()

    def set_source_summary(self, summary: dict[str, Any]) -> None:
        self._source_summary.setText(
            "来源队列: "
            f"{summary.get('total', 0)} 项 / "
            f"{summary.get('tables', 0)} 表格, "
            f"{summary.get('figures', 0)} 图表, "
            f"{summary.get('texts', 0)} 段落"
        )

    def _on_selection_changed(self) -> None:
        selected = self._table.selectionModel().selectedRows()
        if not selected:
            self._clear_detail()
            return
        idx = selected[0].row()
        if idx < len(self._mappings):
            self._show_detail(self._mappings[idx])

    def _show_detail(self, m: dict[str, Any]) -> None:
        self._current = m
        self._detail_fields["源字段"].setText(m.get("source_field", "—"))
        self._detail_fields["目标字段"].setText(m.get("target_field", "—"))
        self._detail_fields["源单位"].setText(m.get("source_unit", "—"))
        self._detail_fields["目标单位"].setText(m.get("target_unit", "—"))
        self._detail_fields["映射类型"].setText(m.get("mapping_type", "—"))

        conf = m.get("confidence", 0)
        conf_label = self._detail_fields["置信度"]
        conf_label.setText(f"{conf:.2f}")
        if conf >= 0.9:
            conf_label.setStyleSheet("color: #027a48; font-weight: 650;")
        elif conf >= 0.7:
            conf_label.setStyleSheet("color: #b54708; font-weight: 650;")
        else:
            conf_label.setStyleSheet("color: #d92d20; font-weight: 650;")

        self._detail_fields["风险等级"].setText(m.get("risk_level", "—"))
        self._detail_fields["需要审核"].setText("是" if m.get("requires_review") else "否")
        self._reason_text.setText(m.get("reason", "无原因信息"))

        needs_review = bool(m.get("requires_review"))
        self._confirm_btn.setEnabled(needs_review)
        self._reject_btn.setEnabled(True)
        self._edit_btn.setEnabled(True)

    def _clear_detail(self) -> None:
        self._current = None
        for lbl in self._detail_fields.values():
            lbl.setText("—")
            lbl.setStyleSheet("")
        self._reason_text.clear()
        for btn in (self._confirm_btn, self._reject_btn, self._edit_btn):
            btn.setEnabled(False)

    def _run_mapping(self) -> None:
        if self.run_mapping_requested:
            table_id = self.get_selected_table_id()
            if table_id:
                self.run_mapping_requested(table_id, self._grouped_check.isChecked())

    def _batch_confirm_low_risk(self) -> None:
        if not self.confirm_mapping_requested:
            return
        for m in self._mappings:
            if m.get("risk_level") == "low" and m.get("requires_review"):
                self.confirm_mapping_requested(m["mapping_id"])

    def _do_confirm(self) -> None:
        if self._current and self.confirm_mapping_requested:
            self.confirm_mapping_requested(self._current["mapping_id"])

    def _do_reject(self) -> None:
        if self._current and self.reject_mapping_requested:
            self.reject_mapping_requested(self._current["mapping_id"])

    def _do_edit(self) -> None:
        if not self._current:
            return
        dlg = EditMappingDialog(self._current, self)
        if dlg.exec() == QDialog.Accepted and self.confirm_mapping_requested:
            values = dlg.get_values()
            self.confirm_mapping_requested(
                self._current["mapping_id"],
                target_field=values["target_field"],
                target_unit=values["target_unit"],
                save_as_rule=values["save_as_rule"],
            )
