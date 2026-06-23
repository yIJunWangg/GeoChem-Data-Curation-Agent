"""Memory/Rules page with mapping rules and learned extraction rules."""

from __future__ import annotations

from typing import Any, Callable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSplitter,
    QTabWidget,
    QTableView,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from .app_widgets import HeaderPage, StatusBadge
from .viewmodels import DataFrameTableModel


class MemoryPage(HeaderPage):
    """Rule memory browser with mapping rules and learned extraction rules."""

    def __init__(self):
        self.toggle_rule_requested: Callable[..., None] | None = None
        self.filter_changed: Callable[[], None] | None = None

        self._mapping_model = DataFrameTableModel(columns=[
            "rule_id", "source_field", "target_field", "source_unit",
            "target_unit", "mapping_type", "formula", "review_status", "scope",
        ])
        self._learned_model = DataFrameTableModel(columns=[
            "rule_id", "target_field", "target_header", "rule_type",
            "confidence", "risk_level", "review_status", "scope",
        ])
        self._current_rules: list[dict[str, Any]] = []
        self._current: dict[str, Any] | None = None
        self._current_tab = 0

        super().__init__("规则记忆", "查看和管理已确认的映射规则与学习到的抽取规则。")
        self._build_body()

    def _build_body(self) -> None:
        toolbar = QHBoxLayout()
        toolbar.setSpacing(10)

        self.status_filter = QComboBox()
        self.status_filter.addItems(["全部状态", "confirmed", "pending", "rejected"])
        self.status_filter.currentIndexChanged.connect(self._on_filter_changed)

        self.scope_filter = QComboBox()
        self.scope_filter.addItems(["全部范围", "project", "article", "global"])
        self.scope_filter.currentIndexChanged.connect(self._on_filter_changed)

        toolbar.addWidget(QLabel("状态:"))
        toolbar.addWidget(self.status_filter)
        toolbar.addWidget(QLabel("范围:"))
        toolbar.addWidget(self.scope_filter)
        toolbar.addStretch(1)
        self._content_layout.addLayout(toolbar)

        self._tabs = QTabWidget()
        mapping_tab = QWidget()
        mapping_layout = QVBoxLayout(mapping_tab)
        mapping_layout.setContentsMargins(0, 0, 0, 0)
        self._mapping_table = QTableView()
        self._mapping_table.setAlternatingRowColors(True)
        self._mapping_table.setModel(self._mapping_model)
        self._mapping_table.horizontalHeader().setStretchLastSection(True)
        self._mapping_table.selectionModel().selectionChanged.connect(self._on_mapping_selected) if self._mapping_table.selectionModel() else None
        mapping_layout.addWidget(self._mapping_table)
        self._tabs.addTab(mapping_tab, "映射规则")

        learned_tab = QWidget()
        learned_layout = QVBoxLayout(learned_tab)
        learned_layout.setContentsMargins(0, 0, 0, 0)
        self._learned_table = QTableView()
        self._learned_table.setAlternatingRowColors(True)
        self._learned_table.setModel(self._learned_model)
        self._learned_table.horizontalHeader().setStretchLastSection(True)
        self._learned_table.selectionModel().selectionChanged.connect(self._on_learned_selected) if self._learned_table.selectionModel() else None
        learned_layout.addWidget(self._learned_table)
        self._tabs.addTab(learned_tab, "学习规则")

        self._tabs.currentChanged.connect(self._on_tab_changed)
        self._content_layout.addWidget(self._tabs, 2)

        detail_frame = QFrame()
        detail_frame.setProperty("class", "card")
        detail_layout = QVBoxLayout(detail_frame)
        detail_layout.setContentsMargins(14, 14, 14, 14)

        detail_title = QLabel("规则详情")
        detail_title.setObjectName("sectionTitle")
        detail_layout.addWidget(detail_title)

        self._detail_text = QTextEdit()
        self._detail_text.setReadOnly(True)
        self._detail_text.setPlaceholderText("选择上方规则查看详情...")
        self._detail_text.setMaximumHeight(200)
        detail_layout.addWidget(self._detail_text)

        btn_row = QHBoxLayout()
        self._enable_btn = QPushButton("启用规则")
        self._enable_btn.setObjectName("primaryButton")
        self._enable_btn.clicked.connect(lambda: self._do_toggle(True))
        self._enable_btn.setEnabled(False)
        self._disable_btn = QPushButton("禁用规则")
        self._disable_btn.setObjectName("dangerButton")
        self._disable_btn.clicked.connect(lambda: self._do_toggle(False))
        self._disable_btn.setEnabled(False)
        btn_row.addWidget(self._enable_btn)
        btn_row.addWidget(self._disable_btn)
        btn_row.addStretch(1)
        detail_layout.addLayout(btn_row)

        self._content_layout.addWidget(detail_frame, 1)

    def _on_tab_changed(self, index: int) -> None:
        self._current_tab = index
        self._current = None
        self._detail_text.clear()
        self._enable_btn.setEnabled(False)
        self._disable_btn.setEnabled(False)
        if self.filter_changed:
            self.filter_changed()

    def _on_filter_changed(self) -> None:
        if self.filter_changed:
            self.filter_changed()

    def get_status_filter(self) -> str:
        text = self.status_filter.currentText()
        return text if text != "全部状态" else "all"

    def get_scope_filter(self) -> str:
        text = self.scope_filter.currentText()
        return text if text != "全部范围" else "all"

    def get_current_tab(self) -> int:
        return self._current_tab

    def set_mapping_rules(self, rules: list[dict[str, Any]]) -> None:
        self._current_rules = rules
        display = []
        for r in rules:
            display.append({
                "rule_id": r.get("rule_id", ""),
                "source_field": r.get("source_field", ""),
                "target_field": r.get("target_field", ""),
                "source_unit": r.get("source_unit", ""),
                "target_unit": r.get("target_unit", ""),
                "mapping_type": r.get("mapping_type", ""),
                "formula": r.get("formula", ""),
                "review_status": r.get("review_status", ""),
                "scope": r.get("scope", ""),
            })
        self._mapping_model.set_rows(display)
        self._mapping_table.resizeColumnsToContents()

    def set_learned_rules(self, rules: list[dict[str, Any]]) -> None:
        self._current_rules = rules
        display = []
        for r in rules:
            display.append({
                "rule_id": r.get("rule_id", ""),
                "target_field": r.get("target_field", ""),
                "target_header": r.get("target_header", ""),
                "rule_type": r.get("rule_type", ""),
                "confidence": f"{r.get('confidence', 0):.2f}",
                "risk_level": r.get("risk_level", ""),
                "review_status": r.get("review_status", ""),
                "scope": r.get("scope", ""),
            })
        self._learned_model.set_rows(display)
        self._learned_table.resizeColumnsToContents()

    def _on_mapping_selected(self) -> None:
        selected = self._mapping_table.selectionModel().selectedRows()
        if not selected:
            self._clear_detail()
            return
        idx = selected[0].row()
        if idx < len(self._current_rules):
            self._show_detail(self._current_rules[idx])

    def _on_learned_selected(self) -> None:
        selected = self._learned_table.selectionModel().selectedRows()
        if not selected:
            self._clear_detail()
            return
        idx = selected[0].row()
        if idx < len(self._current_rules):
            self._show_detail(self._current_rules[idx])

    def _show_detail(self, rule: dict[str, Any]) -> None:
        self._current = rule
        lines = [f"{k}: {v}" for k, v in rule.items() if v is not None and v != ""]
        self._detail_text.setText("\n".join(lines))

        status = (rule.get("review_status") or "").lower()
        self._enable_btn.setEnabled(status != "confirmed")
        self._disable_btn.setEnabled(status != "rejected")

    def _clear_detail(self) -> None:
        self._current = None
        self._detail_text.clear()
        self._enable_btn.setEnabled(False)
        self._disable_btn.setEnabled(False)

    def _do_toggle(self, enabled: bool) -> None:
        if not self._current or not self.toggle_rule_requested:
            return
        rule_id = self._current.get("rule_id")
        if not rule_id:
            return
        rule_type = "mapping" if self._current_tab == 0 else "learned"
        self.toggle_rule_requested(rule_id, rule_type, enabled)
