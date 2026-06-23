"""Unified agent workbench for resource selection, extraction, and mapping."""

from __future__ import annotations

from html import escape
from pathlib import Path
from typing import Any, Callable

from PySide6.QtCore import QPoint, QRect, Qt
from PySide6.QtGui import QColor, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QSpinBox,
    QStackedWidget,
    QTabWidget,
    QTableView,
    QTableWidget,
    QTableWidgetItem,
    QTextBrowser,
    QVBoxLayout,
)

from .app_widgets import HeaderPage, MetricCard
from .mapping_page import EditMappingDialog
from .resources_page import (
    FigureCard,
    ParagraphCard,
    TablePreviewCard,
    highlight_terms,
    parse_markdown_table,
)
from .viewmodels import DataFrameTableModel


class SelectablePdfPageLabel(QLabel):
    """PDF page preview label with drag-to-select normalized bounding boxes."""

    def __init__(self, text: str = "", parent=None):
        super().__init__(text, parent)
        self._dragging = False
        self._start = QPoint()
        self._selection = QRect()
        self.setMouseTracking(True)

    def set_page_pixmap(self, pixmap: QPixmap) -> None:
        self._selection = QRect()
        self.setPixmap(pixmap)
        self.update()

    def clear_page(self, text: str) -> None:
        self._selection = QRect()
        self.setPixmap(QPixmap())
        self.setText(text)
        self.update()

    def selected_bbox_text(self) -> str:
        rect = self._selection.normalized()
        pix_rect = self._pixmap_rect()
        if rect.isNull() or pix_rect.isNull():
            return ""
        clipped = rect.intersected(pix_rect)
        if clipped.width() < 6 or clipped.height() < 6:
            return ""
        x0 = (clipped.left() - pix_rect.left()) / pix_rect.width()
        y0 = (clipped.top() - pix_rect.top()) / pix_rect.height()
        x1 = (clipped.right() - pix_rect.left()) / pix_rect.width()
        y1 = (clipped.bottom() - pix_rect.top()) / pix_rect.height()
        return ",".join(f"{v:.4f}" for v in (x0, y0, x1, y1))

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self._pixmap_rect().contains(event.position().toPoint()):
            self._dragging = True
            self._start = event.position().toPoint()
            self._selection = QRect(self._start, self._start)
            self.update()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self._dragging:
            self._selection = QRect(self._start, event.position().toPoint()).normalized()
            self.update()
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if self._dragging and event.button() == Qt.MouseButton.LeftButton:
            self._dragging = False
            self._selection = QRect(self._start, event.position().toPoint()).normalized()
            self.update()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        rect = self._selection.normalized()
        if rect.isNull():
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.fillRect(rect, QColor(20, 104, 216, 38))
        pen = QPen(QColor("#1468d8"), 2)
        painter.setPen(pen)
        painter.drawRect(rect)

    def _pixmap_rect(self) -> QRect:
        pixmap = self.pixmap()
        if pixmap is None or pixmap.isNull():
            return QRect()
        size = pixmap.size()
        x = int((self.width() - size.width()) / 2)
        y = int((self.height() - size.height()) / 2)
        return QRect(x, y, size.width(), size.height())


class EditExtractionCandidateDialog(QDialog):
    """Edit an evidence-level extracted candidate before confirming it."""

    def __init__(self, candidate: dict[str, Any], parent=None):
        super().__init__(parent)
        self.setWindowTitle("编辑候选值")
        self.setMinimumWidth(420)
        layout = QFormLayout(self)

        self.target_header = QLineEdit(candidate.get("target_header", ""))
        self.target_field = QLineEdit(candidate.get("target_field", ""))
        self.target_unit = QLineEdit(candidate.get("target_unit", ""))
        self.value = QLineEdit(str(candidate.get("value", "")))
        self.source_unit = QLineEdit(candidate.get("source_unit", ""))
        self.save_as_rule = QCheckBox("记住为抽取规则")

        layout.addRow("目标表头:", self.target_header)
        layout.addRow("目标字段:", self.target_field)
        layout.addRow("目标单位:", self.target_unit)
        layout.addRow("候选值:", self.value)
        layout.addRow("原始单位:", self.source_unit)
        layout.addRow(self.save_as_rule)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addRow(buttons)

    def get_values(self) -> dict[str, Any]:
        return {
            "target_header": self.target_header.text().strip(),
            "target_field": self.target_field.text().strip(),
            "target_unit": self.target_unit.text().strip(),
            "value": self.value.text().strip(),
            "source_unit": self.source_unit.text().strip(),
            "save_as_rule": self.save_as_rule.isChecked(),
        }


class AgentWorkbenchPage(HeaderPage):
    """Single place for resource filtering, candidate extraction, and mapping."""

    def __init__(self):
        self.discover_requested: Callable[[str], None] | None = None
        self.extract_selected_requested: Callable[[list[tuple[str, str]]], None] | None = None
        self.run_mapping_requested: Callable[[str, bool], None] | None = None
        self.extract_candidates_requested: Callable[[str], None] | None = None
        self.confirm_candidate_requested: Callable[..., None] | None = None
        self.reject_candidate_requested: Callable[[str], None] | None = None
        self.confirm_mapping_requested: Callable[..., None] | None = None
        self.reject_mapping_requested: Callable[[str], None] | None = None
        self.manual_pdf_evidence_requested: Callable[[str, int, str, str], None] | None = None
        self.filter_changed: Callable[[], None] | None = None
        self.table_preview_requested: Callable[[str], tuple[list[dict], list[str]]] | None = None

        self._schema_fields: list[str] = []
        self._articles: list[dict[str, Any]] = []
        self._header_configs: list[dict[str, Any]] = []
        self._pdf_resources: list[dict[str, Any]] = []
        self._elements: dict[str, list[dict[str, Any]]] = {"tables": [], "figures": [], "links": [], "text": []}
        self._selected_sources: list[dict[str, Any]] = []
        self._source_by_id: dict[str, dict[str, Any]] = {}
        self._mappings: list[dict[str, Any]] = []
        self._extraction_candidates_by_evidence: dict[str, list[dict[str, Any]]] = {}
        self._visible_extraction_candidates: list[dict[str, Any]] = []
        self._current_extraction_candidate: dict[str, Any] | None = None
        self._current_source: dict[str, Any] | None = None
        self._current_mapping: dict[str, Any] | None = None
        self._console_lines: list[str] = []

        self._candidate_cards: list[Any] = []
        self._mapping_model = DataFrameTableModel(columns=[
            "mapping_id", "source_field", "target_field", "source_unit",
            "target_unit", "mapping_type", "confidence", "risk_level",
            "requires_review", "reason",
        ])
        self._candidate_model = DataFrameTableModel(columns=["字段", "目标表头", "值/证据", "置信度", "状态"])

        super().__init__(
            "智能体工作台",
            "在一个工作台内完成文献选择、资源筛选、数据抽取和字段映射，确认后进入人工审核与标准化导出。",
        )
        self._build_body()

    def _build_body(self) -> None:
        top = QHBoxLayout()
        top.setSpacing(10)
        top.addWidget(QLabel("当前文献:"))
        self.article_selector = QComboBox()
        self.article_selector.setMinimumWidth(360)
        self.article_selector.currentIndexChanged.connect(self._on_article_changed)
        top.addWidget(self.article_selector)

        top.addWidget(QLabel("表头配置:"))
        self.header_config_label = QLabel("未绑定")
        self.header_config_label.setStyleSheet(
            "color:#1468d8; font-weight:650; padding:5px 9px; "
            "border:1px solid #b8d7ff; border-radius:6px; background:#f0f6ff;"
        )
        top.addWidget(self.header_config_label)
        top.addStretch(1)
        self.refresh_btn = QPushButton("刷新")
        self.refresh_btn.clicked.connect(lambda: self.filter_changed and self.filter_changed())
        top.addWidget(self.refresh_btn)
        self._content_layout.addLayout(top)

        self.step_tabs = QTabWidget()
        self.step_tabs.addTab(self._pdf_step(), "0 原文 PDF")
        self.step_tabs.addTab(self._resource_step(), "1 资源筛选")
        self.step_tabs.addTab(self._extraction_step(), "2 数据抽取")
        self.step_tabs.addTab(self._mapping_step(), "3 字段映射")
        self.step_tabs.addTab(self._review_step(), "4 审核确认")
        self._content_layout.addWidget(self.step_tabs, 1)

    def _pdf_step(self):
        page = QFrame()
        page.setFrameShape(QFrame.NoFrame)
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 10, 0, 0)
        layout.setSpacing(10)

        toolbar = QHBoxLayout()
        toolbar.setSpacing(10)
        toolbar.addWidget(QLabel("PDF 资源:"))
        self.pdf_selector = QComboBox()
        self.pdf_selector.setMinimumWidth(360)
        self.pdf_selector.currentIndexChanged.connect(self._on_pdf_changed)
        toolbar.addWidget(self.pdf_selector)
        self.prev_page_btn = QPushButton("上一页")
        self.prev_page_btn.clicked.connect(lambda: self._move_pdf_page(-1))
        self.next_page_btn = QPushButton("下一页")
        self.next_page_btn.clicked.connect(lambda: self._move_pdf_page(1))
        self.pdf_page_spin = QSpinBox()
        self.pdf_page_spin.setMinimum(1)
        self.pdf_page_spin.setMaximum(1)
        self.pdf_page_spin.valueChanged.connect(self._render_current_pdf_page)
        toolbar.addWidget(self.prev_page_btn)
        toolbar.addWidget(self.pdf_page_spin)
        toolbar.addWidget(self.next_page_btn)
        toolbar.addStretch(1)
        self.add_pdf_page_btn = QPushButton("将框选/当前页加入抽取队列")
        self.add_pdf_page_btn.setObjectName("primaryButton")
        self.add_pdf_page_btn.clicked.connect(self._add_current_pdf_page)
        toolbar.addWidget(self.add_pdf_page_btn)
        layout.addLayout(toolbar)

        split = QSplitter(Qt.Horizontal)
        viewer = QFrame()
        viewer.setProperty("class", "card")
        viewer_layout = QVBoxLayout(viewer)
        viewer_layout.setContentsMargins(12, 12, 12, 12)
        viewer_layout.addWidget(QLabel("PDF 原文页面"))
        self.pdf_page_image = SelectablePdfPageLabel("当前文章还没有可显示的 PDF。请先在文献导入中导入 PDF。")
        self.pdf_page_image.setAlignment(Qt.AlignCenter)
        self.pdf_page_image.setWordWrap(True)
        self.pdf_page_image.setStyleSheet(
            "background:#ffffff; color:#53637a; border:1px solid #dfe5ee; border-radius:6px;"
        )
        viewer_layout.addWidget(self.pdf_page_image, 1)
        split.addWidget(viewer)

        side = QFrame()
        side.setProperty("class", "card")
        side_layout = QVBoxLayout(side)
        side_layout.setContentsMargins(12, 12, 12, 12)
        side_layout.addWidget(QLabel("人工选择说明"))
        self.pdf_note = QPlainTextEdit()
        self.pdf_note.setPlaceholderText(
            "可在左侧 PDF 页面拖拽框选表格/段落/图像区域。\n"
            "例如：框选 Supplementary Table S1，里面有 Li ppm / Na2O(wt%) 等地化数据。"
        )
        self.pdf_note.setMinimumHeight(140)
        side_layout.addWidget(self.pdf_note)
        self.pdf_page_info = QTextBrowser()
        self.pdf_page_info.setStyleSheet("QTextBrowser { background:#ffffff; border:0; padding:8px; }")
        side_layout.addWidget(self.pdf_page_info, 1)
        split.addWidget(side)
        split.setSizes([840, 360])
        layout.addWidget(split, 1)
        return page

    def _resource_step(self):
        page = QFrame()
        page.setFrameShape(QFrame.NoFrame)
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 10, 0, 0)
        layout.setSpacing(12)

        metrics = QHBoxLayout()
        self.table_metric = MetricCard("表格", "0", accent="#1468d8")
        self.figure_metric = MetricCard("图片", "0", accent="#7c3aed")
        self.link_metric = MetricCard("链接", "0", accent="#027a48")
        self.text_metric = MetricCard("相关段落", "0", accent="#b54708")
        for card in (self.table_metric, self.figure_metric, self.link_metric, self.text_metric):
            metrics.addWidget(card)
        layout.addLayout(metrics)

        toolbar = QHBoxLayout()
        toolbar.setSpacing(10)
        self.discover_btn = QPushButton("重新发现")
        self.discover_btn.clicked.connect(self._do_discover)
        self.add_selected_btn = QPushButton("加入抽取队列")
        self.add_selected_btn.setObjectName("primaryButton")
        self.add_selected_btn.clicked.connect(self._do_add_selected)
        self.add_selected_btn.setEnabled(False)
        self.resource_type_filter = QComboBox()
        self.resource_type_filter.addItems(["全部资源", "表格", "图片", "相关段落"])
        self.resource_type_filter.currentIndexChanged.connect(self._rebuild_resource_candidates)
        self.resource_search = QLineEdit()
        self.resource_search.setPlaceholderText("搜索资源、页码、表头关键词...")
        self.resource_search.textChanged.connect(self._rebuild_resource_candidates)
        toolbar.addWidget(self.discover_btn)
        toolbar.addWidget(self.add_selected_btn)
        toolbar.addWidget(self.resource_type_filter)
        toolbar.addWidget(self.resource_search, 1)
        layout.addLayout(toolbar)

        split = QSplitter(Qt.Horizontal)
        left = QFrame()
        left.setProperty("class", "card")
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(12, 12, 12, 12)
        left_title = QLabel("所有可能用于抽取的位置")
        left_title.setObjectName("sectionTitle")
        left_layout.addWidget(left_title)
        self.all_resources_area = QScrollArea()
        self.all_resources_area.setWidgetResizable(True)
        self.all_resources_area.setFrameShape(QFrame.NoFrame)
        self.all_resources_widget = QFrame()
        self.all_resources_layout = QVBoxLayout(self.all_resources_widget)
        self.all_resources_layout.setContentsMargins(2, 2, 2, 2)
        self.all_resources_layout.setSpacing(8)
        self.all_resources_area.setWidget(self.all_resources_widget)
        left_layout.addWidget(self.all_resources_area, 1)
        split.addWidget(left)

        right = QFrame()
        right.setProperty("class", "card")
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(12, 12, 12, 12)
        self.queue_title = QLabel("已选抽取队列")
        self.queue_title.setObjectName("sectionTitle")
        right_layout.addWidget(self.queue_title)
        self.queue_list = QListWidget()
        self.queue_list.setSelectionMode(QAbstractItemView.SingleSelection)
        self.queue_list.currentItemChanged.connect(self._on_queue_item_changed)
        right_layout.addWidget(self.queue_list, 1)
        locator_title = QLabel("原文定位预览")
        locator_title.setObjectName("sectionTitle")
        right_layout.addWidget(locator_title)
        self.resource_locator_stack = QStackedWidget()
        self.resource_locator_text = QTextBrowser()
        self.resource_locator_text.setOpenExternalLinks(True)
        self.resource_locator_text.setStyleSheet("QTextBrowser { background:#ffffff; border:0; padding:10px; }")
        self.resource_locator_image = QLabel("点击左侧候选资源，可查看 PDF 页、表格截图或段落证据。")
        self.resource_locator_image.setAlignment(Qt.AlignCenter)
        self.resource_locator_image.setWordWrap(True)
        self.resource_locator_image.setStyleSheet(
            "background:#ffffff; color:#53637a; border:1px solid #dfe5ee; border-radius:6px;"
        )
        self.resource_locator_stack.addWidget(self.resource_locator_text)
        self.resource_locator_stack.addWidget(self.resource_locator_image)
        self.resource_locator_stack.setMinimumHeight(240)
        right_layout.addWidget(self.resource_locator_stack, 1)
        queue_hint = QLabel("右侧队列会进入下一步数据抽取。点击队列项可预览。")
        queue_hint.setWordWrap(True)
        queue_hint.setStyleSheet("color:#53637a;")
        right_layout.addWidget(queue_hint)
        split.addWidget(right)
        split.setSizes([850, 380])
        layout.addWidget(split, 1)
        return page

    def _extraction_step(self):
        page = QFrame()
        page.setFrameShape(QFrame.NoFrame)
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 10, 0, 0)
        layout.setSpacing(10)

        split = QSplitter(Qt.Horizontal)
        left = QFrame()
        left.setProperty("class", "card")
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(12, 12, 12, 12)
        left_title = QLabel("已选资源")
        left_title.setObjectName("sectionTitle")
        left_layout.addWidget(left_title)
        self.extract_source_list = QListWidget()
        self.extract_source_list.currentItemChanged.connect(self._on_extract_item_changed)
        left_layout.addWidget(self.extract_source_list, 1)
        split.addWidget(left)

        middle = QFrame()
        middle.setProperty("class", "card")
        middle_layout = QVBoxLayout(middle)
        middle_layout.setContentsMargins(12, 12, 12, 12)
        preview_title = QLabel("资源可视化")
        preview_title.setObjectName("sectionTitle")
        middle_layout.addWidget(preview_title)
        self.preview_stack = QStackedWidget()
        self.preview_table = QTableWidget()
        self.preview_table.setAlternatingRowColors(True)
        self.preview_table.verticalHeader().setVisible(False)
        self.preview_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.preview_text = QTextBrowser()
        self.preview_text.setOpenExternalLinks(True)
        self.preview_text.setStyleSheet("QTextBrowser { background:#ffffff; border:0; padding:12px; }")
        self.preview_image = QLabel("选择左侧资源后，这里显示表格、图片或完整段落。")
        self.preview_image.setAlignment(Qt.AlignCenter)
        self.preview_image.setWordWrap(True)
        self.preview_image.setStyleSheet("background:#ffffff; color:#53637a;")
        self.preview_stack.addWidget(self.preview_table)
        self.preview_stack.addWidget(self.preview_text)
        self.preview_stack.addWidget(self.preview_image)
        middle_layout.addWidget(self.preview_stack, 1)
        split.addWidget(middle)

        right = QFrame()
        right.setProperty("class", "card")
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(12, 12, 12, 12)
        right_title = QLabel("AI 候选字段和值")
        right_title.setObjectName("sectionTitle")
        right_layout.addWidget(right_title)
        self.candidate_table = QTableView()
        self.candidate_table.setAlternatingRowColors(True)
        self.candidate_table.setModel(self._candidate_model)
        self.candidate_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.candidate_table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.candidate_table.selectionModel().selectionChanged.connect(self._on_candidate_selection_changed)
        right_layout.addWidget(self.candidate_table, 1)
        self.run_mapping_for_source_btn = QPushButton("AI 抽取/映射此资源")
        self.run_mapping_for_source_btn.setObjectName("primaryButton")
        self.run_mapping_for_source_btn.clicked.connect(self._run_candidate_or_mapping_for_current_source)
        right_layout.addWidget(self.run_mapping_for_source_btn)
        candidate_btn_row = QHBoxLayout()
        self.confirm_candidate_btn = QPushButton("确认入队")
        self.confirm_candidate_btn.setObjectName("primaryButton")
        self.confirm_candidate_btn.clicked.connect(self._confirm_current_candidate)
        self.edit_candidate_btn = QPushButton("编辑/记住规则")
        self.edit_candidate_btn.clicked.connect(self._edit_current_candidate)
        self.reject_candidate_btn = QPushButton("拒绝")
        self.reject_candidate_btn.setObjectName("dangerButton")
        self.reject_candidate_btn.clicked.connect(self._reject_current_candidate)
        for btn in (self.confirm_candidate_btn, self.edit_candidate_btn, self.reject_candidate_btn):
            btn.setEnabled(False)
            candidate_btn_row.addWidget(btn)
        right_layout.addLayout(candidate_btn_row)
        split.addWidget(right)
        split.setSizes([250, 730, 380])
        layout.addWidget(split, 1)

        console_frame = QFrame()
        console_frame.setProperty("class", "card")
        console_layout = QVBoxLayout(console_frame)
        console_layout.setContentsMargins(12, 10, 12, 12)
        console_head = QHBoxLayout()
        title = QLabel("工作台控制台")
        title.setObjectName("sectionTitle")
        self.task_status = QLabel("空闲")
        clear_btn = QPushButton("清空")
        clear_btn.clicked.connect(self._clear_console)
        console_head.addWidget(title)
        console_head.addStretch(1)
        console_head.addWidget(self.task_status)
        console_head.addWidget(clear_btn)
        console_layout.addLayout(console_head)
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        console_layout.addWidget(self.progress)
        self.console = QPlainTextEdit()
        self.console.setObjectName("importConsole")
        self.console.setReadOnly(True)
        self.console.setMinimumHeight(105)
        self.console.setPlainText("等待工作台任务...")
        console_layout.addWidget(self.console)
        layout.addWidget(console_frame)
        return page

    def _mapping_step(self):
        page = QFrame()
        page.setFrameShape(QFrame.NoFrame)
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 10, 0, 0)
        layout.setSpacing(10)

        toolbar = QHBoxLayout()
        self.mapping_source_label = QLabel("当前资源: —")
        self.mapping_source_label.setStyleSheet("color:#53637a; font-weight:650;")
        toolbar.addWidget(self.mapping_source_label, 1)
        self.grouped_mapping_check = QCheckBox("分组 LLM 补缺")
        self.grouped_mapping_check.setChecked(True)
        toolbar.addWidget(self.grouped_mapping_check)
        self.run_mapping_btn = QPushButton("运行/刷新映射")
        self.run_mapping_btn.setObjectName("primaryButton")
        self.run_mapping_btn.clicked.connect(self._run_mapping_for_current_source)
        toolbar.addWidget(self.run_mapping_btn)
        layout.addLayout(toolbar)

        split = QSplitter(Qt.Horizontal)
        left = QFrame()
        left.setProperty("class", "card")
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(12, 12, 12, 12)
        left_layout.addWidget(QLabel("资源队列"))
        self.mapping_source_list = QListWidget()
        self.mapping_source_list.currentItemChanged.connect(self._on_mapping_item_changed)
        left_layout.addWidget(self.mapping_source_list, 1)
        split.addWidget(left)

        middle = QFrame()
        middle.setProperty("class", "card")
        middle_layout = QVBoxLayout(middle)
        middle_layout.setContentsMargins(12, 12, 12, 12)
        middle_layout.addWidget(QLabel("证据预览"))
        self.mapping_preview = QTextBrowser()
        self.mapping_preview.setOpenExternalLinks(True)
        middle_layout.addWidget(self.mapping_preview, 1)
        split.addWidget(middle)

        right = QFrame()
        right.setProperty("class", "card")
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(12, 12, 12, 12)
        right_layout.addWidget(QLabel("映射到目标表头"))
        self.mapping_table = QTableView()
        self.mapping_table.setAlternatingRowColors(True)
        self.mapping_table.setModel(self._mapping_model)
        self.mapping_table.horizontalHeader().setStretchLastSection(True)
        self.mapping_table.selectionModel().selectionChanged.connect(self._on_mapping_selection_changed)
        right_layout.addWidget(self.mapping_table, 1)
        btn_row = QHBoxLayout()
        self.confirm_mapping_btn = QPushButton("确认")
        self.confirm_mapping_btn.setObjectName("primaryButton")
        self.confirm_mapping_btn.clicked.connect(self._confirm_current_mapping)
        self.edit_mapping_btn = QPushButton("编辑/记住规则")
        self.edit_mapping_btn.clicked.connect(self._edit_current_mapping)
        self.reject_mapping_btn = QPushButton("拒绝")
        self.reject_mapping_btn.setObjectName("dangerButton")
        self.reject_mapping_btn.clicked.connect(self._reject_current_mapping)
        for btn in (self.confirm_mapping_btn, self.edit_mapping_btn, self.reject_mapping_btn):
            btn.setEnabled(False)
            btn_row.addWidget(btn)
        right_layout.addLayout(btn_row)
        split.addWidget(right)
        split.setSizes([250, 520, 640])
        layout.addWidget(split, 1)
        return page

    def _review_step(self):
        page = QFrame()
        page.setFrameShape(QFrame.NoFrame)
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 10, 0, 0)
        layout.setSpacing(12)
        card = QFrame()
        card.setProperty("class", "card")
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(18, 18, 18, 18)
        title = QLabel("下一步：人工审核")
        title.setObjectName("sectionTitle")
        body = QLabel(
            "字段映射确认后，低置信度、单位不一致和需要换算的条目会进入人工审核页面。"
            "审核通过的数据会继续进入标准化导出和溯源查看。"
        )
        body.setWordWrap(True)
        body.setStyleSheet("color:#53637a;")
        card_layout.addWidget(title)
        card_layout.addWidget(body)
        layout.addWidget(card)
        layout.addStretch(1)
        return page

    def set_articles(self, articles: list[dict[str, Any]]) -> None:
        current = self.get_selected_article_id()
        self._articles = articles
        self.article_selector.blockSignals(True)
        self.article_selector.clear()
        if not articles:
            self.article_selector.addItem("暂无已导入文献", "")
        for article in articles:
            title = article.get("title") or article.get("doi") or article.get("article_id")
            label = f"{title[:72]}  ·  {article.get('doi') or article.get('article_id')}"
            self.article_selector.addItem(label, article.get("article_id"))
        if current:
            idx = self.article_selector.findData(current)
            if idx >= 0:
                self.article_selector.setCurrentIndex(idx)
        self.article_selector.blockSignals(False)

    def set_header_configs(self, configs: list[dict[str, Any]]) -> None:
        self._header_configs = configs
        if configs:
            first = configs[0]
            self.header_config_label.setText(f"{first.get('name', first.get('config_id'))} · {first.get('field_count', 0)} 字段")
        else:
            self.header_config_label.setText("未配置")

    def set_schema_fields(self, fields: list[str]) -> None:
        self._schema_fields = [f for f in fields if f and len(str(f)) > 1]

    def get_selected_article_id(self) -> str | None:
        data = self.article_selector.currentData()
        return str(data) if data else None

    def set_pdf_resources(self, resources: list[dict[str, Any]]) -> None:
        current = self.pdf_selector.currentData() if hasattr(self, "pdf_selector") else None
        self._pdf_resources = resources
        self.pdf_selector.blockSignals(True)
        self.pdf_selector.clear()
        if not resources:
            self.pdf_selector.addItem("当前文献暂无 PDF", "")
        for resource in resources:
            page_count = resource.get("page_count") or 0
            label = f"{resource.get('file_name') or resource.get('resource_id')} · {page_count or '?'} 页"
            self.pdf_selector.addItem(label, resource.get("resource_id"))
        if current:
            idx = self.pdf_selector.findData(current)
            if idx >= 0:
                self.pdf_selector.setCurrentIndex(idx)
        self.pdf_selector.blockSignals(False)
        self._on_pdf_changed()

    def get_selected_pdf_resource(self) -> dict[str, Any] | None:
        resource_id = self.pdf_selector.currentData()
        if not resource_id:
            return None
        for resource in self._pdf_resources:
            if resource.get("resource_id") == resource_id:
                return resource
        return None

    def set_discovered_elements(self, elements: dict[str, list[dict[str, Any]]]) -> None:
        self._elements = elements
        tables = elements.get("tables", [])
        figures = elements.get("figures", [])
        links = elements.get("links", [])
        text = self._relevant_text(elements.get("text", []))
        self.table_metric.set_value(len(tables))
        self.figure_metric.set_value(len(figures))
        self.link_metric.set_value(len(links))
        self.text_metric.set_value(len(text))
        self._rebuild_resource_candidates()

    def set_selected_sources(self, items: list[dict[str, Any]]) -> None:
        article_id = self.get_selected_article_id()
        if article_id:
            items = [item for item in items if item.get("article_id") == article_id]
        self._selected_sources = items
        self._source_by_id = {str(item.get("item_id")): item for item in items if item.get("item_id")}
        self.queue_title.setText(f"已选抽取队列 · {len(items)} 项")
        self._fill_source_list(self.queue_list, items)
        self._fill_source_list(self.extract_source_list, items)
        self._fill_source_list(self.mapping_source_list, items)
        if items and not self._current_source:
            self._select_source(items[0])
        elif not items:
            self._show_empty_preview()
            self._set_candidate_rows([])
            self._show_mapping_rows([])

    def set_mappings(self, mappings: list[dict[str, Any]]) -> None:
        self._mappings = mappings
        self._refresh_mapping_for_current_source()

    def set_extraction_candidates(self, candidates: list[dict[str, Any]]) -> None:
        grouped: dict[str, list[dict[str, Any]]] = {}
        for candidate in candidates:
            grouped.setdefault(str(candidate.get("evidence_id") or ""), []).append(candidate)
        self._extraction_candidates_by_evidence = grouped
        if self._current_source:
            self._set_candidate_rows(self._candidate_rows_for_source(self._current_source))

    def set_active_step(self, index: int) -> None:
        if 0 <= index < self.step_tabs.count():
            self.step_tabs.setCurrentIndex(index)

    def _on_pdf_changed(self) -> None:
        resource = self.get_selected_pdf_resource()
        page_count = int(resource.get("page_count") or 1) if resource else 1
        self.pdf_page_spin.blockSignals(True)
        self.pdf_page_spin.setMaximum(max(1, page_count))
        self.pdf_page_spin.setValue(1)
        self.pdf_page_spin.blockSignals(False)
        self.add_pdf_page_btn.setEnabled(bool(resource and resource.get("resource_abs_path")))
        self.prev_page_btn.setEnabled(bool(resource))
        self.next_page_btn.setEnabled(bool(resource))
        self._render_current_pdf_page()

    def _move_pdf_page(self, delta: int) -> None:
        value = self.pdf_page_spin.value() + delta
        value = max(self.pdf_page_spin.minimum(), min(self.pdf_page_spin.maximum(), value))
        self.pdf_page_spin.setValue(value)

    def _render_current_pdf_page(self) -> None:
        resource = self.get_selected_pdf_resource()
        if not resource:
            self.pdf_page_image.clear_page("当前文章还没有可显示的 PDF。请先在文献导入中导入 PDF。")
            self.pdf_page_info.setHtml("<span style='color:#53637a;'>暂无 PDF 资源。</span>")
            return
        pdf_path = Path(resource.get("resource_abs_path") or "")
        page_number = self.pdf_page_spin.value()
        preview_path = self._render_pdf_page_to_cache(pdf_path, page_number)
        if preview_path and Path(preview_path).exists():
            pixmap = QPixmap(preview_path)
            if not pixmap.isNull():
                self.pdf_page_image.setText("")
                self.pdf_page_image.set_page_pixmap(
                    pixmap.scaled(980, 700, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                )
            else:
                self.pdf_page_image.setText(f"PDF 页面无法加载：{pdf_path}")
        else:
            self.pdf_page_image.clear_page(f"无法渲染 PDF 页面：{pdf_path}")
        bbox = self.pdf_page_image.selected_bbox_text()
        bbox_text = f"<br>当前框选: {escape(bbox)}" if bbox else "<br>当前框选: 未框选，将保存整页线索"
        self.pdf_page_info.setHtml(
            f"<h3>{escape(str(resource.get('file_name') or resource.get('resource_id')))}</h3>"
            f"<p style='color:#53637a'>"
            f"资源类型: {escape(str(resource.get('resource_type') or ''))}<br>"
            f"页码: {page_number} / {resource.get('page_count') or '?'}<br>"
            f"路径: {escape(str(pdf_path))}<br>"
            f"文章: {escape(str(resource.get('article_title') or ''))}"
            f"{bbox_text}</p>"
        )

    def _render_pdf_page_to_cache(self, pdf_path: Path, page_number: int) -> str:
        if not pdf_path.exists() or pdf_path.suffix.lower() != ".pdf":
            return ""
        try:
            import fitz

            out_dir = pdf_path.parent / ".cache" / "manual_pages"
            out_dir.mkdir(parents=True, exist_ok=True)
            out_path = out_dir / f"{pdf_path.stem}_manual_{page_number}.png"
            if out_path.exists():
                return str(out_path)
            with fitz.open(str(pdf_path)) as doc:
                page_index = page_number - 1
                if page_index < 0 or page_index >= len(doc):
                    return ""
                pix = doc[page_index].get_pixmap(matrix=fitz.Matrix(1.7, 1.7), alpha=False)
                pix.save(str(out_path))
            return str(out_path)
        except Exception:
            return ""

    def _add_current_pdf_page(self) -> None:
        resource = self.get_selected_pdf_resource()
        if not resource or not self.manual_pdf_evidence_requested:
            return
        note = self.pdf_note.toPlainText().strip()
        bbox_norm = self.pdf_page_image.selected_bbox_text()
        self.manual_pdf_evidence_requested(resource["resource_id"], self.pdf_page_spin.value(), note, bbox_norm)
        self.step_tabs.setCurrentIndex(2)

    def append_console(self, message: str, level: str = "INFO") -> None:
        from datetime import datetime
        line = f"[{datetime.now().strftime('%H:%M:%S')}] {level:<5} {message}"
        self._console_lines.append(line)
        self._console_lines = self._console_lines[-500:]
        self.console.setPlainText("\n".join(self._console_lines))
        self.console.verticalScrollBar().setValue(self.console.verticalScrollBar().maximum())

    def set_task_state(self, status: str, progress: int | float | None = None) -> None:
        self.task_status.setText(status)
        if progress is not None:
            self.progress.setValue(int(progress))

    def _relevant_text(self, text_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        relevant: list[dict[str, Any]] = []
        for row in text_rows:
            data = dict(row)
            content = data.get("evidence_text") or ""
            lower = content.lower()
            matched = [f for f in self._schema_fields if str(f).lower() in lower]
            hints = [f for f in (data.get("target_header"), data.get("target_field")) if f]
            if matched or hints or not self._schema_fields or data.get("evidence_type") == "table_caption":
                data["_matched_fields"] = matched or hints
                relevant.append(data)
        return self._dedupe_paragraphs(relevant)

    def _dedupe_paragraphs(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        sorted_rows = sorted(
            rows,
            key=lambda r: (len((r.get("evidence_text") or "").strip()), float(r.get("confidence") or 0)),
            reverse=True,
        )
        kept: list[dict[str, Any]] = []
        fingerprints: list[str] = []
        for row in sorted_rows:
            text = " ".join((row.get("evidence_text") or "").split()).lower()
            if not text:
                continue
            fp = text[:700]
            if fp in fingerprints or any(fp in old or old in fp for old in fingerprints):
                continue
            fingerprints.append(fp)
            kept.append(row)
        return sorted(kept, key=lambda r: str(r.get("page_or_section") or ""))

    def _rebuild_resource_candidates(self) -> None:
        self._clear_layout(self.all_resources_layout)
        self._candidate_cards.clear()
        kind_filter = self.resource_type_filter.currentText() if hasattr(self, "resource_type_filter") else "全部资源"
        query = self.resource_search.text().strip().lower() if hasattr(self, "resource_search") else ""

        def allowed(kind: str, haystack: str) -> bool:
            if kind_filter != "全部资源" and kind_filter != kind:
                return False
            return not query or query in haystack.lower()

        if kind_filter in {"全部资源", "表格"}:
            self._add_section_label("表格")
            for table in self._elements.get("tables", []):
                title = table.get("title") or table.get("asset_id") or "Table"
                page = table.get("page_or_sheet") or ""
                haystack = f"{title} {page} {table.get('caption', '')}"
                if not allowed("表格", haystack):
                    continue
                card = TablePreviewCard(
                    title,
                    page,
                    table.get("caption") or "",
                    table.get("asset_id") or "",
                    selected=table.get("status") in {"selected_for_extraction", "selected"},
                    row_count=table.get("row_count"),
                    image_path=table.get("raw_file_path") or "",
                )
                card._cb.stateChanged.connect(
                    lambda *_args, item=dict(table): self._preview_candidate_source("table", item)
                )
                card._cb.stateChanged.connect(lambda *_: self._update_candidate_selection_state())
                self._candidate_cards.append(("table", card))
                self.all_resources_layout.addWidget(card)

        if kind_filter in {"全部资源", "图片"}:
            self._add_section_label("图片")
            for figure in self._elements.get("figures", []):
                text = figure.get("evidence_text") or "Figure"
                img_path = ""
                if "\n[PATH]" in text:
                    text, img_path = text.split("\n[PATH]", 1)
                page = figure.get("page_or_section") or ""
                if not allowed("图片", f"{text} {page}"):
                    continue
                card = FigureCard(text, page, img_path, figure.get("evidence_id") or "", selected=figure.get("status") in {"selected_for_extraction", "selected"})
                card._cb.stateChanged.connect(
                    lambda *_args, item=dict(figure): self._preview_candidate_source("figure", item)
                )
                card._cb.stateChanged.connect(lambda *_: self._update_candidate_selection_state())
                self._candidate_cards.append(("figure", card))
                self.all_resources_layout.addWidget(card)

        if kind_filter in {"全部资源", "相关段落"}:
            self._add_section_label("相关段落")
            for para in self._relevant_text(self._elements.get("text", [])):
                text = para.get("evidence_text") or ""
                page = para.get("page_or_section") or ""
                matched = para.get("_matched_fields") or []
                if not allowed("相关段落", f"{text} {page} {' '.join(matched)}"):
                    continue
                card = ParagraphCard(text, page, para.get("evidence_id") or "", matched, selected=para.get("status") in {"selected_for_extraction", "selected"})
                card._cb.stateChanged.connect(
                    lambda *_args, item=dict(para): self._preview_candidate_source("text", item)
                )
                card._cb.stateChanged.connect(lambda *_: self._update_candidate_selection_state())
                self._candidate_cards.append(("text", card))
                self.all_resources_layout.addWidget(card)

        if not self._candidate_cards:
            empty = QLabel("当前筛选下没有资源。可先在文献导入页读取 PDF，或点击重新发现。")
            empty.setAlignment(Qt.AlignCenter)
            empty.setStyleSheet("color:#53637a; padding:40px;")
            self.all_resources_layout.addWidget(empty)
        self.all_resources_layout.addStretch(1)
        self._update_candidate_selection_state()

    def _add_section_label(self, text: str) -> None:
        label = QLabel(text)
        label.setStyleSheet("color:#53637a; font-weight:700; padding:4px 0;")
        self.all_resources_layout.addWidget(label)

    def _preview_candidate_source(self, kind: str, item: dict[str, Any]) -> None:
        source = self._source_from_discovered(kind, item)
        self._show_locator_preview(source)

    def _source_from_discovered(self, kind: str, item: dict[str, Any]) -> dict[str, Any]:
        if kind == "table":
            return {
                "item_type": "table",
                "item_id": item.get("asset_id"),
                "title": item.get("title") or item.get("asset_id"),
                "caption": item.get("caption") or "",
                "page_or_sheet": item.get("page_or_sheet") or "",
                "confidence": item.get("confidence"),
                "raw_file_path": item.get("raw_file_path") or "",
                "resource_abs_path": item.get("resource_abs_path") or "",
                "resource_local_path": item.get("resource_local_path") or "",
                "candidate_table_id": item.get("candidate_table_id"),
                "row_count": item.get("row_count"),
                "col_count": item.get("col_count"),
            }
        text = item.get("evidence_text") or ""
        return {
            "item_type": "figure" if kind == "figure" else "text",
            "item_id": item.get("evidence_id"),
            "evidence_text": text,
            "display_text": text[:260],
            "page_or_section": item.get("page_or_section") or "",
            "target_header": item.get("target_header") or "",
            "target_field": item.get("target_field") or "",
            "confidence": item.get("confidence"),
            "local_path": item.get("local_path") or "",
            "resource_abs_path": item.get("resource_abs_path") or "",
            "resource_local_path": item.get("resource_local_path") or "",
        }

    def _show_locator_preview(self, source: dict[str, Any]) -> None:
        page = source.get("page_or_sheet") or source.get("page_or_section") or ""
        title = source.get("title") or source.get("display_text") or source.get("item_id") or "候选资源"
        image_path = ""
        if source.get("item_type") in {"table", "figure", "text"}:
            image_path = self._image_path_from_source(source)
        if not image_path:
            image_path = self._pdf_page_image_path(source)
        if image_path and Path(image_path).exists():
            pixmap = QPixmap(image_path)
            if not pixmap.isNull():
                self.resource_locator_image.setText("")
                self.resource_locator_image.setPixmap(
                    pixmap.scaled(460, 360, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                )
                self.resource_locator_image.setToolTip(f"{title}\n{page}")
                self.resource_locator_stack.setCurrentWidget(self.resource_locator_image)
                return
        text = source.get("caption") or source.get("evidence_text") or source.get("display_text") or ""
        bbox = self._bbox_from_text(text)
        text = self._clean_evidence_text(text)
        fields = [f for f in (source.get("target_header"), source.get("target_field")) if f]
        bbox_html = f"<br>框选区域: {escape(bbox)}" if bbox else ""
        self.resource_locator_text.setHtml(
            f"<h3>{escape(str(title)[:140])}</h3>"
            f"<p style='color:#53637a'>位置: {escape(str(page or '未知'))}<br>"
            f"PDF: {escape(str(source.get('resource_abs_path') or source.get('resource_local_path') or '未记录'))}"
            f"{bbox_html}</p>"
            f"<div style='font-size:13px;line-height:1.65'>{highlight_terms(text, fields)}</div>"
        )
        self.resource_locator_stack.setCurrentWidget(self.resource_locator_text)

    def _pdf_page_image_path(self, source: dict[str, Any]) -> str:
        pdf_path = Path(source.get("resource_abs_path") or source.get("resource_local_path") or "")
        if not pdf_path.exists() or pdf_path.suffix.lower() != ".pdf":
            return ""
        page_num = self._page_number(source.get("page_or_sheet") or source.get("page_or_section") or "")
        if not page_num:
            return ""
        try:
            import fitz

            out_dir = pdf_path.parent / ".cache" / "pages"
            out_dir.mkdir(parents=True, exist_ok=True)
            out_path = out_dir / f"{pdf_path.stem}_locator_{page_num}.png"
            if out_path.exists():
                return str(out_path)
            with fitz.open(str(pdf_path)) as doc:
                page_index = page_num - 1
                if page_index < 0 or page_index >= len(doc):
                    return ""
                pix = doc[page_index].get_pixmap(matrix=fitz.Matrix(1.3, 1.3), alpha=False)
                pix.save(str(out_path))
            return str(out_path)
        except Exception:
            return ""

    def _page_number(self, value: str) -> int | None:
        import re

        match = re.search(r"page\s+(\d+)", value or "", flags=re.IGNORECASE)
        return int(match.group(1)) if match else None

    def _selected_candidate_tuples(self) -> list[tuple[str, str]]:
        selected: list[tuple[str, str]] = []
        for kind, card in self._candidate_cards:
            if not card.is_selected():
                continue
            if kind == "table":
                selected.append(("table", card.asset_id))
            elif kind == "figure":
                selected.append(("figure", card.evidence_id))
            else:
                selected.append(("text", card.evidence_id))
        return selected

    def _update_candidate_selection_state(self) -> None:
        selected = self._selected_candidate_tuples()
        self.add_selected_btn.setEnabled(bool(selected))

    def _do_add_selected(self) -> None:
        selected = self._selected_candidate_tuples()
        if selected and self.extract_selected_requested:
            self.extract_selected_requested(selected)
            self.step_tabs.setCurrentIndex(2)

    def _do_discover(self) -> None:
        resource_id = ""
        for bucket in ("tables", "figures", "text"):
            for item in self._elements.get(bucket, []):
                resource_id = item.get("resource_id") or ""
                if resource_id:
                    break
            if resource_id:
                break
        if resource_id and self.discover_requested:
            self.discover_requested(resource_id)
        elif self.filter_changed:
            self.filter_changed()

    def _on_article_changed(self) -> None:
        self._current_source = None
        if self.filter_changed:
            self.filter_changed()

    def _fill_source_list(self, widget: QListWidget, items: list[dict[str, Any]]) -> None:
        widget.blockSignals(True)
        widget.clear()
        for item in items:
            label = self._source_label(item)
            entry = QListWidgetItem(label)
            entry.setData(Qt.UserRole, item.get("item_id"))
            widget.addItem(entry)
        widget.blockSignals(False)

    def _source_label(self, item: dict[str, Any]) -> str:
        kind = {"table": "表格", "figure": "图表", "text": "段落"}.get(item.get("item_type"), "来源")
        page = item.get("page_or_sheet") or item.get("page_or_section") or ""
        text = item.get("display_text") or item.get("title") or item.get("item_id") or ""
        return f"{kind}  {page}  {str(text)[:78]}"

    def _on_queue_item_changed(self, current: QListWidgetItem | None, _previous=None) -> None:
        if current:
            self._select_source_by_id(current.data(Qt.UserRole))

    def _on_extract_item_changed(self, current: QListWidgetItem | None, _previous=None) -> None:
        if current:
            self._select_source_by_id(current.data(Qt.UserRole))

    def _on_mapping_item_changed(self, current: QListWidgetItem | None, _previous=None) -> None:
        if current:
            self._select_source_by_id(current.data(Qt.UserRole))
            self.step_tabs.setCurrentIndex(3)

    def _select_source_by_id(self, item_id: str) -> None:
        source = self._source_by_id.get(str(item_id))
        if source:
            self._select_source(source)

    def _select_source(self, source: dict[str, Any]) -> None:
        self._current_source = source
        self._sync_list_selection(source.get("item_id"))
        self._show_locator_preview(source)
        self._show_source_preview(source)
        self._set_candidate_rows(self._candidate_rows_for_source(source))
        self._refresh_mapping_for_current_source()

    def _sync_list_selection(self, item_id: str) -> None:
        for widget in (self.queue_list, self.extract_source_list, self.mapping_source_list):
            widget.blockSignals(True)
            for i in range(widget.count()):
                if str(widget.item(i).data(Qt.UserRole)) == str(item_id):
                    widget.setCurrentRow(i)
                    break
            widget.blockSignals(False)

    def _candidate_rows_for_source(self, source: dict[str, Any]) -> list[dict[str, Any]]:
        kind = source.get("item_type")
        evidence_id = str(source.get("item_id") or "")
        extracted = self._extraction_candidates_by_evidence.get(evidence_id, [])
        self._visible_extraction_candidates = extracted
        self._current_extraction_candidate = None
        if extracted:
            rows = []
            for item in extracted:
                rows.append({
                    "字段": item.get("target_field") or item.get("target_header") or "候选字段",
                    "目标表头": item.get("target_header") or item.get("target_field") or "待确认",
                    "值/证据": f"{item.get('value', '')} {item.get('source_unit') or item.get('target_unit') or ''}".strip(),
                    "置信度": f"{float(item.get('confidence') or 0):.2f}",
                    "状态": self._candidate_status_label(item.get("status") or "pending"),
                })
            return rows
        self._visible_extraction_candidates = []
        if kind == "table":
            rows = []
            if source.get("candidate_table_id"):
                rows.append({
                    "字段": "候选表",
                    "目标表头": "待字段映射",
                    "值/证据": f"{source.get('row_count', 0)} 行 x {source.get('col_count', 0)} 列",
                    "置信度": f"{float(source.get('confidence') or 0):.2f}",
                    "状态": "待映射",
                })
            else:
                rows.append({
                    "字段": "表格线索",
                    "目标表头": "待抽取",
                    "值/证据": source.get("title") or source.get("caption") or "",
                    "置信度": f"{float(source.get('confidence') or 0):.2f}",
                    "状态": "待抽取",
                })
            return rows
        if kind == "figure":
            text = self._clean_evidence_text(source.get("evidence_text") or "")
            bbox = self._bbox_from_text(source.get("evidence_text") or "")
            return [{
                "字段": source.get("target_field") or "图表证据",
                "目标表头": source.get("target_header") or "待确认",
                "值/证据": (f"框选区域 {bbox} · " if bbox else "") + text[:220],
                "置信度": f"{float(source.get('confidence') or 0):.2f}",
                "状态": "待抽取",
            }]
        text = self._clean_evidence_text(source.get("evidence_text") or source.get("display_text") or "")
        bbox = self._bbox_from_text(source.get("evidence_text") or "")
        return [{
            "字段": source.get("target_field") or "段落证据",
            "目标表头": source.get("target_header") or "待确认",
            "值/证据": (f"框选区域 {bbox} · " if bbox else "") + text[:220],
            "置信度": f"{float(source.get('confidence') or 0):.2f}",
            "状态": "待抽取",
        }]

    def _set_candidate_rows(self, rows: list[dict[str, Any]]) -> None:
        self._candidate_model.set_rows(rows, ["字段", "目标表头", "值/证据", "置信度", "状态"])
        self.candidate_table.resizeColumnsToContents()
        self._set_candidate_buttons_enabled(False)

    def _candidate_status_label(self, status: str) -> str:
        return {
            "pending": "待确认",
            "confirmed": "已确认",
            "rejected": "已拒绝",
            "deferred": "稍后处理",
        }.get(status, status or "待确认")

    def _set_candidate_buttons_enabled(self, enabled: bool) -> None:
        for btn in (self.confirm_candidate_btn, self.edit_candidate_btn, self.reject_candidate_btn):
            btn.setEnabled(enabled)

    def _show_source_preview(self, source: dict[str, Any]) -> None:
        kind = source.get("item_type")
        page = source.get("page_or_sheet") or source.get("page_or_section") or ""
        title = source.get("title") or source.get("display_text") or source.get("item_id") or ""
        if kind == "table":
            table_id = source.get("candidate_table_id")
            if table_id and self.table_preview_requested:
                try:
                    rows, columns = self.table_preview_requested(table_id)
                    if rows and columns:
                        self._show_rows_as_table(rows, columns)
                        self._set_mapping_preview(source)
                        return
                except Exception:
                    pass
            headers, rows = parse_markdown_table(source.get("caption") or "")
            if headers and rows:
                self._show_markdown_table(headers, rows)
            elif source.get("raw_file_path") and Path(source["raw_file_path"]).exists():
                self._show_image(source["raw_file_path"], f"{title}\n{page}")
            else:
                self.preview_text.setHtml(
                    "<h3>表格线索</h3>"
                    f"<p style='color:#53637a'>{escape(str(page))}</p>"
                    f"<div style='font-size:14px;line-height:1.65'>{highlight_terms(source.get('caption') or title, [])}</div>"
                )
                self.preview_stack.setCurrentWidget(self.preview_text)
        elif kind == "figure":
            image_path = self._image_path_from_source(source)
            if image_path and Path(image_path).exists():
                self._show_image(image_path, f"{title}\n{page}")
            else:
                text = self._clean_evidence_text(source.get("evidence_text") or "")
                self.preview_text.setHtml(
                    "<h3>图表线索</h3>"
                    f"<p style='color:#53637a'>{escape(str(page))}</p>"
                    f"<div style='font-size:14px;line-height:1.65'>{highlight_terms(text, [])}</div>"
                )
                self.preview_stack.setCurrentWidget(self.preview_text)
        else:
            image_path = self._image_path_from_source(source)
            if image_path and Path(image_path).exists():
                self._show_image(image_path, f"用户框选区域\n{title}\n{page}")
                self._set_mapping_preview(source)
                return
            text = self._clean_evidence_text(source.get("evidence_text") or source.get("display_text") or "")
            fields = [f for f in (source.get("target_header"), source.get("target_field")) if f]
            self.preview_text.setHtml(
                "<h3>完整相关段落</h3>"
                f"<p style='color:#53637a'>{escape(str(page))}</p>"
                f"<div style='font-size:15px;line-height:1.75'>{highlight_terms(text, fields)}</div>"
            )
            self.preview_stack.setCurrentWidget(self.preview_text)
        self._set_mapping_preview(source)

    def _set_mapping_preview(self, source: dict[str, Any]) -> None:
        kind = source.get("item_type")
        page = source.get("page_or_sheet") or source.get("page_or_section") or ""
        title = source.get("title") or source.get("display_text") or source.get("item_id") or ""
        text = source.get("caption") or source.get("evidence_text") or source.get("display_text") or ""
        bbox = self._bbox_from_text(text)
        text = self._clean_evidence_text(text)
        fields = [f for f in (source.get("target_header"), source.get("target_field")) if f]
        bbox_html = f"<p style='color:#1468d8'>用户框选区域: {escape(bbox)}</p>" if bbox else ""
        self.mapping_preview.setHtml(
            f"<h3>{escape({'table':'表格','figure':'图表','text':'段落'}.get(kind, '资源'))} · {escape(str(title)[:160])}</h3>"
            f"<p style='color:#53637a'>{escape(str(page))}</p>"
            f"{bbox_html}"
            f"<div style='font-size:14px;line-height:1.65'>{highlight_terms(text, fields)}</div>"
        )

    def _show_rows_as_table(self, rows: list[dict[str, Any]], columns: list[str]) -> None:
        self.preview_table.clear()
        self.preview_table.setRowCount(min(len(rows), 120))
        self.preview_table.setColumnCount(len(columns))
        self.preview_table.setHorizontalHeaderLabels(columns)
        for r, row in enumerate(rows[:120]):
            for c, col in enumerate(columns):
                self.preview_table.setItem(r, c, QTableWidgetItem(str(row.get(col, ""))))
        self.preview_table.resizeColumnsToContents()
        self.preview_stack.setCurrentWidget(self.preview_table)

    def _show_markdown_table(self, headers: list[str], rows: list[list[str]]) -> None:
        self.preview_table.clear()
        self.preview_table.setRowCount(min(len(rows), 120))
        self.preview_table.setColumnCount(len(headers))
        self.preview_table.setHorizontalHeaderLabels(headers)
        for r, row in enumerate(rows[:120]):
            for c, value in enumerate(row[:len(headers)]):
                self.preview_table.setItem(r, c, QTableWidgetItem(value))
        self.preview_table.resizeColumnsToContents()
        self.preview_stack.setCurrentWidget(self.preview_table)

    def _show_image(self, path: str, caption: str = "") -> None:
        pixmap = QPixmap(path)
        if pixmap.isNull():
            self.preview_image.setPixmap(QPixmap())
            self.preview_image.setText(f"图片无法加载\n{path}")
        else:
            self.preview_image.setText("")
            self.preview_image.setPixmap(pixmap.scaled(900, 620, Qt.KeepAspectRatio, Qt.SmoothTransformation))
            self.preview_image.setToolTip(caption)
        self.preview_stack.setCurrentWidget(self.preview_image)

    def _show_empty_preview(self) -> None:
        self.preview_image.setPixmap(QPixmap())
        self.preview_image.setText("选择资源后，中间区域显示表格、图片或完整段落。")
        self.preview_stack.setCurrentWidget(self.preview_image)
        self.mapping_preview.setHtml("<span style='color:#53637a;'>暂无资源。</span>")

    def _image_path_from_source(self, source: dict[str, Any]) -> str:
        if source.get("local_path"):
            return source.get("local_path") or ""
        text = source.get("evidence_text") or ""
        if "\n[PATH]" in text:
            return text.split("\n[PATH]", 1)[1].strip()
        return source.get("raw_file_path") or ""

    def _clean_evidence_text(self, text: str) -> str:
        import re

        text = (text or "").split("\n[PATH]", 1)[0]
        text = re.sub(r"\n?\[USER_SELECTED_PAGE\]\d+", "", text)
        text = re.sub(r"\n?\[BBOX_NORM\][0-9., -]+", "", text)
        return text.strip()

    def _bbox_from_text(self, text: str) -> str:
        import re

        match = re.search(r"\[BBOX_NORM\]([0-9., -]+)", text or "")
        return match.group(1).strip() if match else ""

    def _refresh_mapping_for_current_source(self) -> None:
        source = self._current_source
        if not source:
            self.mapping_source_label.setText("当前资源: —")
            self._show_mapping_rows([])
            return
        self.mapping_source_label.setText(f"当前资源: {self._source_label(source)}")
        table_id = source.get("candidate_table_id")
        rows = [m for m in self._mappings if table_id and m.get("table_id") == table_id]
        self._show_mapping_rows(rows)

    def _show_mapping_rows(self, mappings: list[dict[str, Any]]) -> None:
        self._current_mapping = None
        display = []
        for m in mappings:
            display.append({
                "mapping_id": m.get("mapping_id", ""),
                "source_field": m.get("source_field", ""),
                "target_field": m.get("target_field", ""),
                "source_unit": m.get("source_unit", ""),
                "target_unit": m.get("target_unit", ""),
                "mapping_type": m.get("mapping_type", ""),
                "confidence": f"{float(m.get('confidence') or 0):.2f}",
                "risk_level": m.get("risk_level", ""),
                "requires_review": "是" if m.get("requires_review") else "否",
                "reason": m.get("reason", ""),
            })
        self._visible_mappings = mappings
        self._mapping_model.set_rows(display)
        self.mapping_table.resizeColumnsToContents()
        for btn in (self.confirm_mapping_btn, self.edit_mapping_btn, self.reject_mapping_btn):
            btn.setEnabled(False)

    def _on_mapping_selection_changed(self) -> None:
        selected = self.mapping_table.selectionModel().selectedRows()
        if not selected:
            self._current_mapping = None
            for btn in (self.confirm_mapping_btn, self.edit_mapping_btn, self.reject_mapping_btn):
                btn.setEnabled(False)
            return
        idx = selected[0].row()
        visible = getattr(self, "_visible_mappings", [])
        self._current_mapping = visible[idx] if idx < len(visible) else None
        for btn in (self.confirm_mapping_btn, self.edit_mapping_btn, self.reject_mapping_btn):
            btn.setEnabled(bool(self._current_mapping))

    def _on_candidate_selection_changed(self) -> None:
        selected = self.candidate_table.selectionModel().selectedRows()
        if not selected:
            self._current_extraction_candidate = None
            self._set_candidate_buttons_enabled(False)
            return
        idx = selected[0].row()
        visible = getattr(self, "_visible_extraction_candidates", [])
        self._current_extraction_candidate = visible[idx] if idx < len(visible) else None
        self._set_candidate_buttons_enabled(bool(self._current_extraction_candidate))

    def _run_mapping_for_current_source(self) -> None:
        source = self._current_source
        table_id = source.get("candidate_table_id") if source else None
        if not table_id:
            QMessageBox.information(self, "无法映射", "当前资源还没有解析成候选表。请先在数据抽取步骤处理表格或选择已有候选表。")
            return
        if self.run_mapping_requested:
            self.run_mapping_requested(table_id, self.grouped_mapping_check.isChecked())
            self.step_tabs.setCurrentIndex(3)

    def _run_candidate_or_mapping_for_current_source(self) -> None:
        source = self._current_source
        if not source:
            return
        table_id = source.get("candidate_table_id")
        if table_id:
            self._run_mapping_for_current_source()
            return
        evidence_id = source.get("item_id")
        if evidence_id and self.extract_candidates_requested:
            self.extract_candidates_requested(str(evidence_id))
            self.step_tabs.setCurrentIndex(2)
            return
        QMessageBox.information(self, "无法抽取", "当前资源缺少可抽取的 evidence_id。")

    def _confirm_current_mapping(self) -> None:
        if self._current_mapping and self.confirm_mapping_requested:
            self.confirm_mapping_requested(self._current_mapping["mapping_id"])

    def _reject_current_mapping(self) -> None:
        if self._current_mapping and self.reject_mapping_requested:
            self.reject_mapping_requested(self._current_mapping["mapping_id"])

    def _edit_current_mapping(self) -> None:
        if not self._current_mapping:
            return
        dlg = EditMappingDialog(self._current_mapping, self)
        if dlg.exec() and self.confirm_mapping_requested:
            values = dlg.get_values()
            self.confirm_mapping_requested(
                self._current_mapping["mapping_id"],
                target_field=values["target_field"],
                target_unit=values["target_unit"],
                save_as_rule=values["save_as_rule"],
            )

    def _confirm_current_candidate(self) -> None:
        candidate = self._current_extraction_candidate
        if candidate and self.confirm_candidate_requested:
            self.confirm_candidate_requested(candidate["candidate_id"])

    def _reject_current_candidate(self) -> None:
        candidate = self._current_extraction_candidate
        if candidate and self.reject_candidate_requested:
            self.reject_candidate_requested(candidate["candidate_id"])

    def _edit_current_candidate(self) -> None:
        candidate = self._current_extraction_candidate
        if not candidate:
            return
        dlg = EditExtractionCandidateDialog(candidate, self)
        if dlg.exec() and self.confirm_candidate_requested:
            values = dlg.get_values()
            self.confirm_candidate_requested(
                candidate["candidate_id"],
                target_header=values["target_header"],
                target_field=values["target_field"],
                target_unit=values["target_unit"],
                value=values["value"],
                source_unit=values["source_unit"],
                save_as_rule=values["save_as_rule"],
            )

    def _clear_console(self) -> None:
        self._console_lines.clear()
        self.console.setPlainText("等待工作台任务...")
        self.progress.setValue(0)
        self.task_status.setText("空闲")

    def _clear_layout(self, layout) -> None:
        while layout.count():
            item = layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
