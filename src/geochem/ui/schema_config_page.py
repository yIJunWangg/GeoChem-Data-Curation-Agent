"""Schema configuration workspace for target Excel headers."""

from __future__ import annotations

from typing import Callable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFrame,
    QGridLayout,
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

from .app_widgets import MetricCard, wrap_in_scroll
from .viewmodels import DataFrameTableModel


class SchemaConfigPage(QWidget):
    """Dense UI for importing, inspecting and configuring user target headers."""

    def __init__(self):
        super().__init__()
        self.import_requested: Callable[[], None] | None = None
        self.save_requested: Callable[[], None] | None = None
        self.config_selected: Callable[[str], None] | None = None
        self.model = DataFrameTableModel(columns=[
            "序号", "字段名", "类型", "默认单位", "别名数量", "审核策略", "必填", "状态", "字段组",
        ], editable=True)
        self._rows: list[dict] = []
        self.current_config_id: str | None = None
        self.source_file: str = ""
        self._build()

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        header = QWidget()
        header_layout = QVBoxLayout(header)
        header_layout.setContentsMargins(20, 18, 20, 10)
        header_layout.setSpacing(4)
        title_row = QHBoxLayout()
        title = QLabel("表头配置 / Schema Configuration")
        title.setObjectName("pageTitle")
        title_row.addWidget(title)
        title_row.addStretch(1)
        save_btn = QPushButton("保存配置")
        save_btn.setObjectName("primaryButton")
        save_btn.clicked.connect(lambda: self.save_requested and self.save_requested())
        title_row.addWidget(save_btn)
        subtitle = QLabel("从用户 Excel 第一行读取最终目标表头、单位和顺序，并配置字段类型、审核策略与换算风险。")
        subtitle.setObjectName("pageSubtitle")
        header_layout.addLayout(title_row)
        header_layout.addWidget(subtitle)
        root.addWidget(header)

        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(20, 10, 20, 18)
        layout.setSpacing(14)

        actions = QHBoxLayout()
        actions.addWidget(QLabel("表头预设"))
        self.config_selector = QComboBox()
        self.config_selector.setMinimumWidth(260)
        self.config_selector.currentIndexChanged.connect(self._config_changed)
        actions.addWidget(self.config_selector)
        self.config_name = QLineEdit()
        self.config_name.setPlaceholderText("配置名称，例如：奥陶纪地化 156 字段")
        self.config_name.setMinimumWidth(260)
        actions.addWidget(self.config_name)
        import_btn = QPushButton("导入表头")
        import_btn.clicked.connect(lambda: self.import_requested and self.import_requested())
        add_btn = QPushButton("新增字段")
        batch_btn = QPushButton("批量编辑")
        template_btn = QPushButton("规则模板")
        actions.addWidget(import_btn)
        actions.addWidget(add_btn)
        actions.addWidget(batch_btn)
        actions.addWidget(template_btn)
        actions.addStretch(1)
        layout.addLayout(actions)

        split = QSplitter(Qt.Horizontal)
        left = QFrame()
        left.setProperty("class", "card")
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(14, 14, 14, 14)
        title_line = QHBoxLayout()
        self.list_title = QLabel("字段列表（0）")
        self.list_title.setObjectName("sectionTitle")
        title_line.addWidget(self.list_title)
        title_line.addStretch(1)
        self.group_filter = QComboBox()
        self.group_filter.addItem("全部类型", "")
        self.group_filter.currentIndexChanged.connect(self._apply_filter)
        title_line.addWidget(self.group_filter)
        left_layout.addLayout(title_line)
        self.table = QTableView()
        self.table.setAlternatingRowColors(True)
        self.table.setModel(self.model)
        self.table.horizontalHeader().setStretchLastSection(True)
        left_layout.addWidget(self.table, 1)

        right = QFrame()
        right.setProperty("class", "card")
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(16, 16, 16, 16)
        right_layout.setSpacing(12)
        self.detail_title = QLabel("字段配置")
        self.detail_title.setObjectName("sectionTitle")
        self.status = QLabel("未选择字段")
        right_layout.addWidget(self.detail_title)
        right_layout.addWidget(self.status)

        self.description = QTextEdit()
        self.description.setReadOnly(False)
        self.description.setMinimumHeight(96)
        right_layout.addWidget(QLabel("字段描述"))
        right_layout.addWidget(self.description)

        grid = QGridLayout()
        self.type_box = QComboBox()
        self.type_box.addItems(["数值（Numeric）", "文本（Text）", "坐标（Coordinate）", "年代（Age）", "比值（Ratio）"])
        self.unit_box = QComboBox()
        self.unit_box.setEditable(True)
        self.unit_box.addItems(["", "wt%", "ppm", "‰", "m", "Ma", "°"])
        self.group_box = QComboBox()
        self.group_box.addItems([
            "basic_info", "location", "stratigraphy_age", "sample_context", "isotope_organic",
            "major_elements", "trace_elements", "ree", "iron_speciation", "weathering_indices",
        ])
        grid.addWidget(QLabel("数据类型"), 0, 0)
        grid.addWidget(self.type_box, 0, 1)
        grid.addWidget(QLabel("默认单位"), 1, 0)
        grid.addWidget(self.unit_box, 1, 1)
        grid.addWidget(QLabel("字段分组"), 2, 0)
        grid.addWidget(self.group_box, 2, 1)
        right_layout.addLayout(grid)

        self.aliases = QLabel("别名：--")
        self.chemistry = QLabel("化学形态：按字段自动识别")
        self.conversion_toggle = QCheckBox("允许单位/化学形态换算")
        self.conversion_toggle.setChecked(True)
        right_layout.addWidget(self.aliases)
        right_layout.addWidget(self.chemistry)
        right_layout.addWidget(self.conversion_toggle)

        self.warning = QLabel("高风险规则会进入人工审核，确认后才能复用。")
        self.warning.setObjectName("warningText")
        right_layout.addWidget(self.warning)
        self.samples = QLabel("示例值：--")
        self.samples.setWordWrap(True)
        right_layout.addWidget(self.samples)
        apply_btn = QPushButton("应用到字段表")
        apply_btn.clicked.connect(self._apply_detail_to_row)
        right_layout.addWidget(apply_btn)
        right_layout.addStretch(1)

        split.addWidget(left)
        split.addWidget(right)
        split.setSizes([880, 380])
        layout.addWidget(split, 1)

        footer = QHBoxLayout()
        self.health = MetricCard("表头健康度", "0%")
        self.count_card = MetricCard("字段总数", "0")
        self.required_card = MetricCard("必填字段", "0", accent="#12b76a")
        self.risk_card = MetricCard("风险字段", "0", accent="#f79009")
        self.unconfigured_card = MetricCard("未完成配置", "0", accent="#667085")
        for card in (self.health, self.count_card, self.required_card, self.risk_card, self.unconfigured_card):
            footer.addWidget(card)
        layout.addLayout(footer)

        self.advice = QLabel("校验与建议：导入 Excel 后会显示字段缺口、单位风险和可入库建议。")
        self.advice.setWordWrap(True)
        layout.addWidget(self.advice)

        root.addWidget(wrap_in_scroll(content), 1)
        self.table.selectionModel().selectionChanged.connect(self._selection_changed)

    def set_headers(
        self,
        rows: list[dict],
        summary: dict | None = None,
        config_id: str | None = None,
        source_file: str = "",
    ) -> None:
        self._rows = rows
        self.current_config_id = config_id
        self.source_file = source_file or summary.get("source_file", "") if summary else source_file
        if summary and summary.get("name"):
            self.config_name.setText(summary["name"])
        self._refresh_group_filter(rows)
        self._apply_filter()
        summary = summary or {}
        self.list_title.setText(f"字段列表（{len(rows)}）")
        self.health.set_value(f"{summary.get('health_percent', 0)}%")
        self.count_card.set_value(summary.get("field_count", len(rows)))
        self.required_card.set_value(summary.get("required_count", 0))
        self.risk_card.set_value(summary.get("risk_count", 0))
        self.unconfigured_card.set_value(summary.get("unconfigured_count", 0))
        self.advice.setText(summary.get("advice", "校验与建议：所有导入表头会按用户 Excel 原始顺序输出，缺数据列保留为空。"))

    def set_config_options(self, configs: list[dict], current_config_id: str | None = None) -> None:
        self.config_selector.blockSignals(True)
        self.config_selector.clear()
        self.config_selector.addItem("新建表头配置", "")
        for config in configs:
            label = f"{config.get('name', config.get('config_id'))}  ({config.get('field_count', 0)} 字段)"
            self.config_selector.addItem(label, config.get("config_id"))
        if current_config_id:
            idx = self.config_selector.findData(current_config_id)
            if idx >= 0:
                self.config_selector.setCurrentIndex(idx)
        self.config_selector.blockSignals(False)

    def get_rows_for_save(self) -> list[dict]:
        self._apply_detail_to_row(silent=True)
        return [dict(row) for row in self._rows]

    def get_config_name(self) -> str:
        name = self.config_name.text().strip()
        return name or "未命名表头配置"

    def _refresh_group_filter(self, rows: list[dict]) -> None:
        current = self.group_filter.currentData()
        self.group_filter.blockSignals(True)
        self.group_filter.clear()
        self.group_filter.addItem("全部类型", "")
        for group in sorted({row.get("字段组", "") for row in rows if row.get("字段组")}):
            self.group_filter.addItem(group, group)
        if current:
            idx = self.group_filter.findData(current)
            if idx >= 0:
                self.group_filter.setCurrentIndex(idx)
        self.group_filter.blockSignals(False)

    def _apply_filter(self) -> None:
        group = self.group_filter.currentData()
        rows = [row for row in self._rows if not group or row.get("字段组") == group]
        self.model.set_rows(rows)
        self.table.resizeColumnsToContents()

    def _selection_changed(self) -> None:
        selected = self.table.selectionModel().selectedRows()
        if not selected:
            return
        row = self.model.rows[selected[0].row()]
        field = row.get("字段名", "")
        self.detail_title.setText(f"字段配置：{field}")
        self.status.setText(f"状态：{row.get('状态', '--')}    审核策略：{row.get('审核策略', '--')}")
        self.description.setText(row.get("description", ""))
        self.unit_box.setCurrentText(row.get("默认单位", "") or "")
        self.group_box.setCurrentText(row.get("字段组", "trace_elements") or "trace_elements")
        self.aliases.setText(f"别名：{row.get('aliases', '--')}")
        self.chemistry.setText(f"化学形态：{row.get('chemistry', '按字段自动识别')}")
        self.warning.setText(row.get("risk_reason", "高风险规则会进入人工审核，确认后才能复用。"))
        samples = row.get("sample_values") or []
        self.samples.setText("示例值：" + (" / ".join(map(str, samples[:8])) if samples else "--"))

    def _apply_detail_to_row(self, silent: bool = False) -> None:
        selected = self.table.selectionModel().selectedRows()
        if not selected:
            return
        row = self.model.rows[selected[0].row()]
        row["description"] = self.description.toPlainText().strip()
        row["默认单位"] = self.unit_box.currentText().strip() or "—"
        row["字段组"] = self.group_box.currentText().strip()
        row["状态"] = "配置中" if "审核" in str(row.get("审核策略", "")) else "已完成"
        self._apply_filter()
        if not silent:
            self.advice.setText(f"已应用「{row.get('字段名', '')}」的字段详情修改，保存配置后可复用。")

    def _config_changed(self) -> None:
        config_id = self.config_selector.currentData()
        if config_id and self.config_selected:
            self.config_selected(config_id)
