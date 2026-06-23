"""Human review queue page with accept/reject/edit/defer actions."""

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

from .app_widgets import HeaderPage, RiskBadge, StatusBadge
from .viewmodels import DataFrameTableModel


class EditReviewDialog(QDialog):
    """Dialog for editing a review item's target field/unit."""

    def __init__(self, item: dict[str, Any], parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("编辑审核项")
        self.setMinimumWidth(400)
        layout = QFormLayout(self)

        self.target_field = QLineEdit(item.get("ai_suggestion", ""))
        self.target_unit = QLineEdit(item.get("original_unit", ""))
        self.formula = QLineEdit()
        self.save_as_rule = QCheckBox("保存为长期映射规则")
        self.notes = QLineEdit()

        layout.addRow("目标字段:", self.target_field)
        layout.addRow("目标单位:", self.target_unit)
        layout.addRow("换算公式:", self.formula)
        layout.addRow(self.save_as_rule)
        layout.addRow("备注:", self.notes)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addRow(buttons)

    def get_values(self) -> dict[str, Any]:
        return {
            "target_field": self.target_field.text().strip(),
            "target_unit": self.target_unit.text().strip(),
            "formula": self.formula.text().strip() or None,
            "save_as_rule": self.save_as_rule.isChecked(),
            "notes": self.notes.text().strip(),
        }


class ReviewPage(HeaderPage):
    """Review queue with filtering, actions, and detail panel."""

    def __init__(self):
        self.decide_requested: Callable[..., None] | None = None
        self.filter_changed: Callable[[], None] | None = None

        self._model = DataFrameTableModel(columns=[
            "review_id", "item_type", "risk_level", "original_field",
            "original_unit", "ai_suggestion", "confidence", "status",
        ])
        self._items: list[dict[str, Any]] = []
        self._current_item: dict[str, Any] | None = None

        super().__init__("人工审核", "处理高风险字段映射、单位换算和学习规则，确认后可保存为长期规则。")
        self._build_body()

    def _build_body(self) -> None:
        filter_row = QHBoxLayout()
        filter_row.setSpacing(10)

        self.status_filter = QComboBox()
        self.status_filter.addItems([
            ("待审核"), ("已确认"), ("已拒绝"), ("已延后"), ("全部"),
        ])
        self.status_filter.setCurrentIndex(0)
        self.status_filter.currentIndexChanged.connect(self._on_filter_changed)

        self.risk_filter = QComboBox()
        self.risk_filter.addItems(["全部风险", "HIGH", "MEDIUM", "LOW"])
        self.risk_filter.currentIndexChanged.connect(self._on_filter_changed)

        self.type_filter = QComboBox()
        self.type_filter.addItems(["全部类型", "field_mapping", "learned_extraction_rule"])
        self.type_filter.currentIndexChanged.connect(self._on_filter_changed)

        for label_text, widget in [("状态:", self.status_filter), ("风险:", self.risk_filter), ("类型:", self.type_filter)]:
            lbl = QLabel(label_text)
            filter_row.addWidget(lbl)
            filter_row.addWidget(widget)
        filter_row.addStretch(1)
        self._source_summary = QLabel("来源队列: 0 项")
        self._source_summary.setStyleSheet(
            "color: #1468d8; font-weight: 650; padding: 4px 8px; "
            "border: 1px solid #b8d7ff; border-radius: 6px; background: #f0f6ff;"
        )
        filter_row.addWidget(self._source_summary)

        self._content_layout.addLayout(filter_row)

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

        detail_title = QLabel("审核详情")
        detail_title.setObjectName("sectionTitle")
        detail_layout.addWidget(detail_title)

        self._detail_fields: dict[str, QLabel] = {}
        for field_name in ["原始字段", "原始单位", "AI 建议", "置信度", "风险等级", "状态", "类型"]:
            row = QHBoxLayout()
            lbl = QLabel(f"{field_name}:")
            lbl.setStyleSheet("color: #53637a; min-width: 70px;")
            val = QLabel("—")
            val.setWordWrap(True)
            row.addWidget(lbl)
            row.addWidget(val, 1)
            detail_layout.addLayout(row)
            self._detail_fields[field_name] = val

        detail_layout.addWidget(QLabel("操作:"))
        btn_row = QHBoxLayout()
        self._accept_btn = QPushButton("接受")
        self._accept_btn.setObjectName("primaryButton")
        self._accept_btn.clicked.connect(lambda: self._do_decide("accept"))
        self._reject_btn = QPushButton("拒绝")
        self._reject_btn.setObjectName("dangerButton")
        self._reject_btn.clicked.connect(lambda: self._do_decide("reject"))
        self._edit_btn = QPushButton("编辑")
        self._edit_btn.clicked.connect(self._do_edit)
        self._defer_btn = QPushButton("延后")
        self._defer_btn.clicked.connect(lambda: self._do_decide("defer"))
        for btn in (self._accept_btn, self._reject_btn, self._edit_btn, self._defer_btn):
            btn.setEnabled(False)
            btn_row.addWidget(btn)
        detail_layout.addLayout(btn_row)

        self._save_rule_check = QCheckBox("保存为长期映射规则")
        detail_layout.addWidget(self._save_rule_check)

        self._evidence_text = QTextEdit()
        self._evidence_text.setReadOnly(True)
        self._evidence_text.setPlaceholderText("选择左侧记录查看证据和原始值...")
        self._evidence_text.setMinimumHeight(120)
        detail_layout.addWidget(QLabel("证据与原始值:"))
        detail_layout.addWidget(self._evidence_text, 1)

        split.addWidget(detail_frame)
        split.setSizes([700, 400])
        self._content_layout.addWidget(split, 1)

    def _on_filter_changed(self) -> None:
        if self.filter_changed:
            self.filter_changed()

    def get_status_filter(self) -> str:
        mapping = {"待审核": "pending", "已确认": "confirmed", "已拒绝": "rejected", "已延后": "deferred", "全部": "all"}
        return mapping.get(self.status_filter.currentText(), "pending")

    def get_risk_filter(self) -> str:
        text = self.risk_filter.currentText()
        return text if text != "全部风险" else "all"

    def get_type_filter(self) -> str:
        text = self.type_filter.currentText()
        return text if text != "全部类型" else "all"

    def set_items(self, items: list[dict[str, Any]]) -> None:
        self._items = items
        display = []
        for item in items:
            display.append({
                "review_id": item.get("review_id", ""),
                "item_type": item.get("item_type", ""),
                "risk_level": item.get("risk_level", ""),
                "original_field": item.get("original_field", ""),
                "original_unit": item.get("original_unit", ""),
                "ai_suggestion": item.get("ai_suggestion", ""),
                "confidence": f"{item.get('confidence', 0):.2f}",
                "status": item.get("status", ""),
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
        row_idx = selected[0].row()
        if row_idx < len(self._items):
            self._show_detail(self._items[row_idx])

    def _show_detail(self, item: dict[str, Any]) -> None:
        self._current_item = item
        self._detail_fields["原始字段"].setText(item.get("original_field", "—"))
        self._detail_fields["原始单位"].setText(item.get("original_unit", "—"))
        self._detail_fields["AI 建议"].setText(item.get("ai_suggestion", "—"))

        conf = item.get("confidence", 0)
        conf_label = self._detail_fields["置信度"]
        conf_label.setText(f"{conf:.2f}")
        if conf >= 0.9:
            conf_label.setStyleSheet("color: #027a48; font-weight: 650;")
        elif conf >= 0.7:
            conf_label.setStyleSheet("color: #b54708; font-weight: 650;")
        else:
            conf_label.setStyleSheet("color: #d92d20; font-weight: 650;")

        self._detail_fields["风险等级"].setText(item.get("risk_level", "—"))
        self._detail_fields["状态"].setText(item.get("status", "—"))
        self._detail_fields["类型"].setText(item.get("item_type", "—"))

        evidence_parts = []
        if item.get("original_field"):
            evidence_parts.append(f"原始字段: {item['original_field']}")
        if item.get("original_unit"):
            evidence_parts.append(f"原始单位: {item['original_unit']}")
        if item.get("original_value"):
            evidence_parts.append(f"原始值: {item['original_value']}")
        if item.get("available_actions"):
            try:
                import json
                actions = json.loads(item["available_actions"]) if isinstance(item["available_actions"], str) else item["available_actions"]
                evidence_parts.append(f"可用操作: {', '.join(actions)}")
            except Exception:
                pass
        self._evidence_text.setText("\n".join(evidence_parts) or "无证据信息")

        is_pending = item.get("status") == "pending"
        for btn in (self._accept_btn, self._reject_btn, self._edit_btn, self._defer_btn):
            btn.setEnabled(is_pending)

    def _clear_detail(self) -> None:
        self._current_item = None
        for lbl in self._detail_fields.values():
            lbl.setText("—")
            lbl.setStyleSheet("")
        self._evidence_text.clear()
        for btn in (self._accept_btn, self._reject_btn, self._edit_btn, self._defer_btn):
            btn.setEnabled(False)

    def _do_decide(self, action: str) -> None:
        if not self._current_item or not self.decide_requested:
            return
        self.decide_requested(
            self._current_item["review_id"],
            action,
            save_as_rule=self._save_rule_check.isChecked(),
        )

    def _do_edit(self) -> None:
        if not self._current_item:
            return
        dlg = EditReviewDialog(self._current_item, self)
        if dlg.exec() == QDialog.Accepted and self.decide_requested:
            values = dlg.get_values()
            self.decide_requested(
                self._current_item["review_id"],
                "edit",
                target_field=values["target_field"],
                target_unit=values["target_unit"],
                formula=values["formula"],
                save_as_rule=values["save_as_rule"] or self._save_rule_check.isChecked(),
                notes=values["notes"],
            )
