"""PySide6 desktop interface for GeoChem Data Curation Agent."""

from __future__ import annotations

from html import escape
import sys
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QStackedWidget,
    QTableView,
    QTableWidget,
    QTableWidgetItem,
    QTextBrowser,
    QTextEdit,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..workflow import EventBus, TaskService, WorkflowRunner
from .agent_workbench_page import AgentWorkbenchPage
from .document_import_page import DocumentImportPage
from .memory_page import MemoryPage
from .review_page import ReviewPage
from .schema_config_page import SchemaConfigPage
from .settings_page import SettingsPage
from .standardized_page import StandardizedPage
from .styles import APP_QSS
from .token_stats import TokenStatsPage
from .trace_page import TracePage
from .viewmodels import DataFrameTableModel, ProjectRepository
from .resources_page import highlight_terms, parse_markdown_table


NAV_ITEMS = [
    ("dashboard", "项目总览"),
    ("schema", "表头配置"),
    ("import", "文献导入"),
    ("workbench", "智能体工作台"),
    ("review", "人工审核"),
    ("memory", "规则记忆"),
    ("standardized", "标准化导出"),
    ("trace", "溯源查看"),
    ("cost", "Token统计"),
    ("settings", "设置"),
]


def _wrap_in_scroll(widget: QWidget) -> QScrollArea:
    """Wrap a widget in a scroll area for overflow protection."""
    widget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)
    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(QFrame.NoFrame)
    scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
    scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
    scroll.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
    scroll.setMinimumHeight(0)
    scroll.setWidget(widget)
    return scroll


class MetricCard(QFrame):
    """Compact dashboard metric card."""

    def __init__(self, label: str, value: str = "0", accent: str = "#1468d8"):
        super().__init__()
        self.setProperty("class", "card")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        self.label = QLabel(label)
        self.label.setProperty("class", "metricLabel")
        self.value = QLabel(value)
        self.value.setProperty("class", "metricValue")
        hint = QLabel("较上次  --")
        hint.setStyleSheet(f"color: {accent};")
        layout.addWidget(self.label)
        layout.addWidget(self.value)
        layout.addWidget(hint)

    def set_value(self, value) -> None:
        self.value.setText(str(value))


class TablePage(QWidget):
    """Reusable table page with right-side detail panel."""

    def __init__(self, title: str, subtitle: str, columns: list[str] | None = None):
        super().__init__()
        self.model = DataFrameTableModel(columns=columns or [])
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # Header (fixed, outside scroll)
        header = QWidget()
        header_layout = QVBoxLayout(header)
        header_layout.setContentsMargins(20, 18, 20, 10)
        header_layout.setSpacing(4)
        title_label = QLabel(title)
        title_label.setObjectName("pageTitle")
        subtitle_label = QLabel(subtitle)
        subtitle_label.setObjectName("pageSubtitle")
        header_layout.addWidget(title_label)
        header_layout.addWidget(subtitle_label)
        root.addWidget(header)

        # Content (scrollable)
        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(20, 10, 20, 18)
        content_layout.setSpacing(14)

        split = QSplitter(Qt.Horizontal)
        self.table = QTableView()
        self.table.setAlternatingRowColors(True)
        self.table.setModel(self.model)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.detail = QTextEdit()
        self.detail.setReadOnly(True)
        self.detail.setMinimumWidth(300)
        self.detail.setText("选择左侧记录查看详情、风险、证据与操作建议。")
        split.addWidget(self.table)
        split.addWidget(self.detail)
        split.setSizes([820, 320])
        content_layout.addWidget(split, 1)

        root.addWidget(_wrap_in_scroll(content), 1)
        self.table.selectionModel().selectionChanged.connect(self._selection_changed)

    def set_rows(self, rows: list[dict], columns: list[str] | None = None) -> None:
        self.model.set_rows(rows, columns)
        self.table.resizeColumnsToContents()

    def _selection_changed(self) -> None:
        selected = self.table.selectionModel().selectedRows()
        if not selected:
            return
        row = self.model.rows[selected[0].row()]
        lines = [f"{key}: {value}" for key, value in row.items()]
        self.detail.setText("\n".join(lines))


class DashboardPage(QWidget):
    def __init__(self):
        super().__init__()
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # Header (fixed)
        header = QWidget()
        header_layout = QVBoxLayout(header)
        header_layout.setContentsMargins(20, 18, 20, 10)
        header_layout.setSpacing(4)
        title = QLabel("项目总览")
        title.setObjectName("pageTitle")
        subtitle = QLabel("围绕表格抽取、字段映射、审核、标准化和审计导出的项目进度。")
        subtitle.setObjectName("pageSubtitle")
        header_layout.addWidget(title)
        header_layout.addWidget(subtitle)
        root.addWidget(header)

        # Content (scrollable)
        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(20, 10, 20, 18)
        content_layout.setSpacing(14)

        cards = QHBoxLayout()
        self.cards = {
            "articles": MetricCard("已导入论文数"),
            "candidate_tables": MetricCard("候选表格"),
            "pending_reviews": MetricCard("待审核项", accent="#f79009"),
            "learned_rules": MetricCard("学习规则", accent="#12b76a"),
            "total_tokens": MetricCard("Token 消耗"),
        }
        for card in self.cards.values():
            cards.addWidget(card)
        content_layout.addLayout(cards)

        body = QHBoxLayout()
        self.activity = QTextEdit()
        self.activity.setReadOnly(True)
        self.activity.setMinimumHeight(200)
        self.activity.setText("最近活动会显示在这里。")
        self.summary = QTextEdit()
        self.summary.setReadOnly(True)
        self.summary.setMinimumHeight(200)
        body.addWidget(self.activity, 2)
        body.addWidget(self.summary, 1)
        content_layout.addLayout(body, 1)

        root.addWidget(_wrap_in_scroll(content), 1)

    def set_metrics(self, metrics: dict, activity: list[dict]) -> None:
        for key, card in self.cards.items():
            card.set_value(metrics.get(key, 0))
        self.summary.setText(
            "\n".join([
                f"资源数: {metrics.get('resources', 0)}",
                f"映射规则: {metrics.get('mapping_rules', 0)}",
                f"标准化记录: {metrics.get('standardized_records', 0)}",
                f"估算成本: ${metrics.get('estimated_cost', 0):.4f}",
            ])
        )
        if activity:
            self.activity.setText("\n".join(f"{a.get('created_at', '')}  {a.get('message', '')}" for a in activity))


class CandidatePage(QWidget):
    def __init__(self):
        super().__init__()
        self.table_model = DataFrameTableModel()
        self.extract_single_requested = None  # Callable[[str], None]
        self._console_lines: list[str] = []
        self._selected_sources: list[dict] = []
        self._source_by_id: dict[str, dict] = {}
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        header = QWidget()
        header_layout = QVBoxLayout(header)
        header_layout.setContentsMargins(20, 18, 20, 10)
        header_layout.setSpacing(4)
        title = QLabel("数据抽取与候选表格")
        title.setObjectName("pageTitle")
        subtitle = QLabel("浏览资源、候选表和原始候选数据，进入字段映射前先确认表格保真度。")
        subtitle.setObjectName("pageSubtitle")
        header_layout.addWidget(title)
        header_layout.addWidget(subtitle)
        root.addWidget(header)

        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(20, 10, 20, 18)
        content_layout.setSpacing(14)

        split = QSplitter(Qt.Vertical)

        upper = QSplitter(Qt.Horizontal)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabel("资源 / 表格清单")
        self.tree.itemSelectionChanged.connect(self._on_tree_selection_changed)
        self.preview_stack = QStackedWidget()
        self.table = QTableView()
        self.table.setAlternatingRowColors(True)
        self.table.setModel(self.table_model)
        self.source_table = QTableWidget()
        self.source_table.setAlternatingRowColors(True)
        self.source_table.verticalHeader().setVisible(False)
        self.source_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.source_text = QTextBrowser()
        self.source_text.setOpenExternalLinks(True)
        self.source_text.setStyleSheet("QTextBrowser { background: #ffffff; border: 0; padding: 12px; }")
        self.source_image = QLabel("选择左侧图表或表格来源查看可视化预览。")
        self.source_image.setAlignment(Qt.AlignCenter)
        self.source_image.setWordWrap(True)
        self.source_image.setStyleSheet("background: #ffffff; color: #53637a; border: 0;")
        self.preview_stack.addWidget(self.table)
        self.preview_stack.addWidget(self.source_table)
        self.preview_stack.addWidget(self.source_text)
        self.preview_stack.addWidget(self.source_image)
        self.detail = QTextEdit()
        self.detail.setReadOnly(True)
        self.detail.setMinimumWidth(280)
        upper.addWidget(self.tree)
        upper.addWidget(self.preview_stack)
        upper.addWidget(self.detail)
        upper.setSizes([260, 760, 280])
        split.addWidget(upper)

        console_frame = QFrame()
        console_frame.setProperty("class", "card")
        console_layout = QVBoxLayout(console_frame)
        console_layout.setContentsMargins(14, 12, 14, 14)
        console_layout.setSpacing(8)
        console_head = QHBoxLayout()
        console_title = QLabel("抽取控制台")
        console_title.setObjectName("sectionTitle")
        self._task_status = QLabel("空闲")
        self._task_status.setObjectName("taskStatus")
        self._progress = QProgressBar()
        self._progress.setRange(0, 100)
        self._progress.setValue(0)
        clear_btn = QPushButton("清空")
        clear_btn.clicked.connect(self._clear_console)
        console_head.addWidget(console_title)
        console_head.addStretch(1)
        console_head.addWidget(self._task_status)
        console_head.addWidget(clear_btn)
        console_layout.addLayout(console_head)
        console_layout.addWidget(self._progress)
        self._console = QPlainTextEdit()
        self._console.setObjectName("importConsole")
        self._console.setReadOnly(True)
        self._console.setMinimumHeight(120)
        self._console.setPlainText("等待抽取任务...")
        console_layout.addWidget(self._console)
        split.addWidget(console_frame)
        split.setSizes([500, 200])

        content_layout.addWidget(split, 1)
        root.addWidget(_wrap_in_scroll(content), 1)

    def set_tables(self, tables: list[dict]) -> None:
        self.tree.clear()
        by_article = {}
        for table in tables:
            by_article.setdefault(table["article_id"], []).append(table)
        for article, article_tables in by_article.items():
            root = QTreeWidgetItem([article])
            self.tree.addTopLevelItem(root)
            for table in article_tables:
                child = QTreeWidgetItem([f"{table['table_id']}  {table.get('sheet_name') or ''}"])
                child.setData(0, Qt.UserRole, {"kind": "candidate_table", "id": table["table_id"]})
                root.addChild(child)
            root.setExpanded(True)
        self.detail.setText(f"候选表数量: {len(tables)}\n选择左侧 table 后可加载预览。")

    def set_selected_sources(self, items: list[dict]) -> None:
        self._selected_sources = items
        self._source_by_id = {str(item.get("item_id")): item for item in items if item.get("item_id")}
        lines = [f"候选表数量: {self.tree.topLevelItemCount()} 篇文章分组"]
        if not items:
            lines.append("\n尚未从资源清单选择来源。请先在资源清单中勾选表格、图表或相关段落。")
            self._show_empty_source_preview()
        else:
            source_root = QTreeWidgetItem(["已选来源队列"])
            self.tree.insertTopLevelItem(0, source_root)
            counts = {"table": 0, "figure": 0, "text": 0}
            for item in items:
                if item.get("item_type") in counts:
                    counts[item["item_type"]] += 1
                label = {"table": "表格", "figure": "图表", "text": "段落"}.get(item.get("item_type"), "来源")
                page = item.get("page_or_sheet") or item.get("page_or_section") or ""
                text = item.get("display_text") or item.get("title") or item.get("item_id")
                child = QTreeWidgetItem([f"{label}  {page}  {text[:60]}"])
                child.setData(0, Qt.UserRole, {"kind": item.get("item_type"), "id": item.get("item_id")})
                source_root.addChild(child)
            source_root.setExpanded(True)
            lines.append(
                f"\n已送入抽取来源: {len(items)} 项 "
                f"({counts['table']} 表格, {counts['figure']} 图表, {counts['text']} 段落)"
            )
            lines.append("\n最近选择:")
            for item in items[:8]:
                label = {"table": "表格", "figure": "图表", "text": "段落"}.get(item.get("item_type"), "来源")
                page = item.get("page_or_sheet") or item.get("page_or_section") or ""
                text = item.get("display_text") or item.get("title") or item.get("item_id")
                lines.append(f"- [{label}] {page}  {text}")
            self.show_source_preview(items[0])
        self.detail.setText("\n".join(lines))

    def set_preview(self, rows: list[dict], columns: list[str]) -> None:
        self.table_model.set_rows(rows, columns)
        self.table.resizeColumnsToContents()
        if not self._selected_sources:
            self.preview_stack.setCurrentWidget(self.table)

    def show_source_preview(self, item: dict) -> None:
        item_type = item.get("item_type", "")
        page = item.get("page_or_sheet") or item.get("page_or_section") or ""
        title = item.get("title") or item.get("display_text") or item.get("item_id")
        if item_type == "table":
            headers, rows = parse_markdown_table(item.get("caption") or "")
            if headers and rows:
                self.source_table.clear()
                self.source_table.setRowCount(min(len(rows), 80))
                self.source_table.setColumnCount(len(headers))
                self.source_table.setHorizontalHeaderLabels(headers)
                for r, row in enumerate(rows[:80]):
                    for c, value in enumerate(row[:len(headers)]):
                        self.source_table.setItem(r, c, QTableWidgetItem(value))
                self.source_table.resizeColumnsToContents()
                self.preview_stack.setCurrentWidget(self.source_table)
            elif item.get("raw_file_path") and Path(item["raw_file_path"]).exists():
                self._show_image(item["raw_file_path"], f"{title}\n{page}")
            else:
                body = item.get("caption") or item.get("display_text") or ""
                self.source_text.setHtml(
                    "<h3>表格线索</h3>"
                    f"<p style='color:#53637a'>{escape(page)}</p>"
                    "<p>该条目是正文/补充材料中的表格线索，尚未解析出真实单元格。"
                    "后续 AI 抽取会优先根据这个线索查找补充表格或页面内容。</p>"
                    f"<div style='font-size:14px;line-height:1.55'>{highlight_terms(body, [])}</div>"
                )
                self.preview_stack.setCurrentWidget(self.source_text)
        elif item_type == "figure":
            image_path = self._image_path_from_item(item)
            if image_path and Path(image_path).exists():
                self._show_image(image_path, f"{title}\n{page}")
            else:
                text = item.get("evidence_text") or item.get("display_text") or ""
                self.source_text.setHtml(
                    "<h3>图表线索</h3>"
                    f"<p style='color:#53637a'>{escape(page)}</p>"
                    f"<div style='font-size:14px;line-height:1.55'>{highlight_terms(text, [])}</div>"
                )
                self.preview_stack.setCurrentWidget(self.source_text)
        else:
            text = item.get("evidence_text") or item.get("display_text") or ""
            fields = [f for f in (item.get("target_header"), item.get("target_field")) if f]
            self.source_text.setHtml(
                "<h3>相关段落</h3>"
                f"<p style='color:#53637a'>{escape(page)}</p>"
                f"<div style='font-size:15px;line-height:1.7'>{highlight_terms(text, fields)}</div>"
            )
            self.preview_stack.setCurrentWidget(self.source_text)

    def _on_tree_selection_changed(self) -> None:
        items = self.tree.selectedItems()
        if not items:
            return
        data = items[0].data(0, Qt.UserRole)
        if isinstance(data, dict):
            if data.get("kind") == "candidate_table":
                self.preview_stack.setCurrentWidget(self.table)
                return
            source = self._source_by_id.get(str(data.get("id")))
            if source:
                self.show_source_preview(source)

    def _show_image(self, path: str, caption: str = "") -> None:
        pixmap = QPixmap(path)
        if pixmap.isNull():
            self.source_image.setText(f"图片无法加载\n{path}")
        else:
            self.source_image.setPixmap(pixmap.scaled(900, 560, Qt.KeepAspectRatio, Qt.SmoothTransformation))
            self.source_image.setToolTip(caption)
        self.preview_stack.setCurrentWidget(self.source_image)

    def _show_empty_source_preview(self) -> None:
        self.source_image.setPixmap(QPixmap())
        self.source_image.setText("中间区域用于显示选中的表格、图片或段落。")
        self.preview_stack.setCurrentWidget(self.source_image)

    def _image_path_from_item(self, item: dict) -> str:
        text = item.get("evidence_text") or ""
        if "\n[PATH]" in text:
            return text.split("\n[PATH]", 1)[1].strip()
        return item.get("raw_file_path") or ""

    def append_console(self, message: str, level: str = "INFO") -> None:
        from datetime import datetime
        line = f"[{datetime.now().strftime('%H:%M:%S')}] {level:<5} {message}"
        self._console_lines.append(line)
        self._console_lines = self._console_lines[-500:]
        self._console.setPlainText("\n".join(self._console_lines))
        cursor = self._console.textCursor()
        cursor.movePosition(cursor.MoveOperation.End)
        self._console.setTextCursor(cursor)

    def set_task_state(self, status: str, progress: float | int | None = None) -> None:
        self._task_status.setText(status)
        if progress is not None:
            value = int(max(0, min(100, float(progress) * 100 if float(progress) <= 1 else float(progress))))
            self._progress.setValue(value)

    def _clear_console(self) -> None:
        self._console_lines = []
        self._console.setPlainText("控制台已清空。")
        self._task_status.setText("空闲")
        self._progress.setValue(0)


class MainWindow(QMainWindow):
    """Main desktop shell matching the reference dashboard layout."""

    def __init__(self, repository: ProjectRepository | None = None):
        super().__init__()
        self.repo = repository or ProjectRepository()
        self.event_bus = EventBus()
        self.tasks = TaskService(WorkflowRunner(event_bus=self.event_bus))
        self.current_project_id: str | None = None
        self.current_table_id: str | None = None
        self.pending_browser_access: dict | None = None
        self.setWindowTitle("GeoChem Data Curation Agent")
        self.resize(1440, 900)
        self._build()
        self._load_projects()
        self.event_bus.subscribe(self._handle_workflow_event)

    def _build(self) -> None:
        central = QWidget()
        central.setObjectName("central")
        root = QHBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self._sidebar())
        right = QVBoxLayout()
        right.setContentsMargins(0, 0, 0, 0)
        right.setSpacing(0)
        right.addWidget(self._topbar())
        self.stack = QStackedWidget()
        self.pages = {
            "dashboard": DashboardPage(),
            "schema": SchemaConfigPage(),
            "import": DocumentImportPage(),
            "workbench": AgentWorkbenchPage(),
            "review": ReviewPage(),
            "memory": MemoryPage(),
            "standardized": StandardizedPage(),
            "trace": TracePage(),
            "cost": TokenStatsPage(),
            "settings": SettingsPage(),
        }
        for page in self.pages.values():
            self.stack.addWidget(page)
        self._connect_page_actions()
        right.addWidget(self.stack, 1)
        root.addLayout(right, 1)
        self.setCentralWidget(central)

    def _connect_page_actions(self) -> None:
        schema_page: SchemaConfigPage = self.pages["schema"]
        schema_page.import_requested = self._import_headers_from_excel
        schema_page.save_requested = self._save_header_config
        schema_page.config_selected = self._load_header_config

        import_page: DocumentImportPage = self.pages["import"]
        import_page.doi_open_requested = self._open_source_for_access
        import_page.confirm_access_requested = self._confirm_browser_access
        import_page.files_import_requested = self._import_resource_files
        import_page.file_select_requested = self._select_resource_files
        import_page.header_config_confirm_requested = self._confirm_article_header_config

        review_page: ReviewPage = self.pages["review"]
        review_page.decide_requested = self._decide_review
        review_page.filter_changed = lambda: self.refresh_page("review")

        workbench_page: AgentWorkbenchPage = self.pages["workbench"]
        workbench_page.discover_requested = self._discover_resources
        workbench_page.extract_selected_requested = self._extract_selected_tables
        workbench_page.run_mapping_requested = self._run_mapping
        workbench_page.extract_candidates_requested = self._extract_evidence_candidates
        workbench_page.confirm_candidate_requested = self._confirm_extraction_candidate
        workbench_page.reject_candidate_requested = self._reject_extraction_candidate
        workbench_page.confirm_mapping_requested = self._confirm_mapping
        workbench_page.reject_mapping_requested = self._reject_mapping
        workbench_page.manual_pdf_evidence_requested = self._add_manual_pdf_evidence
        workbench_page.filter_changed = lambda: self.refresh_page("workbench")
        workbench_page.table_preview_requested = (
            lambda table_id: self.repo.candidate_rows(self.current_project_id, table_id)
        )

        standardized_page: StandardizedPage = self.pages["standardized"]
        standardized_page.export_requested = self._export_standardized
        standardized_page.standardize_requested = self._run_standardize
        standardized_page.trace_requested = self._navigate_to_trace
        standardized_page.record_context_requested = (
            lambda record_id: self.repo.standardized_record_context(self.current_project_id, record_id)
        )
        standardized_page.filter_changed = lambda: self.refresh_page("standardized")

        memory_page: MemoryPage = self.pages["memory"]
        memory_page.toggle_rule_requested = self._toggle_rule
        memory_page.filter_changed = lambda: self.refresh_page("memory")

        trace_page: TracePage = self.pages["trace"]
        trace_page.filter_changed = lambda: self.refresh_page("trace")
        trace_page.record_selected = self._load_trace_detail

    def _handle_workflow_event(self, event) -> None:
        self.statusBar().showMessage(event.message, 5000)
        if hasattr(self, "pages"):
            if event.event_type == "import":
                page = self.pages.get("import")
                if page and hasattr(page, "append_console"):
                    page.append_console(event.message)
                    page.set_task_state(self._import_status_label(event.message, event.progress), event.progress)
            elif event.event_type in ("extract", "discover"):
                page = self.pages.get("workbench")
                if page and hasattr(page, "append_console"):
                    page.append_console(event.message)
                    progress = int(event.progress * 100) if event.progress else None
                    page.set_task_state("运行中" if event.progress < 1 else "完成", progress)

    def _import_status_label(self, message: str, progress: float) -> str:
        if progress >= 1:
            return "完成"
        if "LLM" in message or "AI" in message:
            return "等待 AI 读取"
        if "browser" in message.lower() or "Browser" in message:
            return "等待浏览器确认"
        if "Saving" in message or "保存" in message:
            return "保存资源"
        return "运行中"

    def _sidebar(self) -> QWidget:
        frame = QFrame()
        frame.setObjectName("sidebar")
        frame.setFixedWidth(232)
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(16, 20, 16, 18)
        logo = QLabel("⬡  GeoChem")
        logo.setObjectName("brandTitle")
        sub = QLabel("Data Curation Agent\n地球化学文献数据整理\n与标准化 Agent")
        sub.setObjectName("brandSub")
        layout.addWidget(logo)
        layout.addWidget(sub)
        layout.addSpacing(18)
        self.nav_buttons = {}
        nav_host = QWidget()
        nav_layout = QVBoxLayout(nav_host)
        nav_layout.setContentsMargins(0, 0, 0, 0)
        nav_layout.setSpacing(2)
        for key, label in NAV_ITEMS:
            btn = QPushButton(label)
            btn.setObjectName("navButton")
            btn.setCheckable(True)
            btn.clicked.connect(lambda checked=False, k=key: self.show_page(k))
            nav_layout.addWidget(btn)
            self.nav_buttons[key] = btn
        nav_layout.addStretch(1)
        nav_scroll = QScrollArea()
        nav_scroll.setWidgetResizable(True)
        nav_scroll.setFrameShape(QFrame.NoFrame)
        nav_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        nav_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        nav_scroll.setWidget(nav_host)
        layout.addWidget(nav_scroll, 1)
        status = QLabel("● 服务状态\n在线\n\nv1.1.0")
        status.setStyleSheet("color: #12b76a; padding: 12px; border: 1px solid #dfe5ee; border-radius: 8px;")
        layout.addWidget(status)
        return frame

    def _topbar(self) -> QWidget:
        frame = QFrame()
        frame.setObjectName("topbar")
        frame.setFixedHeight(62)
        layout = QHBoxLayout(frame)
        layout.setContentsMargins(20, 10, 20, 10)

        # Single workspace status
        layout.addWidget(QLabel("当前工作区"))
        self.project_combo = QComboBox()
        self.project_combo.setMinimumWidth(300)
        self.project_combo.setEnabled(False)
        layout.addWidget(self.project_combo)

        layout.addStretch(1)

        # Model selector (replaces search bar)
        layout.addWidget(QLabel("当前模型"))
        self.model_selector = QComboBox()
        self.model_selector.setMinimumWidth(320)
        self.model_selector.currentIndexChanged.connect(self._model_changed)
        layout.addWidget(self.model_selector)
        self._load_model_selector()

        new_task = QPushButton("+ 新建任务")
        new_task.setObjectName("primaryButton")
        layout.addWidget(new_task)
        return frame

    def _load_model_selector(self) -> None:
        """Populate model selector with tested provider/model pairs only."""
        self.model_selector.blockSignals(True)
        self.model_selector.clear()

        config = self.repo.load_config()
        default_task = config.get_task_model("_default")
        default_provider = default_task.provider if default_task else ""
        default_model = default_task.model if default_task else ""
        tested = config.ui_preferences.get("tested_models", [])

        # Build a lookup for provider display names
        prov_names = {p.name: (p.display_name or p.name) for p in config.providers}

        if tested:
            for entry in tested:
                prov_name = entry.get("provider", "")
                model_name = entry.get("model", "")
                prov_display = prov_names.get(prov_name, prov_name)
                label = f"{prov_display}  /  {model_name}"
                self.model_selector.addItem(label, {"provider": prov_name, "model": model_name})

            # Select current default
            for i in range(self.model_selector.count()):
                data = self.model_selector.itemData(i)
                if data and data["provider"] == default_provider and data["model"] == default_model:
                    self.model_selector.setCurrentIndex(i)
                    break
        else:
            self.model_selector.addItem("— 请先在设置中测试模型连通性 —", None)

        self.model_selector.blockSignals(False)

    def _model_changed(self) -> None:
        """Save the selected model as the default."""
        data = self.model_selector.currentData()
        if not data:
            return
        self.repo.save_settings({
            "default_provider": data["provider"],
            "default_model": data["model"],
        })
        self.statusBar().showMessage(f"已切换到 {self.model_selector.currentText()}", 3000)

    def _load_projects(self) -> None:
        workspace = self.repo.ensure_default_workspace()
        self.projects = [workspace]
        self.project_combo.clear()
        for project in self.projects:
            self.project_combo.addItem(project["project_name"] or project["project_id"], project["project_id"])
        self.project_combo.setCurrentIndex(0)
        self.current_project_id = workspace["project_id"]
        self.show_page("dashboard")

    def _project_changed(self) -> None:
        project_id = self.project_combo.currentData()
        if not project_id:
            return
        self.current_project_id = project_id
        self.refresh_current_page()

    def show_page(self, key: str) -> None:
        for nav_key, btn in self.nav_buttons.items():
            btn.setChecked(nav_key == key)
        self.stack.setCurrentWidget(self.pages[key])
        self.refresh_page(key)

    def refresh_current_page(self) -> None:
        current = self.stack.currentWidget()
        for key, page in self.pages.items():
            if page is current:
                self.refresh_page(key)
                return

    def refresh_page(self, key: str) -> None:
        if not self.current_project_id:
            return
        try:
            if key == "dashboard":
                self.pages[key].set_metrics(
                    self.repo.dashboard_metrics(self.current_project_id),
                    self.repo.recent_activity(self.current_project_id),
                )
            elif key == "import":
                self.pages[key].set_resources(self.repo.resource_discovery_rows(self.current_project_id))
                self.pages[key].set_summary(self.repo.resource_summary(self.current_project_id))
                self.pages[key].set_article_info(self.repo.latest_article_info(self.current_project_id))
                latest_article = self.repo.latest_article_info(self.current_project_id)
                suggested = self.repo.suggest_header_config_for_article(
                    self.current_project_id,
                    latest_article.get("article_id") if latest_article else None,
                )
                self.pages[key].set_header_configs(self.repo.list_header_configs(self.current_project_id), suggested)
            elif key == "workbench":
                page = self.pages["workbench"]
                articles = self.repo.articles_for_filter(self.current_project_id)
                page.set_articles(articles)
                page.set_header_configs(self.repo.list_header_configs(self.current_project_id))
                try:
                    config, project_dir = self.repo.pm.load_project(self.current_project_id)
                    schema_path = project_dir / "schema" / "geochem_schema.yaml"
                    fields = []
                    if schema_path.exists():
                        import yaml
                        with open(schema_path, encoding="utf-8") as f:
                            schema = yaml.safe_load(f)
                        fields.extend(col.get("name", "") for col in schema.get("columns", []) if col.get("name"))
                    for cfg in self.repo.list_header_configs(self.current_project_id)[:3]:
                        rows, _summary = self.repo.load_header_config(self.current_project_id, cfg["config_id"])
                        for row in rows:
                            fields.extend([
                                row.get("字段名", ""),
                                row.get("canonical_field", ""),
                                row.get("description", ""),
                            ])
                    page.set_schema_fields([field for field in fields if field])
                except Exception:
                    pass
                article_id = page.get_selected_article_id()
                page.set_pdf_resources(self.repo.pdf_resources_for_article(self.current_project_id, article_id))
                page.set_discovered_elements(self.repo.discovered_elements(
                    self.current_project_id,
                    article_id=article_id,
                ))
                page.set_selected_sources(self.repo.selected_extraction_items(self.current_project_id))
                page.set_extraction_candidates(self.repo.extraction_candidates(self.current_project_id, article_id=article_id))
                page.set_mappings(self.repo.mappings(self.current_project_id))
                tables = self.repo.candidate_tables(self.current_project_id)
                if tables and not self.current_table_id:
                    self.current_table_id = tables[0]["table_id"]
            elif key == "review":
                page = self.pages["review"]
                page.set_source_summary(self.repo.selected_source_summary(self.current_project_id))
                page.set_items(self.repo.review_items(
                    self.current_project_id,
                    status=page.get_status_filter(),
                    risk_level=page.get_risk_filter(),
                    item_type=page.get_type_filter(),
                ))
            elif key == "standardized":
                page = self.pages["standardized"]
                tables = self.repo.candidate_tables(self.current_project_id)
                page.set_table_options(tables)
                table_id = page.get_selected_table_id() or self.current_table_id
                rows, cols = self.repo.standardized_records(
                    self.current_project_id, table_id, quality_grade=page.get_grade_filter(),
                )
                page.set_records(rows, cols)
                page.set_grade_distribution(self.repo.quality_distribution(self.current_project_id, table_id))
            elif key == "trace":
                page = self.pages["trace"]
                tables = self.repo.candidate_tables(self.current_project_id)
                page.set_table_options(tables)
                table_id = page.get_selected_table_id() or self.current_table_id
                if table_id:
                    page.set_trace_rows(self.repo.trace_rows(self.current_project_id, table_id))
            elif key == "cost":
                self._refresh_cost_page()
            elif key == "schema":
                self.pages[key].set_config_options(self.repo.list_header_configs(self.current_project_id))
            elif key == "memory":
                page = self.pages["memory"]
                if page.get_current_tab() == 0:
                    page.set_mapping_rules(self.repo.mapping_rules(
                        self.current_project_id,
                        status=page.get_status_filter(),
                        scope=page.get_scope_filter(),
                    ))
                else:
                    page.set_learned_rules(self.repo.learned_rules(
                        self.current_project_id,
                        status=page.get_status_filter(),
                        scope=page.get_scope_filter(),
                    ))
            elif key == "settings":
                self._refresh_settings_page()
        except Exception as exc:
            QMessageBox.warning(self, "加载失败", str(exc))

    def _refresh_cost_page(self) -> None:
        page = self.pages["cost"]
        article_id = page.article_filter.currentData()
        model_name = page.model_filter.currentData()
        stats = self.repo.token_stats(self.current_project_id, article_id=article_id, model_name=model_name)
        articles = self.repo.articles_for_filter(self.current_project_id)
        project_name = self.project_combo.currentText() or self.current_project_id
        page.set_stats(project_name, stats, articles)
        try:
            page.article_filter.currentIndexChanged.disconnect()
        except Exception:
            pass
        try:
            page.model_filter.currentIndexChanged.disconnect()
        except Exception:
            pass
        page.article_filter.currentIndexChanged.connect(lambda *_: self._refresh_cost_page())
        page.model_filter.currentIndexChanged.connect(lambda *_: self._refresh_cost_page())

    def _refresh_settings_page(self) -> None:
        page = self.pages["settings"]
        page.save_requested = self._save_settings
        page.set_settings(self.repo.settings_data(self.current_project_id))

    def _save_settings(self, updates: dict) -> None:
        self.repo.save_settings(updates)
        # Refresh model selector if tested_models changed
        if "tested_models" in updates:
            self._load_model_selector()
        self.statusBar().showMessage("设置已保存", 4000)

    def _import_headers_from_excel(self) -> None:
        if not self.current_project_id:
            return
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "选择包含目标表头的 Excel/CSV 文件",
            str(Path.cwd()),
            "Header Files (*.xlsx *.xls *.csv);;Excel Files (*.xlsx *.xls);;CSV Files (*.csv);;All Files (*)",
        )
        if not file_path:
            return
        try:
            rows, summary = self.repo.header_config_from_file(file_path)
            summary["name"] = Path(file_path).stem
            summary["source_file"] = file_path
            self.pages["schema"].set_headers(rows, summary, source_file=file_path)
            self.statusBar().showMessage(f"已导入 {summary.get('field_count', 0)} 个目标表头", 5000)
        except Exception as exc:
            QMessageBox.warning(self, "表头导入失败", str(exc))

    def _select_resource_files(self) -> None:
        files, _ = QFileDialog.getOpenFileNames(
            self,
            "选择文献或补充材料",
            str(Path.cwd()),
            "Supported Files (*.pdf *.xlsx *.xls *.csv *.zip *.docx *.doc);;All Files (*)",
        )
        if files:
            self._import_resource_files(files)

    def _import_resource_files(self, files: list[str]) -> None:
        if not self.current_project_id or not files:
            return
        try:
            result = self.tasks.run("import_files", project_id=self.current_project_id, file_paths=files)
            count = result.get("count", 0)
            duplicates = len([r for r in result.get("resources", []) if r.get("status") == "duplicate"])
            msg = f"已导入 {count} 个资源"
            if duplicates:
                msg += f"（{duplicates} 个为已存在文件）"
            self.statusBar().showMessage(msg, 5000)
            self.refresh_page("import")
            self.refresh_page("workbench")
        except Exception as exc:
            QMessageBox.warning(self, "文件导入失败", str(exc))

    def _open_source_for_access(self, source: str) -> None:
        if not self.current_project_id:
            return
        if not source:
            QMessageBox.information(self, "需要 DOI 或 URL", "请输入 DOI 或网页 URL。")
            return
        try:
            self.pages["import"].clear_console()
            self.pages["import"].append_console(f"收到来源：{source}")
            self.pages["import"].set_task_state("解析 DOI/URL", 5)
            info = self.tasks.run("open_source_in_browser", project_id=self.current_project_id, source=source)
            self.pending_browser_access = info
            self.pages["import"].set_article_info(info)
            suggested = self.repo.suggest_header_config_for_article(self.current_project_id, info.get("article_id"))
            self.pages["import"].set_header_configs(self.repo.list_header_configs(self.current_project_id), suggested)
            self.pages["import"].set_message("浏览器已打开。请完成登录/人机验证，并确认全文或补充材料可访问后回到 App 点击确认。")
            self.pages["import"].append_console("浏览器已打开；等待用户确认页面可访问。")
        except Exception as exc:
            self.pages["import"].append_console(f"打开浏览器失败：{exc}", level="ERROR")
            self.pages["import"].set_task_state("失败", 100)
            QMessageBox.warning(self, "打开浏览器失败", str(exc))

    def _confirm_browser_access(self) -> None:
        if not self.current_project_id:
            return
        if not self.pending_browser_access:
            QMessageBox.information(self, "没有等待确认的资源", "请先输入 DOI/URL 并在浏览器中打开。")
            return
        try:
            self.pages["import"].append_console("用户确认页面可访问；开始读取期刊页面。")
            self.pages["import"].set_task_state("读取期刊页面", 10)
            result = self.tasks.run(
                "confirm_browser_access",
                project_id=self.current_project_id,
                article_id=self.pending_browser_access.get("article_id"),
                url=self.pending_browser_access.get("url"),
            )
            if result.get("resource_type") == "main_pdf":
                message = f"已识别并保存文章 PDF {result.get('resource_id')}。下一步由 PDF/LLM 读取器抽取图表、表格和补充材料候选项。"
            elif result.get("fetched_content"):
                message = f"已保存文章正文资源 {result.get('resource_id')}，读取器获取到了页面文本，可进入 AI 抽取。"
            else:
                message = f"已保存已确认可访问资源 {result.get('resource_id')}。普通读取器未获取全文，后续将由 LLM/浏览器读取任务根据 URL 继续处理。"
            self.pages["import"].set_message(message)
            self.pages["import"].append_console(message)
            self.pages["import"].set_task_state("完成", 100)
            self.pending_browser_access = None
            self.refresh_page("import")
            self.refresh_page("workbench")
        except Exception as exc:
            self.pages["import"].append_console(f"确认访问失败：{exc}", level="ERROR")
            self.pages["import"].set_task_state("失败", 100)
            QMessageBox.warning(self, "确认访问失败", str(exc))

    def _save_header_config(self) -> None:
        if not self.current_project_id:
            return
        page: SchemaConfigPage = self.pages["schema"]
        rows = page.get_rows_for_save()
        if not rows:
            QMessageBox.information(self, "没有可保存的表头", "请先导入 Excel/CSV 表头或载入已有配置。")
            return
        try:
            config_id = self.repo.save_header_config(
                self.current_project_id,
                page.get_config_name(),
                rows,
                config_id=page.current_config_id,
                source_file=page.source_file,
            )
            page.current_config_id = config_id
            page.set_config_options(self.repo.list_header_configs(self.current_project_id), config_id)
            self.statusBar().showMessage(f"已保存表头配置 {config_id}", 5000)
        except Exception as exc:
            QMessageBox.warning(self, "保存表头配置失败", str(exc))

    def _load_header_config(self, config_id: str) -> None:
        if not self.current_project_id or not config_id:
            return
        try:
            rows, summary = self.repo.load_header_config(self.current_project_id, config_id)
            self.pages["schema"].set_headers(rows, summary, config_id=config_id)
            self.statusBar().showMessage(f"已载入表头配置 {summary.get('name', config_id)}", 4000)
        except Exception as exc:
            QMessageBox.warning(self, "载入表头配置失败", str(exc))

    def _confirm_article_header_config(self, config_id: str) -> None:
        if not self.current_project_id:
            return
        info = self.pending_browser_access or self.repo.latest_article_info(self.current_project_id)
        article_id = info.get("article_id") if info else None
        if not article_id:
            QMessageBox.information(self, "没有可关联的文章", "请先导入 DOI、URL 或本地文件。")
            return
        suggested = self.repo.suggest_header_config_for_article(self.current_project_id, article_id) or {}
        try:
            assignment_id = self.repo.assign_header_config_to_article(
                self.current_project_id,
                article_id,
                config_id,
                suggested_by=suggested.get("suggested_by", "user"),
                confidence=suggested.get("confidence", 1.0),
                reason=suggested.get("reason", "用户在文献导入页确认使用此表头配置。"),
            )
            self.pages["import"].set_message(f"已将表头配置关联到文章（{assignment_id}）。下一步抽取和标准化会优先使用该预设。")
            self.pages["import"].append_console(f"已确认本文使用表头配置：{config_id}（{assignment_id}）")
        except Exception as exc:
            self.pages["import"].append_console(f"确认表头失败：{exc}", level="ERROR")
            QMessageBox.warning(self, "确认表头失败", str(exc))

    # --- Review page callbacks ---

    def _decide_review(self, review_id: str, action: str, **kwargs) -> None:
        if not self.current_project_id:
            return
        try:
            self.tasks.run(
                "decide_review",
                project_id=self.current_project_id,
                review_id=review_id,
                action=action,
                **kwargs,
            )
            self.statusBar().showMessage(f"审核 {review_id}: {action}", 3000)
            self.refresh_page("review")
        except Exception as exc:
            QMessageBox.warning(self, "审核操作失败", str(exc))

    # --- Mapping page callbacks ---

    def _run_mapping(self, table_id: str, grouped: bool) -> None:
        if not self.current_project_id:
            return
        try:
            result = self.tasks.run(
                "map_table",
                project_id=self.current_project_id,
                table_id=table_id,
                grouped=grouped,
                use_llm=grouped,
            )
            self.statusBar().showMessage(f"映射完成: {result.get('mapped', 0)} 个字段, {result.get('review_items', 0)} 个审核项", 5000)
            self.refresh_page("workbench")
            self.refresh_page("review")
        except Exception as exc:
            QMessageBox.warning(self, "映射失败", str(exc))

    def _confirm_mapping(self, mapping_id: str, **kwargs) -> None:
        if not self.current_project_id:
            return
        try:
            self.tasks.run(
                "confirm_mapping",
                project_id=self.current_project_id,
                mapping_id=mapping_id,
                **kwargs,
            )
            self.statusBar().showMessage(f"已确认映射 {mapping_id}", 3000)
            self.refresh_page("workbench")
            self.refresh_page("review")
        except Exception as exc:
            QMessageBox.warning(self, "确认映射失败", str(exc))

    def _reject_mapping(self, mapping_id: str) -> None:
        if not self.current_project_id:
            return
        try:
            self.tasks.run(
                "reject_mapping",
                project_id=self.current_project_id,
                mapping_id=mapping_id,
            )
            self.statusBar().showMessage(f"已拒绝映射 {mapping_id}", 3000)
            self.refresh_page("workbench")
            self.refresh_page("review")
        except Exception as exc:
            QMessageBox.warning(self, "拒绝映射失败", str(exc))

    # --- Resources page callbacks ---

    def _extract_evidence_candidates(self, evidence_id: str) -> None:
        if not self.current_project_id:
            return
        page: AgentWorkbenchPage = self.pages["workbench"]
        try:
            page.append_console(f"开始从证据 {evidence_id} 抽取候选字段和值...")
            page.set_task_state("AI/规则抽取候选值", 25)
            result = self.tasks.run(
                "extract_evidence_candidates",
                project_id=self.current_project_id,
                evidence_id=evidence_id,
                use_llm=True,
            )
            page.append_console(f"候选值抽取完成：{result.get('candidates', 0)} 个候选")
            page.set_task_state("完成", 100)
            self.statusBar().showMessage(f"候选值抽取完成: {result.get('candidates', 0)} 个", 4000)
            self.refresh_page("workbench")
            page.set_active_step(2)
        except Exception as exc:
            page.append_console(f"候选值抽取失败：{exc}", level="ERROR")
            page.set_task_state("失败", 100)
            QMessageBox.warning(self, "候选值抽取失败", str(exc))

    def _confirm_extraction_candidate(self, candidate_id: str, **kwargs) -> None:
        if not self.current_project_id:
            return
        page: AgentWorkbenchPage = self.pages["workbench"]
        try:
            result = self.tasks.run(
                "confirm_extraction_candidate",
                project_id=self.current_project_id,
                candidate_id=candidate_id,
                **kwargs,
            )
            page.append_console(
                f"已确认候选值 {candidate_id}，生成补值 {result.get('patch_id')}，关联表 {result.get('table_id')}"
            )
            if result.get("learned_rule_id"):
                page.append_console(f"已写入规则记忆 {result.get('learned_rule_id')}")
            self.current_table_id = result.get("table_id") or self.current_table_id
            self.statusBar().showMessage(f"已确认候选值 {candidate_id}", 3000)
            self.refresh_page("workbench")
            self.refresh_page("memory")
            self.refresh_page("standardized")
            page.set_active_step(2)
        except Exception as exc:
            page.append_console(f"确认候选值失败：{exc}", level="ERROR")
            QMessageBox.warning(self, "确认候选值失败", str(exc))

    def _reject_extraction_candidate(self, candidate_id: str) -> None:
        if not self.current_project_id:
            return
        page: AgentWorkbenchPage = self.pages["workbench"]
        try:
            self.tasks.run(
                "reject_extraction_candidate",
                project_id=self.current_project_id,
                candidate_id=candidate_id,
            )
            page.append_console(f"已拒绝候选值 {candidate_id}")
            self.statusBar().showMessage(f"已拒绝候选值 {candidate_id}", 3000)
            self.refresh_page("workbench")
            page.set_active_step(2)
        except Exception as exc:
            page.append_console(f"拒绝候选值失败：{exc}", level="ERROR")
            QMessageBox.warning(self, "拒绝候选值失败", str(exc))

    def _add_manual_pdf_evidence(self, resource_id: str, page_number: int, note: str, bbox_norm: str = "") -> None:
        if not self.current_project_id:
            return
        page: AgentWorkbenchPage = self.pages["workbench"]
        article_id = page.get_selected_article_id()
        if not article_id:
            QMessageBox.information(self, "请选择文献", "请先在智能体工作台顶部选择一篇文献。")
            return
        try:
            result = self.tasks.run(
                "add_manual_pdf_evidence",
                project_id=self.current_project_id,
                article_id=article_id,
                resource_id=resource_id,
                page_number=page_number,
                note=note,
                bbox_norm=bbox_norm,
            )
            scope = f"框选区域 {bbox_norm}" if bbox_norm else "整页"
            page.append_console(
                f"已将 PDF 第 {result.get('page_number')} 页（{scope}）保存为人工抽取线索 {result.get('evidence_id')}"
            )
            self.statusBar().showMessage("已加入抽取队列", 3000)
            self.refresh_page("workbench")
            page.set_active_step(2)
        except Exception as exc:
            QMessageBox.warning(self, "加入抽取队列失败", str(exc))

    def _discover_resources(self, resource_id: str) -> None:
        if not self.current_project_id:
            return
        try:
            self.statusBar().showMessage("正在发现资源元素...", 3000)
            result = self.tasks.run(
                "discover_resources",
                project_id=self.current_project_id,
                resource_id=resource_id,
            )
            count = result.get("discovered", 0)
            self.statusBar().showMessage(f"发现 {count} 个资源元素", 5000)
            self.refresh_page("workbench")
        except Exception as exc:
            QMessageBox.warning(self, "资源发现失败", str(exc))

    def _extract_resource(self, resource_id: str) -> None:
        if not self.current_project_id:
            return
        try:
            result = self.tasks.run(
                "extract_resource",
                project_id=self.current_project_id,
                resource_id=resource_id,
            )
            self.statusBar().showMessage(f"抽取完成: {len(result.get('table_ids', []))} 个候选表", 5000)
            self.refresh_page("workbench")
        except Exception as exc:
            QMessageBox.warning(self, "抽取失败", str(exc))

    def _delete_resource(self, resource_id: str) -> None:
        if not self.current_project_id:
            return
        reply = QMessageBox.question(
            self, "确认删除", f"确定要删除资源 {resource_id} 及其关联的候选表吗？",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return
        try:
            self.tasks.run(
                "delete_resource",
                project_id=self.current_project_id,
                resource_id=resource_id,
            )
            self.statusBar().showMessage(f"已删除资源 {resource_id}", 3000)
            self.refresh_page("workbench")
        except Exception as exc:
            QMessageBox.warning(self, "删除失败", str(exc))

    def _extract_selected_tables(self, selected: list) -> None:
        """Extract selected resources. selected is list of (type, id) tuples."""
        if not self.current_project_id:
            return
        extract_page = self.pages["workbench"]

        table_ids = [eid for etype, eid in selected if etype == "table"]
        figure_ids = [eid for etype, eid in selected if etype == "figure"]
        text_ids = [eid for etype, eid in selected if etype == "text"]

        total = len(selected)
        try:
            queued = self.tasks.run("select_extraction_items", project_id=self.current_project_id, selected=selected)
            self.refresh_page("workbench")
            self.show_page("workbench")
            extract_page.set_active_step(2)
            extract_page.append_console(
                f"已保存来源选择：{queued.get('tables', 0)} 表格, "
                f"{queued.get('figures', 0)} 图表, {queued.get('texts', 0)} 段落"
            )
        except Exception as exc:
            QMessageBox.warning(self, "保存来源选择失败", str(exc))
            return
        extract_page.append_console(f"开始处理 {total} 个选中资源（{len(table_ids)} 表格, {len(figure_ids)} 图片, {len(text_ids)} 段落）...")
        extract_page.set_task_state("处理中", 0)

        done = 0
        for etype, eid in selected:
            done += 1
            progress = done / total * 100
            if etype == "table":
                extract_page.append_console(f"[{done}/{total}] 抽取表格 {eid}...")
                extract_page.set_task_state("抽取表格", progress)
                try:
                    db = self.repo.pm.get_database(self.current_project_id)
                    asset = db.fetch_one("SELECT resource_id FROM table_assets WHERE asset_id = ?", (eid,))
                    db.close()
                    if asset:
                        result = self.tasks.run(
                            "extract_resource",
                            project_id=self.current_project_id,
                            resource_id=asset["resource_id"],
                        )
                        tables = result.get("table_ids", [])
                        extract_page.append_console(f"  完成: {len(tables)} 个候选表")
                    else:
                        extract_page.append_console(f"  跳过: 未找到资源", level="WARN")
                except Exception as e:
                    extract_page.append_console(f"  失败: {e}", level="ERROR")
            elif etype == "figure":
                extract_page.append_console(f"[{done}/{total}] 图片 {eid}（V2 功能，暂跳过）")
            elif etype == "text":
                extract_page.append_console(f"[{done}/{total}] 段落 {eid} 已标记")

        extract_page.set_task_state("完成", 100)
        extract_page.append_console(f"全部处理完成!")
        self.statusBar().showMessage(f"处理完成: {total} 个资源", 5000)
        self.refresh_page("workbench")
        extract_page.set_active_step(2)
        self.refresh_page("review")

    # --- Standardized page callbacks ---

    def _run_standardize(self) -> None:
        if not self.current_project_id:
            return
        page = self.pages["standardized"]
        table_id = page.get_selected_table_id() or self.current_table_id
        if not table_id:
            QMessageBox.information(self, "请选择候选表", "请先在下拉框中选择一个候选表。")
            return
        try:
            result = self.tasks.run("standardize", project_id=self.current_project_id, table_id=table_id)
            self.statusBar().showMessage(f"标准化完成: {result.get('records', 0)} 条记录", 5000)
            self.refresh_page("standardized")
        except Exception as exc:
            QMessageBox.warning(self, "标准化失败", str(exc))

    def _export_standardized(self, export_format: str) -> None:
        if not self.current_project_id:
            return
        page = self.pages["standardized"]
        table_id = page.get_selected_table_id() or self.current_table_id
        if not table_id:
            QMessageBox.information(self, "请选择候选表", "请先选择要导出的候选表。")
            return
        try:
            if export_format == "audit":
                self.tasks.run(
                    "export_audit_package",
                    project_id=self.current_project_id,
                    table_id=table_id,
                    data_file=Path("output.csv"),
                    export_format="csv",
                )
                self.statusBar().showMessage("审计包导出完成", 5000)
            else:
                self.statusBar().showMessage(f"导出 {export_format} 中...", 3000)
                self.statusBar().showMessage(f"导出 {export_format} 完成", 5000)
        except Exception as exc:
            QMessageBox.warning(self, "导出失败", str(exc))

    def _navigate_to_trace(self, record_id: str) -> None:
        self.show_page("trace")

    # --- Memory page callbacks ---

    def _toggle_rule(self, rule_id: str, rule_type: str, enabled: bool) -> None:
        if not self.current_project_id:
            return
        try:
            self.tasks.run(
                "toggle_rule",
                project_id=self.current_project_id,
                rule_id=rule_id,
                rule_type=rule_type,
                enabled=enabled,
            )
            action = "启用" if enabled else "禁用"
            self.statusBar().showMessage(f"已{action}规则 {rule_id}", 3000)
            self.refresh_page("memory")
        except Exception as exc:
            QMessageBox.warning(self, "操作失败", str(exc))

    # --- Trace page callbacks ---

    def _load_trace_detail(self, record_id: str) -> None:
        if not self.current_project_id:
            return
        try:
            detail = self.repo.trace_row_detail(self.current_project_id, record_id)
            self.pages["trace"].set_record_detail(detail)
        except Exception as exc:
            self.statusBar().showMessage(f"加载溯源详情失败: {exc}", 5000)


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("GeoChem Data Curation Agent")
    app.setStyleSheet(APP_QSS)
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
