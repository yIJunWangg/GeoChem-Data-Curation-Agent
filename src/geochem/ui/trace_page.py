"""Trace/provenance page with row-level drill-down tabs."""

from __future__ import annotations

from typing import Any, Callable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QSplitter,
    QTabWidget,
    QTableView,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from .app_widgets import HeaderPage, StatusBadge
from .viewmodels import DataFrameTableModel


class TracePage(HeaderPage):
    """Row-level data provenance drill-down with 6 tabs."""

    def __init__(self):
        self.filter_changed: Callable[[], None] | None = None
        self.record_selected: Callable[[str], None] | None = None

        self._row_model = DataFrameTableModel(columns=[
            "record_id", "source_row", "quality_grade", "table_id",
        ])
        self._mapping_model = DataFrameTableModel(columns=[
            "source_field", "target_field", "mapping_type", "confidence", "risk_level",
        ])
        self._patch_model = DataFrameTableModel(columns=[
            "patch_id", "target_field", "value", "source_type", "confidence", "reason",
        ])
        self._review_model = DataFrameTableModel(columns=[
            "review_id", "original_field", "ai_suggestion", "risk_level", "status",
        ])
        self._calc_model = DataFrameTableModel(columns=[
            "calc_id", "source_field", "source_value", "source_unit", "target_unit", "formula", "result",
        ])
        self._llm_model = DataFrameTableModel(columns=[
            "call_id", "agent_name", "skill_name", "model_name", "total_tokens", "estimated_cost", "status",
        ])

        self._trace_rows: list[dict[str, Any]] = []
        self._current_record: dict[str, Any] | None = None

        super().__init__("数据溯源查看", "行级追溯数据来源、映射过程、补值、审核记录、计算档案和 LLM 调用。")
        self._build_body()

    def _build_body(self) -> None:
        toolbar = QHBoxLayout()
        toolbar.setSpacing(10)

        toolbar.addWidget(QLabel("候选表:"))
        self.table_selector = QComboBox()
        self.table_selector.setMinimumWidth(200)
        self.table_selector.currentIndexChanged.connect(self._on_filter_changed)
        toolbar.addWidget(self.table_selector)
        toolbar.addStretch(1)
        self._content_layout.addLayout(toolbar)

        split = QSplitter(Qt.Horizontal)

        row_frame = QFrame()
        row_frame.setProperty("class", "card")
        row_layout = QVBoxLayout(row_frame)
        row_layout.setContentsMargins(0, 0, 0, 0)
        row_title = QLabel("标准化记录列表")
        row_title.setObjectName("sectionTitle")
        row_title.setStyleSheet("padding: 8px 12px;")
        row_layout.addWidget(row_title)
        self._row_table = QTableView()
        self._row_table.setAlternatingRowColors(True)
        self._row_table.setModel(self._row_model)
        self._row_table.horizontalHeader().setStretchLastSection(True)
        self._row_table.selectionModel().selectionChanged.connect(self._on_row_selected) if self._row_table.selectionModel() else None
        row_layout.addWidget(self._row_table, 1)
        split.addWidget(row_frame)

        detail_frame = QFrame()
        detail_frame.setProperty("class", "card")
        detail_layout = QVBoxLayout(detail_frame)
        detail_layout.setContentsMargins(14, 14, 14, 14)

        self._record_header = QLabel("选择左侧记录查看详情")
        self._record_header.setObjectName("sectionTitle")
        detail_layout.addWidget(self._record_header)

        self._quality_badge = StatusBadge("")
        detail_layout.addWidget(self._quality_badge)

        self._tabs = QTabWidget()

        source_tab = QWidget()
        source_layout = QVBoxLayout(source_tab)
        source_layout.setContentsMargins(0, 0, 0, 0)
        self._source_text = QTextEdit()
        self._source_text.setReadOnly(True)
        source_layout.addWidget(self._source_text)
        self._tabs.addTab(source_tab, "来源")

        mapping_tab = QWidget()
        mapping_layout = QVBoxLayout(mapping_tab)
        mapping_layout.setContentsMargins(0, 0, 0, 0)
        self._mapping_table = QTableView()
        self._mapping_table.setAlternatingRowColors(True)
        self._mapping_table.setModel(self._mapping_model)
        mapping_layout.addWidget(self._mapping_table)
        self._tabs.addTab(mapping_tab, "字段映射")

        patch_tab = QWidget()
        patch_layout = QVBoxLayout(patch_tab)
        patch_layout.setContentsMargins(0, 0, 0, 0)
        self._patch_table = QTableView()
        self._patch_table.setAlternatingRowColors(True)
        self._patch_table.setModel(self._patch_model)
        patch_layout.addWidget(self._patch_table)
        self._tabs.addTab(patch_tab, "补值 Patch")

        review_tab = QWidget()
        review_layout = QVBoxLayout(review_tab)
        review_layout.setContentsMargins(0, 0, 0, 0)
        self._review_table = QTableView()
        self._review_table.setAlternatingRowColors(True)
        self._review_table.setModel(self._review_model)
        review_layout.addWidget(self._review_table)
        self._tabs.addTab(review_tab, "审核记录")

        calc_tab = QWidget()
        calc_layout = QVBoxLayout(calc_tab)
        calc_layout.setContentsMargins(0, 0, 0, 0)
        self._calc_table = QTableView()
        self._calc_table.setAlternatingRowColors(True)
        self._calc_table.setModel(self._calc_model)
        calc_layout.addWidget(self._calc_table)
        self._tabs.addTab(calc_tab, "计算记录")

        llm_tab = QWidget()
        llm_layout = QVBoxLayout(llm_tab)
        llm_layout.setContentsMargins(0, 0, 0, 0)
        self._llm_table = QTableView()
        self._llm_table.setAlternatingRowColors(True)
        self._llm_table.setModel(self._llm_model)
        llm_layout.addWidget(self._llm_table)
        self._tabs.addTab(llm_tab, "LLM 调用")

        detail_layout.addWidget(self._tabs, 1)

        self._raw_text = QTextEdit()
        self._raw_text.setReadOnly(True)
        self._raw_text.setPlaceholderText("原始 JSON 数据...")
        self._raw_text.setMaximumHeight(150)
        detail_layout.addWidget(QLabel("原始数据:"))
        detail_layout.addWidget(self._raw_text)

        split.addWidget(detail_frame)
        split.setSizes([350, 750])
        self._content_layout.addWidget(split, 1)

    def _on_filter_changed(self) -> None:
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

    def set_trace_rows(self, rows: list[dict[str, Any]]) -> None:
        self._trace_rows = rows
        display = []
        for r in rows:
            display.append({
                "record_id": r.get("record_id", ""),
                "source_row": r.get("source_row", ""),
                "quality_grade": r.get("quality_grade", ""),
                "table_id": r.get("table_id", ""),
            })
        self._row_model.set_rows(display)
        self._row_table.resizeColumnsToContents()

    def set_record_detail(self, detail: dict[str, Any]) -> None:
        self._current_record = detail
        self._record_header.setText(
            f"记录 {detail.get('record_id', '')}  —  来源行 {detail.get('source_row', '')}"
        )
        self._quality_badge.set_status(detail.get("quality_grade", ""))

        source_parts = [
            f"记录 ID: {detail.get('record_id', '')}",
            f"表 ID: {detail.get('table_id', '')}",
            f"来源行: {detail.get('source_row', '')}",
            f"质量等级: {detail.get('quality_grade', '')}",
            f"处理时间: {detail.get('processed_at', '')}",
            f"来源文件: {detail.get('source_file', '')}",
        ]
        self._source_text.setText("\n".join(source_parts))

        data = detail.get("data_parsed", {})
        import json
        self._raw_text.setText(json.dumps(data, indent=2, ensure_ascii=False) if data else "{}")

        patches = detail.get("patches", [])
        self._patch_model.set_rows(patches)

        reviews = detail.get("reviews", [])
        self._review_model.set_rows(reviews)

        calculations = detail.get("calculations", [])
        self._calc_model.set_rows(calculations)

        self._mapping_model.set_rows([])
        self._llm_model.set_rows([])

    def _on_row_selected(self) -> None:
        selected = self._row_table.selectionModel().selectedRows()
        if not selected:
            return
        idx = selected[0].row()
        if idx < len(self._trace_rows):
            record_id = self._trace_rows[idx].get("record_id")
            if record_id and self.record_selected:
                self.record_selected(record_id)
