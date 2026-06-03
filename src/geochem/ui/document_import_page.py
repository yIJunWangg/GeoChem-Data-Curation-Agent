"""Document import and resource discovery workspace."""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDragEnterEvent, QDropEvent
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QComboBox,
    QLineEdit,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QSplitter,
    QTabWidget,
    QTableView,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from .app_widgets import MetricCard, wrap_in_scroll
from .viewmodels import DataFrameTableModel


class DropZone(QFrame):
    """Simple drag-and-drop upload zone."""

    def __init__(self):
        super().__init__()
        self.files_dropped: Callable[[list[str]], None] | None = None
        self.file_select_requested: Callable[[], None] | None = None
        self.setAcceptDrops(True)
        self.setStyleSheet(
            "QFrame { border: 1px dashed #9ec5fe; border-radius: 8px; background: #f8fbff; }"
        )
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(8)
        title = QLabel("或拖拽文件到此处上传")
        title.setAlignment(Qt.AlignCenter)
        subtitle = QLabel("支持 PDF、Excel、CSV、ZIP、DOCX（单个文件 ≤ 200MB）")
        subtitle.setAlignment(Qt.AlignCenter)
        subtitle.setStyleSheet("color: #667085;")
        choose = QPushButton("选择文件")
        choose.clicked.connect(lambda: self.file_select_requested and self.file_select_requested())
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(choose)
        row.addStretch(1)
        layout.addWidget(title)
        layout.addWidget(subtitle)
        layout.addLayout(row)

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event: QDropEvent) -> None:
        paths = []
        for url in event.mimeData().urls():
            if isinstance(url, QUrl) and url.isLocalFile():
                paths.append(url.toLocalFile())
        if paths and self.files_dropped:
            self.files_dropped(paths)
        event.acceptProposedAction()


class DocumentImportPage(QWidget):
    """UI for DOI/browser auth and local resource import."""

    def __init__(self):
        super().__init__()
        self.doi_open_requested: Callable[[str], None] | None = None
        self.confirm_access_requested: Callable[[], None] | None = None
        self.files_import_requested: Callable[[list[str]], None] | None = None
        self.file_select_requested: Callable[[], None] | None = None
        self.resource_action_requested: Callable[[dict], None] | None = None
        self.header_config_confirm_requested: Callable[[str], None] | None = None
        self.resource_model = DataFrameTableModel(columns=["资源", "类型", "来源", "访问状态", "下一步", "article_id", "resource_id"])
        self._console_lines: list[str] = []
        self._build()

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        header = QWidget()
        header_layout = QVBoxLayout(header)
        header_layout.setContentsMargins(20, 18, 20, 10)
        title_row = QHBoxLayout()
        title = QLabel("文献导入与资源发现")
        title.setObjectName("pageTitle")
        title_row.addWidget(title)
        title_row.addStretch(1)
        guide = QPushButton("导入指南")
        title_row.addWidget(guide)
        subtitle = QLabel("通过 DOI、网页链接或文件上传导入文献；需机构登录或人机验证时，先在浏览器完成访问确认，再回到 App 继续 AI 读取。")
        subtitle.setObjectName("pageSubtitle")
        header_layout.addLayout(title_row)
        header_layout.addWidget(subtitle)
        root.addWidget(header)

        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(20, 10, 20, 18)
        layout.setSpacing(14)

        cards = QHBoxLayout()
        self.imported_card = MetricCard("已导入文件数", "0", hint="本项目资源")
        self.public_card = MetricCard("公开资源链接", "0", hint="可直接访问", accent="#12b76a")
        self.login_card = MetricCard("需人工登录", "0", hint="等待浏览器确认", accent="#f79009")
        self.pending_card = MetricCard("等待解析", "0", hint="可进入数据抽取", accent="#667085")
        for card in (self.imported_card, self.public_card, self.login_card, self.pending_card):
            cards.addWidget(card)
        layout.addLayout(cards)

        workspace = QSplitter(Qt.Vertical)
        split = QSplitter(Qt.Horizontal)
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(14)

        source_card = QFrame()
        source_card.setProperty("class", "card")
        source_layout = QVBoxLayout(source_card)
        source_layout.setContentsMargins(14, 14, 14, 14)
        tabs = QTabWidget()
        tabs.addTab(self._doi_tab(), "输入 DOI")
        tabs.addTab(self._url_tab(), "输入网页 URL")
        tabs.addTab(self._file_tab("导入 PDF"), "导入 PDF")
        tabs.addTab(self._file_tab("导入补充材料"), "导入补充材料")
        source_layout.addWidget(tabs)
        left_layout.addWidget(source_card)

        flow = QFrame()
        flow.setProperty("class", "card")
        flow_layout = QVBoxLayout(flow)
        flow_layout.setContentsMargins(14, 14, 14, 14)
        flow_title = QLabel("导入与解析流程")
        flow_title.setObjectName("sectionTitle")
        flow_layout.addWidget(flow_title)
        steps = QHBoxLayout()
        for i, text in enumerate(["输入来源", "打开浏览器/用户登录", "下载或确认访问", "导入项目", "开始解析"], start=1):
            step = QLabel(f"{i}\n{text}")
            step.setAlignment(Qt.AlignCenter)
            step.setProperty("class", "stepBadge")
            steps.addWidget(step)
        flow_layout.addLayout(steps)
        hint = QLabel("提示：对需要订阅或机构权限的资源，请在浏览器中完成人机验证、登录和全文访问确认后，再点击右侧确认按钮。")
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #667085;")
        flow_layout.addWidget(hint)
        left_layout.addWidget(flow)

        middle = QFrame()
        middle.setProperty("class", "card")
        middle_layout = QVBoxLayout(middle)
        middle_layout.setContentsMargins(14, 14, 14, 14)
        table_title = QHBoxLayout()
        self.resource_title = QLabel("候选图表/表格（0 项）")
        self.resource_title.setObjectName("sectionTitle")
        table_title.addWidget(self.resource_title)
        table_title.addStretch(1)
        refresh = QPushButton("刷新")
        table_title.addWidget(refresh)
        middle_layout.addLayout(table_title)
        self.resource_table = QTableView()
        self.resource_table.setAlternatingRowColors(True)
        self.resource_table.setModel(self.resource_model)
        self.resource_table.horizontalHeader().setStretchLastSection(True)
        middle_layout.addWidget(self.resource_table, 1)

        right = QFrame()
        right.setProperty("class", "card")
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(16, 16, 16, 16)
        self.article_title = QLabel("文献信息")
        self.article_title.setObjectName("sectionTitle")
        self.article_status = QLabel("未识别")
        right_layout.addWidget(self.article_title)
        right_layout.addWidget(self.article_status)
        self.article_meta = QLabel("导入 DOI 或文件后，这里会显示题名、作者、年份、期刊和 DOI。")
        self.article_meta.setWordWrap(True)
        right_layout.addWidget(self.article_meta)
        self.notes = QTextEdit()
        self.notes.setMinimumHeight(105)
        self.notes.setPlaceholderText("备注、标签或资源访问说明...")
        right_layout.addWidget(self.notes)
        right_layout.addWidget(QLabel("本文使用的表头预设"))
        self.header_config_selector = QComboBox()
        right_layout.addWidget(self.header_config_selector)
        self.header_suggestion = QLabel("导入文章后，系统会先给出表头预设建议，用户确认后再进入抽取。")
        self.header_suggestion.setWordWrap(True)
        self.header_suggestion.setStyleSheet("color: #667085;")
        right_layout.addWidget(self.header_suggestion)
        confirm_header = QPushButton("确认使用此表头")
        confirm_header.clicked.connect(self._confirm_header_config)
        right_layout.addWidget(confirm_header)
        self.confirm_btn = QPushButton("已在浏览器确认可访问")
        self.confirm_btn.setObjectName("primaryButton")
        self.confirm_btn.clicked.connect(lambda: self.confirm_access_requested and self.confirm_access_requested())
        right_layout.addWidget(self.confirm_btn)
        self.next_hint = QLabel("确认后会保存网页证据资源。若普通抓取无法读取全文，系统仍会保留 URL、DOI 和人工确认状态，交给后续 LLM/浏览器读取任务处理。")
        self.next_hint.setWordWrap(True)
        self.next_hint.setStyleSheet("color: #667085;")
        right_layout.addWidget(self.next_hint)
        right_layout.addStretch(1)

        split.addWidget(left)
        split.addWidget(right)
        split.addWidget(middle)
        split.setSizes([470, 360, 520])
        workspace.addWidget(split)

        console_card = QFrame()
        console_card.setProperty("class", "card")
        console_layout = QVBoxLayout(console_card)
        console_layout.setContentsMargins(14, 12, 14, 14)
        console_layout.setSpacing(8)
        console_head = QHBoxLayout()
        console_title = QLabel("AI 读取控制台")
        console_title.setObjectName("sectionTitle")
        self.task_status = QLabel("空闲")
        self.task_status.setObjectName("taskStatus")
        console_head.addWidget(console_title)
        console_head.addStretch(1)
        console_head.addWidget(self.task_status)
        clear_btn = QPushButton("清空")
        clear_btn.clicked.connect(self.clear_console)
        console_head.addWidget(clear_btn)
        console_layout.addLayout(console_head)
        self.task_progress = QProgressBar()
        self.task_progress.setRange(0, 100)
        self.task_progress.setValue(0)
        console_layout.addWidget(self.task_progress)
        self.console = QPlainTextEdit()
        self.console.setObjectName("importConsole")
        self.console.setReadOnly(True)
        self.console.setMinimumHeight(150)
        self.console.setPlainText("等待任务。输入 DOI/URL 并打开浏览器后，这里会显示 AI/读取器每一步正在做什么。")
        console_layout.addWidget(self.console)
        workspace.addWidget(console_card)
        workspace.setSizes([620, 230])
        layout.addWidget(workspace, 1)

        root.addWidget(wrap_in_scroll(content), 1)

    def _doi_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        self.doi_input = QLineEdit()
        self.doi_input.setPlaceholderText("例如：10.1016/j.gca.2023.02.014")
        open_btn = QPushButton("在浏览器中打开")
        open_btn.setObjectName("primaryButton")
        open_btn.clicked.connect(lambda: self.doi_open_requested and self.doi_open_requested(self.doi_input.text().strip()))
        row = QHBoxLayout()
        row.addWidget(self.doi_input, 1)
        row.addWidget(open_btn)
        layout.addWidget(QLabel("输入 DOI"))
        layout.addLayout(row)
        examples = QLabel("示例：10.1016/j.gca.2023.02.014    10.5194/essd-16-123-2024")
        examples.setStyleSheet("color: #667085;")
        layout.addWidget(examples)
        dz = DropZone()
        dz.files_dropped = lambda paths: self.files_import_requested and self.files_import_requested(paths)
        dz.file_select_requested = lambda: self.file_select_requested and self.file_select_requested()
        layout.addWidget(dz)
        return tab

    def _url_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        self.url_input = QLineEdit()
        self.url_input.setPlaceholderText("https://publisher/article/fulltext 或数据集 URL")
        open_btn = QPushButton("在浏览器中打开")
        open_btn.clicked.connect(lambda: self.doi_open_requested and self.doi_open_requested(self.url_input.text().strip()))
        row = QHBoxLayout()
        row.addWidget(self.url_input, 1)
        row.addWidget(open_btn)
        layout.addWidget(QLabel("输入网页 URL"))
        layout.addLayout(row)
        return tab

    def _file_tab(self, label: str) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.addWidget(QLabel(label))
        dz = DropZone()
        dz.files_dropped = lambda paths: self.files_import_requested and self.files_import_requested(paths)
        dz.file_select_requested = lambda: self.file_select_requested and self.file_select_requested()
        layout.addWidget(dz)
        return tab

    def set_resources(self, rows: list[dict]) -> None:
        display_rows = []
        for row in rows:
            display_rows.append({
                "资源": row.get("discovery_name") or row.get("file_name") or row.get("source_url") or row.get("resource_id"),
                "类型": row.get("discovery_type") or row.get("resource_type", ""),
                "来源": row.get("source") or row.get("doi") or row.get("title") or "本地文件",
                "访问状态": row.get("access_status") or self._status_label(row),
                "下一步": row.get("next_step") or ("解析" if row.get("status") == "pending" else row.get("status", "")),
                "article_id": row.get("article_id", ""),
                "resource_id": row.get("resource_id", ""),
            })
        self.resource_model.set_rows(display_rows)
        self.resource_table.resizeColumnsToContents()
        self.resource_title.setText(f"候选图表/表格（{len(rows)} 项）")

    def set_summary(self, summary: dict) -> None:
        self.imported_card.set_value(summary.get("imported_files", 0))
        self.public_card.set_value(summary.get("public_links", 0))
        self.login_card.set_value(summary.get("needs_login", 0))
        self.pending_card.set_value(summary.get("pending_parse", 0))

    def set_article_info(self, info: dict | None) -> None:
        if not info:
            self.article_status.setText("未识别")
            self.article_meta.setText("导入 DOI 或文件后，这里会显示题名、作者、年份、期刊和 DOI。")
            return
        self.article_status.setText(info.get("status", "已识别"))
        self.article_meta.setText(
            "\n".join([
                info.get("title", "") or "Untitled article",
                f"作者：{info.get('authors', '--')}",
                f"年份：{info.get('year', '--')}    期刊：{info.get('journal', '--')}",
                f"DOI：{info.get('doi', '--')}",
                f"URL：{info.get('url', '--')}",
            ])
        )

    def set_header_configs(self, configs: list[dict], suggested: dict | None = None) -> None:
        self.header_config_selector.blockSignals(True)
        self.header_config_selector.clear()
        self.header_config_selector.addItem("暂不指定", "")
        for config in configs:
            label = f"{config.get('name', config.get('config_id'))}  ({config.get('field_count', 0)} 字段)"
            self.header_config_selector.addItem(label, config.get("config_id"))
        if suggested:
            idx = self.header_config_selector.findData(suggested.get("config_id"))
            if idx >= 0:
                self.header_config_selector.setCurrentIndex(idx)
            self.header_suggestion.setText(
                f"建议：{suggested.get('name', '')}，置信度 {suggested.get('confidence', 0):.2f}。\n{suggested.get('reason', '')}"
            )
        self.header_config_selector.blockSignals(False)

    def set_message(self, message: str) -> None:
        self.next_hint.setText(message)

    def append_console(self, message: str, level: str = "INFO") -> None:
        """Append an import workflow message to the visible console."""
        from datetime import datetime

        line = f"[{datetime.now().strftime('%H:%M:%S')}] {level:<5} {message}"
        self._console_lines.append(line)
        self._console_lines = self._console_lines[-300:]
        self.console.setPlainText("\n".join(self._console_lines))
        cursor = self.console.textCursor()
        cursor.movePosition(cursor.MoveOperation.End)
        self.console.setTextCursor(cursor)

    def set_task_state(self, status: str, progress: float | int | None = None) -> None:
        self.task_status.setText(status)
        if progress is not None:
            value = int(max(0, min(100, float(progress) * 100 if float(progress) <= 1 else float(progress))))
            self.task_progress.setValue(value)

    def clear_console(self) -> None:
        self._console_lines = []
        self.console.setPlainText("控制台已清空。")
        self.task_status.setText("空闲")
        self.task_progress.setValue(0)

    def _status_label(self, row: dict) -> str:
        if row.get("resource_type") == "html_page":
            return "已获取"
        if row.get("source_url"):
            return "可访问"
        return "已导入" if row.get("status") else "待处理"

    def _confirm_header_config(self) -> None:
        config_id = self.header_config_selector.currentData()
        if config_id and self.header_config_confirm_requested:
            self.header_config_confirm_requested(config_id)
