"""Standardized data page with export and quality filtering."""

from __future__ import annotations

from html import escape
from pathlib import Path
from typing import Any, Callable

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSplitter,
    QStackedWidget,
    QTableView,
    QTextBrowser,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from .app_widgets import HeaderPage, MetricCard
from .viewmodels import DataFrameTableModel


class StandardizedPage(HeaderPage):
    """Standardized records with quality filtering and export."""

    def __init__(self):
        self.export_requested: Callable[[str], None] | None = None
        self.standardize_requested: Callable[[], None] | None = None
        self.trace_requested: Callable[[str], None] | None = None
        self.record_context_requested: Callable[[str], dict[str, Any]] | None = None
        self.filter_changed: Callable[[], None] | None = None

        self._model = DataFrameTableModel()
        self._rows: list[dict[str, Any]] = []
        self._current: dict[str, Any] | None = None

        super().__init__("标准化导出", "查看按用户表头生成的标准化记录，按质量等级筛选并导出。")
        self._build_body()

    def _build_body(self) -> None:
        cards = QHBoxLayout()
        self._grade_cards = {
            "A": MetricCard("A 级(直接匹配)", "0", hint="无需换算", accent="#027a48"),
            "B": MetricCard("B 级(别名映射)", "0", hint="已确认", accent="#1468d8"),
            "C": MetricCard("C 级(换算)", "0", hint="已归档", accent="#b54708"),
            "D": MetricCard("D 级(待确认)", "0", hint="不建议入库", accent="#f79009"),
        }
        for card in self._grade_cards.values():
            cards.addWidget(card)
        self._content_layout.addLayout(cards)

        toolbar = QHBoxLayout()
        toolbar.setSpacing(10)

        toolbar.addWidget(QLabel("候选表:"))
        self.table_selector = QComboBox()
        self.table_selector.setMinimumWidth(200)
        self.table_selector.currentIndexChanged.connect(self._on_filter_changed)
        toolbar.addWidget(self.table_selector)

        toolbar.addWidget(QLabel("质量等级:"))
        self.grade_filter = QComboBox()
        self.grade_filter.addItems(["全部等级", "A", "B", "C", "D", "E"])
        self.grade_filter.currentIndexChanged.connect(self._on_filter_changed)
        toolbar.addWidget(self.grade_filter)

        self._std_btn = QPushButton("运行标准化")
        self._std_btn.setObjectName("primaryButton")
        self._std_btn.clicked.connect(lambda: self.standardize_requested and self.standardize_requested())
        toolbar.addWidget(self._std_btn)

        toolbar.addStretch(1)

        self._export_csv_btn = QPushButton("导出 CSV")
        self._export_csv_btn.clicked.connect(lambda: self.export_requested and self.export_requested("csv"))
        self._export_xlsx_btn = QPushButton("导出 XLSX")
        self._export_xlsx_btn.clicked.connect(lambda: self.export_requested and self.export_requested("xlsx"))
        self._export_audit_btn = QPushButton("导出审计包")
        self._export_audit_btn.setObjectName("primaryButton")
        self._export_audit_btn.clicked.connect(lambda: self.export_requested and self.export_requested("audit"))
        toolbar.addWidget(self._export_csv_btn)
        toolbar.addWidget(self._export_xlsx_btn)
        toolbar.addWidget(self._export_audit_btn)

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
        self._table.doubleClicked.connect(self._on_double_click)
        self._table.selectionModel().selectionChanged.connect(self._on_selection_changed) if self._table.selectionModel() else None
        table_layout.addWidget(self._table)
        split.addWidget(table_frame)

        detail_frame = QFrame()
        detail_frame.setProperty("class", "card")
        detail_layout = QVBoxLayout(detail_frame)
        detail_layout.setContentsMargins(14, 14, 14, 14)

        detail_title = QLabel("记录详情与来源定位")
        detail_title.setObjectName("sectionTitle")
        detail_layout.addWidget(detail_title)

        self._source_stack = QStackedWidget()
        self._source_text = QTextBrowser()
        self._source_text.setOpenExternalLinks(True)
        self._source_text.setStyleSheet("QTextBrowser { background:#ffffff; border:0; padding:10px; }")
        self._source_image = QLabel("选择左侧标准化记录后，这里显示来源 PDF 页面或证据信息。")
        self._source_image.setAlignment(Qt.AlignCenter)
        self._source_image.setWordWrap(True)
        self._source_image.setStyleSheet("background:#ffffff; color:#53637a; border:1px solid #dfe5ee; border-radius:6px;")
        self._source_stack.addWidget(self._source_text)
        self._source_stack.addWidget(self._source_image)
        detail_layout.addWidget(self._source_stack, 2)

        self._detail_text = QTextEdit()
        self._detail_text.setReadOnly(True)
        self._detail_text.setPlaceholderText("选择左侧记录查看详情，双击跳转溯源...")
        self._detail_text.setMaximumHeight(190)
        detail_layout.addWidget(self._detail_text)

        trace_btn = QPushButton("查看溯源")
        trace_btn.clicked.connect(self._do_trace)
        trace_btn.setEnabled(False)
        self._trace_btn = trace_btn
        detail_layout.addWidget(trace_btn)

        split.addWidget(detail_frame)
        split.setSizes([800, 350])
        self._content_layout.addWidget(split, 1)

    def _on_filter_changed(self) -> None:
        if self.filter_changed:
            self.filter_changed()

    def get_selected_table_id(self) -> str | None:
        data = self.table_selector.currentData()
        return data if data else None

    def get_grade_filter(self) -> str:
        text = self.grade_filter.currentText()
        return text if text != "全部等级" else "all"

    def set_table_options(self, tables: list[dict[str, Any]]) -> None:
        self.table_selector.blockSignals(True)
        self.table_selector.clear()
        for t in tables:
            label = f"{t['table_id']}  {t.get('sheet_name') or ''}"
            self.table_selector.addItem(label, t["table_id"])
        self.table_selector.blockSignals(False)

    def set_records(self, rows: list[dict[str, Any]], columns: list[str]) -> None:
        self._rows = rows
        self._model.set_rows(rows, columns)
        self._table.resizeColumnsToContents()

    def set_grade_distribution(self, dist: dict[str, int]) -> None:
        for grade, card in self._grade_cards.items():
            card.set_value(dist.get(grade, 0))

    def _on_selection_changed(self) -> None:
        selected = self._table.selectionModel().selectedRows()
        if not selected:
            self._current = None
            self._detail_text.clear()
            self._trace_btn.setEnabled(False)
            return
        idx = selected[0].row()
        if idx < len(self._rows):
            self._current = self._rows[idx]
            lines = [f"{k}: {v}" for k, v in self._current.items() if v is not None and v != ""]
            self._detail_text.setText("\n".join(lines))
            self._trace_btn.setEnabled(True)
            self._load_source_context()

    def _on_double_click(self, index) -> None:
        if index.isValid() and index.row() < len(self._rows):
            record_id = self._rows[index.row()].get("Record_ID")
            if record_id and self.trace_requested:
                self.trace_requested(record_id)

    def _do_trace(self) -> None:
        if self._current and self.trace_requested:
            record_id = self._current.get("Record_ID")
            if record_id:
                self.trace_requested(record_id)

    def _load_source_context(self) -> None:
        record_id = self._current.get("Record_ID") if self._current else ""
        if not record_id or not self.record_context_requested:
            self._show_context_text({})
            return
        try:
            context = self.record_context_requested(record_id)
        except Exception as exc:
            context = {"error": str(exc)}
        preview = context.get("page_preview_path") or ""
        if preview and Path(preview).exists():
            pixmap = QPixmap(preview)
            if not pixmap.isNull():
                self._source_image.setText("")
                self._source_image.setPixmap(pixmap.scaled(760, 620, Qt.KeepAspectRatio, Qt.SmoothTransformation))
                self._source_image.setToolTip(self._context_title(context))
                self._source_stack.setCurrentWidget(self._source_image)
                return
        self._show_context_text(context)

    def _show_context_text(self, context: dict[str, Any]) -> None:
        if not context:
            self._source_text.setHtml("<span style='color:#53637a;'>暂无来源上下文。</span>")
            self._source_stack.setCurrentWidget(self._source_text)
            return
        if context.get("error"):
            self._source_text.setHtml(f"<b>来源加载失败</b><br>{escape(str(context['error']))}")
            self._source_stack.setCurrentWidget(self._source_text)
            return
        candidate_row = context.get("candidate_row") or {}
        row_html = ""
        if candidate_row:
            row_html = "<h4>原始候选行</h4>" + "".join(
                f"<div><b>{escape(str(k))}</b>: {escape(str(v))}</div>"
                for k, v in list(candidate_row.items())[:40]
            )
        self._source_text.setHtml(
            f"<h3>{escape(self._context_title(context))}</h3>"
            f"<p style='color:#53637a'>"
            f"PDF/资源: {escape(str(context.get('resource_file_name') or context.get('source_file') or ''))}<br>"
            f"表格: {escape(str(context.get('source_table') or context.get('sheet_name') or context.get('table_title') or ''))}<br>"
            f"页码: {escape(str(context.get('page_number') or '未知'))} · 来源行: {escape(str(context.get('source_row') or ''))}<br>"
            f"路径: {escape(str(context.get('resource_abs_path') or ''))}"
            f"</p>{row_html}"
        )
        self._source_stack.setCurrentWidget(self._source_text)

    def _context_title(self, context: dict[str, Any]) -> str:
        return (
            context.get("article_title")
            or context.get("reference")
            or context.get("article_doi")
            or context.get("record_id")
            or "来源定位"
        )
