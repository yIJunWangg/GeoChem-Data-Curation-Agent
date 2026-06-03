"""PySide6 desktop interface for GeoChem Data Curation Agent."""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QStackedWidget,
    QTableView,
    QTextEdit,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..workflow import EventBus, TaskService, WorkflowRunner
from .document_import_page import DocumentImportPage
from .schema_config_page import SchemaConfigPage
from .settings_page import SettingsPage
from .styles import APP_QSS
from .token_stats import TokenStatsPage
from .viewmodels import DataFrameTableModel, ProjectRepository


NAV_ITEMS = [
    ("dashboard", "项目总览"),
    ("schema", "表头配置"),
    ("import", "文献导入"),
    ("resources", "资源清单"),
    ("extract", "数据抽取"),
    ("mapping", "字段映射"),
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
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # Header (fixed)
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

        # Content (scrollable)
        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(20, 10, 20, 18)
        content_layout.setSpacing(14)

        split = QSplitter(Qt.Horizontal)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabel("资源 / 表格清单")
        self.table = QTableView()
        self.table.setAlternatingRowColors(True)
        self.table.setModel(self.table_model)
        self.detail = QTextEdit()
        self.detail.setReadOnly(True)
        self.detail.setMinimumWidth(280)
        split.addWidget(self.tree)
        split.addWidget(self.table)
        split.addWidget(self.detail)
        split.setSizes([260, 760, 280])
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
                child.setData(0, Qt.UserRole, table["table_id"])
                root.addChild(child)
            root.setExpanded(True)
        self.detail.setText(f"候选表数量: {len(tables)}\n选择左侧 table 后可加载预览。")

    def set_preview(self, rows: list[dict], columns: list[str]) -> None:
        self.table_model.set_rows(rows, columns)
        self.table.resizeColumnsToContents()


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
            "resources": TablePage("资源清单", "查看从文章中抽取到的相关段落、表题、图题、表格和图片资产。"),
            "extract": CandidatePage(),
            "mapping": TablePage("字段映射与单位换算", "审查 source field 到目标 schema 的映射、单位、风险和置信度。"),
            "review": TablePage("人工审核与规则记忆", "处理高风险映射、单位换算和 learned rule。"),
            "memory": TablePage("规则记忆", "查看已确认映射规则与学习规则。"),
            "standardized": TablePage("标准化导出", "查看按用户表头生成的标准化记录并准备导出。"),
            "trace": TablePage("数据溯源查看", "行级追溯来源、patch、review、计算与质量等级。"),
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

    def _handle_workflow_event(self, event) -> None:
        self.statusBar().showMessage(event.message, 5000)
        page = self.pages.get("import") if hasattr(self, "pages") else None
        if event.event_type == "import" and hasattr(page, "append_console"):
            page.append_console(event.message)
            page.set_task_state(self._import_status_label(event.message, event.progress), event.progress)

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
            elif key in {"import", "resources"}:
                if key == "import":
                    self.pages[key].set_resources(self.repo.resource_discovery_rows(self.current_project_id))
                    self.pages[key].set_summary(self.repo.resource_summary(self.current_project_id))
                    self.pages[key].set_article_info(self.repo.latest_article_info(self.current_project_id))
                    latest_article = self.repo.latest_article_info(self.current_project_id)
                    suggested = self.repo.suggest_header_config_for_article(
                        self.current_project_id,
                        latest_article.get("article_id") if latest_article else None,
                    )
                    self.pages[key].set_header_configs(self.repo.list_header_configs(self.current_project_id), suggested)
                else:
                    self.pages[key].set_rows(self.repo.resource_discovery_rows(self.current_project_id))
            elif key == "extract":
                tables = self.repo.candidate_tables(self.current_project_id)
                self.pages[key].set_tables(tables)
                if tables:
                    self.current_table_id = tables[0]["table_id"]
                    rows, cols = self.repo.candidate_rows(self.current_project_id, self.current_table_id)
                    self.pages[key].set_preview(rows, cols)
            elif key == "mapping":
                self.pages[key].set_rows(self.repo.mappings(self.current_project_id, self.current_table_id))
            elif key == "review":
                self.pages[key].set_rows(self.repo.review_items(self.current_project_id))
            elif key == "standardized":
                rows, cols = self.repo.standardized_records(self.current_project_id, self.current_table_id)
                self.pages[key].set_rows(rows, cols)
            elif key == "trace" and self.current_table_id:
                self.pages[key].set_rows(self.repo.trace_rows(self.current_project_id, self.current_table_id))
            elif key == "cost":
                self._refresh_cost_page()
            elif key == "schema":
                self.pages[key].set_config_options(self.repo.list_header_configs(self.current_project_id))
            elif key == "memory":
                mappings = self.repo.mappings(self.current_project_id)
                self.pages[key].set_rows(mappings)
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
            self.statusBar().showMessage(f"已导入 {result.get('count', 0)} 个资源", 5000)
            self.refresh_page("import")
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


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("GeoChem Data Curation Agent")
    app.setStyleSheet(APP_QSS)
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
