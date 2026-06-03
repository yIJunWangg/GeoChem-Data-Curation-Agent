"""Settings page — single-column scrollable layout with fully wired buttons."""

from __future__ import annotations

import shutil
import threading
from pathlib import Path

import yaml
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)


def fmt_size(size: int) -> str:
    if size >= 1024 ** 3:
        return f"{size / 1024 ** 3:.2f} GB"
    if size >= 1024 ** 2:
        return f"{size / 1024 ** 2:.2f} MB"
    if size >= 1024:
        return f"{size / 1024:.1f} KB"
    return f"{size} B"


class SettingsCard(QFrame):
    def __init__(self, title: str, icon: str = ""):
        super().__init__()
        self.setProperty("class", "card")
        self.layout = QVBoxLayout(self)
        self.layout.setContentsMargins(16, 14, 16, 14)
        self.layout.setSpacing(12)
        header = QLabel(f"{icon}  {title}" if icon else title)
        header.setObjectName("sectionTitle")
        self.layout.addWidget(header)


class SettingsPage(QWidget):
    """Configuration dashboard for LLM, API keys, storage, review, export and UI."""

    save_requested = None  # set by MainWindow
    test_connection_requested = Signal(str, str)  # provider_name, model_name

    def __init__(self):
        super().__init__()
        self.providers: list[dict] = []
        self.data: dict = {}
        self._testing = False

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # Header (fixed, outside scroll)
        header = QWidget()
        header_layout = QVBoxLayout(header)
        header_layout.setContentsMargins(20, 18, 20, 10)
        header_layout.setSpacing(4)
        title = QLabel("设置")
        title.setObjectName("pageTitle")
        subtitle = QLabel("管理 LLM 服务、API Key、数据缓存、审核规则、导出追溯与界面偏好。")
        subtitle.setObjectName("pageSubtitle")
        header_layout.addWidget(title)
        header_layout.addWidget(subtitle)
        root.addWidget(header)

        # Content (scrollable)
        content = QWidget()
        content.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)
        col = QVBoxLayout(content)
        col.setContentsMargins(20, 10, 20, 18)
        col.setSpacing(14)

        self._build_llm_card(col)
        self._build_keys_card(col)
        self._build_storage_card(col)
        self._build_export_card(col)
        self._build_review_card(col)
        self._build_ui_card(col)
        self._build_summary_card(col)
        self._build_config_card(col)
        col.addStretch(1)

        # Wrap content in scroll area
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        scroll.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        scroll.setMinimumHeight(0)
        scroll.setWidget(content)
        root.addWidget(scroll, 1)

    # ------------------------------------------------------------------
    # Card builders
    # ------------------------------------------------------------------

    def _build_llm_card(self, parent: QVBoxLayout) -> None:
        card = SettingsCard("LLM 调用服务", "🤖")
        form = QGridLayout()
        self.provider_combo = QComboBox()
        self.model_combo = QComboBox()
        self.model_combo.setEditable(True)
        self.model_combo.setInsertPolicy(QComboBox.NoInsert)
        self.model_combo.setPlaceholderText("选择预设模型或手动输入自定义模型名")
        self.base_url = QLineEdit()
        self.base_url.setPlaceholderText("https://api.example.com/v1")
        self.timeout_spin = QSpinBox()
        self.timeout_spin.setRange(1, 600)
        self.timeout_spin.setValue(60)
        self.retry_spin = QSpinBox()
        self.retry_spin.setRange(0, 10)
        self.retry_spin.setValue(3)
        self.parallel_spin = QSpinBox()
        self.parallel_spin.setRange(1, 20)
        self.parallel_spin.setValue(5)
        self.temperature = QDoubleSpinBox()
        self.temperature.setRange(0, 2)
        self.temperature.setSingleStep(0.05)
        self.temperature.setDecimals(2)

        form.addWidget(QLabel("默认服务商"), 0, 0)
        form.addWidget(self.provider_combo, 0, 1)
        form.addWidget(QLabel("默认模型"), 0, 2)
        form.addWidget(self.model_combo, 0, 3)
        form.addWidget(QLabel("Base URL / API Endpoint"), 1, 0, 1, 4)
        form.addWidget(self.base_url, 2, 0, 1, 4)
        form.addWidget(QLabel("请求超时(秒)"), 3, 0)
        form.addWidget(self.timeout_spin, 4, 0)
        form.addWidget(QLabel("最大重试次数"), 3, 1)
        form.addWidget(self.retry_spin, 4, 1)
        form.addWidget(QLabel("并发请求数"), 3, 2)
        form.addWidget(self.parallel_spin, 4, 2)
        form.addWidget(QLabel("温度 / Temperature"), 3, 3)
        form.addWidget(self.temperature, 4, 3)
        card.layout.addLayout(form)

        toggles = QHBoxLayout()
        self.cache_toggle = QCheckBox("允许缓存响应")
        self.cache_toggle.setChecked(True)
        self.token_toggle = QCheckBox("记录 token 用量")
        self.token_toggle.setChecked(True)
        self.cost_toggle = QCheckBox("成本估算")
        self.cost_toggle.setChecked(True)
        toggles.addWidget(self.cache_toggle)
        toggles.addWidget(self.token_toggle)
        toggles.addWidget(self.cost_toggle)
        card.layout.addLayout(toggles)

        footer = QHBoxLayout()
        self.connection_status = QLabel("连接状态：未检测")
        self.connection_status.setStyleSheet("color:#667085;")
        self.test_btn = QPushButton("测试连接")
        self.test_btn.clicked.connect(self._test_connection)
        self.save_btn = QPushButton("保存设置")
        self.save_btn.setObjectName("primaryButton")
        self.save_btn.clicked.connect(self._save_clicked)
        footer.addWidget(self.connection_status)
        footer.addStretch(1)
        footer.addWidget(self.test_btn)
        footer.addWidget(self.save_btn)
        card.layout.addLayout(footer)

        self.provider_combo.currentIndexChanged.connect(self._provider_changed)
        parent.addWidget(card)

    def _build_storage_card(self, parent: QVBoxLayout) -> None:
        card = SettingsCard("数据与缓存", "🛢")
        self.project_dir = QLineEdit()
        self.temp_dir = QLineEdit(str(Path.home() / "GeoChem_Temp"))
        browse1 = QPushButton("浏览...")
        browse1.clicked.connect(lambda: self._browse_dir(self.project_dir))
        browse2 = QPushButton("浏览...")
        browse2.clicked.connect(lambda: self._browse_dir(self.temp_dir))
        grid = QGridLayout()
        grid.addWidget(QLabel("默认项目目录"), 0, 0)
        grid.addWidget(self.project_dir, 0, 1)
        grid.addWidget(browse1, 0, 2)
        grid.addWidget(QLabel("临时文件目录"), 1, 0)
        grid.addWidget(self.temp_dir, 1, 1)
        grid.addWidget(browse2, 1, 2)
        self.autosave_spin = QSpinBox()
        self.autosave_spin.setRange(1, 120)
        self.autosave_spin.setValue(5)
        grid.addWidget(QLabel("自动保存间隔(分钟)"), 2, 0)
        grid.addWidget(self.autosave_spin, 2, 1)
        card.layout.addLayout(grid)

        self.restore_toggle = QCheckBox("启动时恢复上次项目")
        self.restore_toggle.setChecked(True)
        self.local_cache_toggle = QCheckBox("启用本地缓存")
        self.local_cache_toggle.setChecked(True)
        self.cache_size_label = QLabel("缓存大小：计算中...")
        clean_btn = QPushButton("清理缓存...")
        clean_btn.clicked.connect(self._clean_cache)
        line = QHBoxLayout()
        line.addWidget(self.restore_toggle)
        line.addWidget(self.local_cache_toggle)
        line.addWidget(self.cache_size_label)
        line.addStretch(1)
        line.addWidget(clean_btn)
        card.layout.addLayout(line)
        parent.addWidget(card)

    def _build_export_card(self, parent: QVBoxLayout) -> None:
        card = SettingsCard("导出与追溯", "📄")
        row1 = QHBoxLayout()
        row1.addWidget(QLabel("默认导出格式"))
        self.export_format = QComboBox()
        self.export_format.addItems(["Excel (.xlsx)", "CSV (.csv)"])
        row1.addWidget(self.export_format)
        card.layout.addLayout(row1)

        self.trace_toggle = QCheckBox("自动附加 Trace 信息")
        self.trace_toggle.setChecked(True)
        self.audit_toggle = QCheckBox("自动生成审计报告")
        self.audit_toggle.setChecked(True)
        self.calc_format = QComboBox()
        self.calc_format.addItems(["Markdown + JSONL", "JSONL only", "Markdown only"])
        card.layout.addWidget(self.trace_toggle)
        card.layout.addWidget(self.audit_toggle)
        row2 = QHBoxLayout()
        row2.addWidget(QLabel("计算过程归档格式"))
        row2.addWidget(self.calc_format)
        card.layout.addLayout(row2)
        parent.addWidget(card)

    def _build_keys_card(self, parent: QVBoxLayout) -> None:
        card = SettingsCard("API Key 管理", "🛡")
        hint = QLabel("安全显示：仅展示前缀，其余以 * 隐藏。建议通过环境变量或系统钥匙串配置。")
        hint.setStyleSheet(
            "background:#ecfdf3; color:#027a48; padding:8px; border:1px solid #abefc6; border-radius:7px;"
        )
        hint.setWordWrap(True)
        card.layout.addWidget(hint)
        self.key_rows = QVBoxLayout()
        card.layout.addLayout(self.key_rows)
        note = QLabel("所有密钥建议保存在环境变量或系统钥匙串中，避免以明文形式写入配置文件。")
        note.setStyleSheet("color:#667085;")
        note.setWordWrap(True)
        card.layout.addWidget(note)
        parent.addWidget(card)

    def _build_review_card(self, parent: QVBoxLayout) -> None:
        card = SettingsCard("审核与规则", "✅")
        self.high_risk_toggle = QCheckBox("高风险字段强制人工审核")
        self.high_risk_toggle.setChecked(True)
        self.auto_rule_toggle = QCheckBox("自动应用已确认规则")
        self.auto_rule_toggle.setChecked(True)
        self.teaching_toggle = QCheckBox("Teaching / 规则学习开关")
        self.teaching_toggle.setChecked(True)
        self.threshold = QDoubleSpinBox()
        self.threshold.setRange(0, 1)
        self.threshold.setSingleStep(0.01)
        self.threshold.setValue(0.75)
        card.layout.addWidget(self.high_risk_toggle)
        card.layout.addWidget(self.auto_rule_toggle)
        row = QHBoxLayout()
        row.addWidget(QLabel("审核阈值"))
        row.addWidget(self.threshold)
        card.layout.addLayout(row)
        card.layout.addWidget(self.teaching_toggle)
        scope = QHBoxLayout()
        scope.addWidget(QLabel("规则作用域默认值"))
        self.scope_project = QPushButton("当前项目")
        self.scope_project.setObjectName("primaryButton")
        self.scope_global = QPushButton("全局")
        scope.addWidget(self.scope_project)
        scope.addWidget(self.scope_global)
        card.layout.addLayout(scope)
        parent.addWidget(card)

    def _build_ui_card(self, parent: QVBoxLayout) -> None:
        card = SettingsCard("界面与通用", "🎨")
        theme = QHBoxLayout()
        theme.addWidget(QLabel("主题"))
        self.light_btn = QPushButton("浅色")
        self.light_btn.setObjectName("primaryButton")
        self.dark_btn = QPushButton("深色")
        self.system_btn = QPushButton("跟随系统")
        self.light_btn.clicked.connect(lambda: self._set_theme("light"))
        self.dark_btn.clicked.connect(lambda: self._set_theme("dark"))
        self.system_btn.clicked.connect(lambda: self._set_theme("system"))
        theme.addWidget(self.light_btn)
        theme.addWidget(self.dark_btn)
        theme.addWidget(self.system_btn)
        card.layout.addLayout(theme)
        self.dense_toggle = QCheckBox("紧凑模式")
        self.notify_toggle = QCheckBox("通知提醒")
        self.notify_toggle.setChecked(True)
        lang = QHBoxLayout()
        lang.addWidget(QLabel("语言"))
        self.lang_zh_btn = QPushButton("中文")
        self.lang_zh_btn.setObjectName("primaryButton")
        self.lang_en_btn = QPushButton("English")
        self.lang_zh_btn.clicked.connect(lambda: self._set_language("zh"))
        self.lang_en_btn.clicked.connect(lambda: self._set_language("en"))
        lang.addWidget(self.lang_zh_btn)
        lang.addWidget(self.lang_en_btn)
        card.layout.addWidget(self.dense_toggle)
        card.layout.addWidget(self.notify_toggle)
        card.layout.addLayout(lang)
        parent.addWidget(card)

    def _build_summary_card(self, parent: QVBoxLayout) -> None:
        card = SettingsCard("当前配置摘要", "📘")
        self.summary = QLabel("暂无配置")
        self.summary.setWordWrap(True)
        self.summary.setTextInteractionFlags(Qt.TextSelectableByMouse)
        card.layout.addWidget(self.summary)
        parent.addWidget(card)

    def _build_config_card(self, parent: QVBoxLayout) -> None:
        card = SettingsCard("配置文件管理", "⚙")
        export_btn = QPushButton("导出配置")
        export_btn.clicked.connect(self._export_config)
        import_btn = QPushButton("导入配置")
        import_btn.clicked.connect(self._import_config)
        reset_btn = QPushButton("恢复默认")
        reset_btn.setObjectName("dangerButton")
        reset_btn.clicked.connect(self._reset_config)
        row = QHBoxLayout()
        row.addWidget(export_btn)
        row.addWidget(import_btn)
        row.addWidget(reset_btn)
        card.layout.addLayout(row)
        self.config_path = QLabel("配置文件：—")
        self.config_path.setWordWrap(True)
        self.config_path.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.config_path.setStyleSheet("font-family: monospace; color: #344054; padding: 4px;")
        self.saved_at = QLabel("上次保存：—")
        card.layout.addWidget(self.config_path)
        card.layout.addWidget(self.saved_at)
        parent.addWidget(card)

    # ------------------------------------------------------------------
    # Data loading
    # ------------------------------------------------------------------

    def set_settings(self, data: dict) -> None:
        """Load settings dict into all form widgets."""
        self.data = data
        self.providers = data.get("providers", [])

        # LLM card
        self.provider_combo.blockSignals(True)
        self.provider_combo.clear()
        for provider in self.providers:
            status_icon = {"configured": "✓", "inline": "✓", "missing": "✗"}.get(
                provider.get("key_status", ""), "○"
            )
            label = f"{status_icon} {provider['display_name']}"
            self.provider_combo.addItem(label, provider["name"])
        idx = self.provider_combo.findData(data.get("default_provider"))
        self.provider_combo.setCurrentIndex(max(idx, 0))
        self.provider_combo.blockSignals(False)

        self.temperature.setValue(float(data.get("temperature", 0.1)))
        self.timeout_spin.setValue(int(data.get("timeout", 60)))
        self.retry_spin.setValue(int(data.get("max_retries", 3)))
        self.parallel_spin.setValue(int(data.get("max_parallel", 5)))
        self.cache_toggle.setChecked(bool(data.get("enable_cache", True)))
        self.token_toggle.setChecked(bool(data.get("enable_token_tracking", True)))
        self.cost_toggle.setChecked(bool(data.get("enable_cost_estimation", True)))
        self._provider_changed()
        default_model = data.get("default_model", "")
        if default_model:
            model_idx = self.model_combo.findText(default_model)
            if model_idx >= 0:
                self.model_combo.setCurrentIndex(model_idx)
            else:
                # Custom model not in preset list — set as editable text
                self.model_combo.setEditText(default_model)

        # Storage card
        self.project_dir.setText(data.get("default_project_dir", ""))
        self.temp_dir.setText(data.get("temp_dir", str(Path.home() / "GeoChem_Temp")))
        self.autosave_spin.setValue(int(data.get("autosave_interval", 5)))
        self.restore_toggle.setChecked(bool(data.get("restore_last_project", True)))
        self.local_cache_toggle.setChecked(bool(data.get("enable_local_cache", True)))
        cache_size = data.get("cache_size", 0)
        self.cache_size_label.setText(f"缓存大小：{fmt_size(cache_size)}")

        # Export card
        fmt = data.get("export_format", "Excel (.xlsx)")
        idx = self.export_format.findText(fmt)
        if idx >= 0:
            self.export_format.setCurrentIndex(idx)
        self.trace_toggle.setChecked(bool(data.get("auto_trace", True)))
        self.audit_toggle.setChecked(bool(data.get("auto_audit", True)))
        calc_fmt = data.get("calc_archive_format", "Markdown + JSONL")
        idx = self.calc_format.findText(calc_fmt)
        if idx >= 0:
            self.calc_format.setCurrentIndex(idx)

        # Review card
        self.high_risk_toggle.setChecked(bool(data.get("high_risk_force_review", True)))
        self.auto_rule_toggle.setChecked(bool(data.get("auto_apply_rules", True)))
        self.threshold.setValue(float(data.get("review_threshold", 0.75)))
        self.teaching_toggle.setChecked(bool(data.get("teaching_enabled", True)))

        # UI card
        self.dense_toggle.setChecked(bool(data.get("compact_mode", False)))
        self.notify_toggle.setChecked(bool(data.get("notifications", True)))

        # Keys card
        self._render_keys()

        # Summary card
        self._render_summary()

        # Config card
        self.config_path.setText(f"配置文件：{data.get('config_path', '')}")

    # ------------------------------------------------------------------
    # Provider / model helpers
    # ------------------------------------------------------------------

    def _provider_changed(self) -> None:
        provider = self._selected_provider()
        self.model_combo.blockSignals(True)
        self.model_combo.clear()
        if not provider:
            self.model_combo.blockSignals(False)
            return
        self.model_combo.addItems(provider.get("models", []))
        # Select first model by default
        if self.model_combo.count() > 0:
            self.model_combo.setCurrentIndex(0)
        self.model_combo.blockSignals(False)
        self.base_url.setText(provider.get("base_url", ""))
        if provider.get("key_status") == "configured":
            self.connection_status.setText("连接状态：密钥已配置")
            self.connection_status.setStyleSheet("color:#027a48;")
        elif provider.get("key_status") == "missing":
            self.connection_status.setText("连接状态：缺少环境变量")
            self.connection_status.setStyleSheet("color:#b54708;")
        elif provider.get("key_status") == "inline":
            self.connection_status.setText("连接状态：密钥已内联配置")
            self.connection_status.setStyleSheet("color:#027a48;")
        else:
            self.connection_status.setText("连接状态：待测试")
            self.connection_status.setStyleSheet("color:#667085;")

    def _selected_provider(self) -> dict | None:
        name = self.provider_combo.currentData()
        for provider in self.providers:
            if provider["name"] == name:
                return provider
        return self.providers[0] if self.providers else None

    # ------------------------------------------------------------------
    # API Key rendering & management
    # ------------------------------------------------------------------

    def _render_keys(self) -> None:
        # Clear existing rows
        while self.key_rows.count():
            item = self.key_rows.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        for provider in self.providers[:8]:
            row = QFrame()
            row.setStyleSheet("border-bottom:1px solid #eef2f7;")
            layout = QHBoxLayout(row)
            layout.setContentsMargins(2, 8, 2, 8)

            # Status indicator
            status = provider.get("key_status", "not_configured")
            status_map = {
                "configured": ("●", "#12b76a", "环境变量已配置"),
                "inline": ("●", "#1468d8", "内联密钥"),
                "missing": ("●", "#f79009", "环境变量未检测到"),
                "not_configured": ("○", "#98a2b3", "未配置"),
            }
            dot, color, tooltip = status_map.get(status, ("○", "#98a2b3", "未知"))
            status_dot = QLabel(dot)
            status_dot.setStyleSheet(f"color:{color}; font-size:16px;")
            status_dot.setToolTip(tooltip)
            status_dot.setFixedWidth(20)

            name_label = QLabel(provider["display_name"])
            name_label.setMinimumWidth(120)
            key_label = QLabel(provider.get("api_key_mask", "未配置"))
            key_label.setMinimumWidth(200)
            if status in ("configured", "inline"):
                key_label.setStyleSheet("color:#027a48;")
            elif status == "missing":
                key_label.setStyleSheet("color:#b54708;")
            else:
                key_label.setStyleSheet("color:#98a2b3;")

            edit_btn = QPushButton("编辑")
            edit_btn.clicked.connect(lambda checked=False, p=provider, kl=key_label, sd=status_dot: self._edit_key(p, kl, sd))
            clear_btn = QPushButton("清除")
            clear_btn.setObjectName("dangerButton")
            clear_btn.clicked.connect(lambda checked=False, p=provider, kl=key_label, sd=status_dot: self._clear_key(p, kl, sd))
            keychain_btn = QPushButton("钥匙串")
            keychain_btn.clicked.connect(
                lambda checked=False, p=provider: self._show_keychain_info(p)
            )

            layout.addWidget(status_dot)
            layout.addWidget(name_label)
            layout.addWidget(key_label, 1)
            layout.addWidget(edit_btn)
            layout.addWidget(keychain_btn)
            layout.addWidget(clear_btn)
            self.key_rows.addWidget(row)

    def _edit_key(self, provider: dict, key_label: QLabel, status_dot: QLabel | None = None) -> None:
        """Prompt user to enter a new API key for this provider."""
        text, ok = QInputDialog.getText(
            self,
            f"编辑 API Key — {provider['display_name']}",
            f"输入 {provider['display_name']} 的 API Key：",
            QLineEdit.Password,
        )
        if ok and text.strip():
            new_key = text.strip()
            provider["api_key_mask"] = f"{new_key[:6]}{'*' * 14}{new_key[-4:] if len(new_key) > 10 else ''}"
            provider["key_status"] = "inline"
            provider["_new_key"] = new_key  # stash for save
            key_label.setText(provider["api_key_mask"])
            key_label.setStyleSheet("color:#1468d8;")
            if status_dot:
                status_dot.setText("●")
                status_dot.setStyleSheet("color:#1468d8; font-size:16px;")
                status_dot.setToolTip("内联密钥（未保存）")
            self.connection_status.setText("连接状态：密钥已更新 — 点击「测试连接」验证后自动保存")
            self.connection_status.setStyleSheet("color:#b54708;")
            self._render_summary()

    def _clear_key(self, provider: dict, key_label: QLabel, status_dot: QLabel | None = None) -> None:
        """Clear the API key for this provider."""
        reply = QMessageBox.question(
            self,
            "确认清除",
            f"确定要清除 {provider['display_name']} 的 API Key 吗？",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply == QMessageBox.Yes:
            provider["api_key_mask"] = "未配置"
            provider["key_status"] = "not_configured"
            provider["_new_key"] = ""
            key_label.setText("未配置")
            key_label.setStyleSheet("color:#98a2b3;")
            if status_dot:
                status_dot.setText("○")
                status_dot.setStyleSheet("color:#98a2b3; font-size:16px;")
                status_dot.setToolTip("未配置")
            self._render_summary()

    def _show_keychain_info(self, provider: dict) -> None:
        env_name = provider.get("env_name", "")
        if env_name:
            QMessageBox.information(
                self,
                "系统钥匙串",
                f"此服务通过环境变量 {env_name} 读取密钥。\n\n"
                f"当前状态：{'已检测到' if provider.get('key_status') == 'configured' else '未检测到'}\n\n"
                "请在终端或 .env 文件中设置该环境变量后重启应用。",
            )
        else:
            QMessageBox.information(
                self,
                "系统钥匙串",
                f"{provider['display_name']} 当前使用内联密钥或未配置。\n\n"
                "如需使用环境变量，请在 config/settings.yaml 中将 api_key 设为 "
                '"${ENV_VAR_NAME}" 格式，然后在系统中设置对应环境变量。',
            )

    # ------------------------------------------------------------------
    # Test connection
    # ------------------------------------------------------------------

    def _test_connection(self) -> None:
        """Test LLM connection in a background thread."""
        if self._testing:
            return
        provider = self._selected_provider()
        if not provider:
            QMessageBox.warning(self, "错误", "没有可用的服务商。")
            return

        model = self.model_combo.currentText().strip()
        if not model:
            QMessageBox.warning(self, "错误", "请选择或输入一个模型。")
            return

        self._testing = True
        self.test_btn.setEnabled(False)
        self.test_btn.setText("测试中...")
        self.connection_status.setText("连接状态：正在测试...")
        self.connection_status.setStyleSheet("color:#667085;")

        # Collect any unsaved API key so the test uses the latest value
        api_key = provider.get("_new_key", "")
        base_url = self.base_url.text().strip()

        def _do_test():
            try:
                from ..ui.viewmodels import ProjectRepository

                repo = ProjectRepository()
                result = repo.test_provider_connection(
                    provider_name=provider["name"],
                    model=model,
                    api_key=api_key,
                    base_url=base_url,
                )
                self._on_test_result(result["success"], result["message"])
            except Exception as exc:
                self._on_test_result(False, f"测试异常: {exc}")

        thread = threading.Thread(target=_do_test, daemon=True)
        thread.start()

    def _on_test_result(self, success: bool, message: str) -> None:
        """Slot called from test thread — must be invoked on the GUI thread."""
        self._testing = False
        self.test_btn.setEnabled(True)
        self.test_btn.setText("测试连接")
        if success:
            self.connection_status.setText(f"连接状态：{message}")
            self.connection_status.setStyleSheet("color:#027a48;")
            # Record this provider/model as tested
            provider = self._selected_provider()
            model = self.model_combo.currentText().strip()
            if provider and model:
                tested = self.data.setdefault("tested_models", [])
                entry = {"provider": provider["name"], "model": model}
                if entry not in tested:
                    tested.append(entry)
            # Auto-save the API key + tested model
            self._auto_save_pending_key()
        else:
            self.connection_status.setText(f"连接状态：{message}")
            self.connection_status.setStyleSheet("color:#d92d20;")

    def _auto_save_pending_key(self) -> None:
        """Save pending API key changes and tested model to config."""
        # Stash any new keys
        for p in self.providers:
            if "_new_key" in p:
                p["api_key"] = p.pop("_new_key")

        updates = {
            "tested_models": self.data.get("tested_models", []),
            "providers": [
                {
                    "name": p["name"],
                    "api_key": p.get("api_key", p.get("api_key_ref", "")),
                    "base_url": p.get("base_url", ""),
                }
                for p in self.providers
            ],
        }
        if self.save_requested:
            self.save_requested(updates)
        self._render_keys()
        self._render_summary()
        self.saved_at.setText("上次保存：刚刚（自动保存）")

    # ------------------------------------------------------------------
    # Save
    # ------------------------------------------------------------------

    def _save_clicked(self) -> None:
        """Collect all form values and emit to the save handler."""
        updates = self.collect_all_settings()
        if self.save_requested:
            self.save_requested(updates)
        self.saved_at.setText("上次保存：刚刚")
        self._render_summary()
        QMessageBox.information(self, "保存成功", "设置已保存到配置文件。")

    def collect_all_settings(self) -> dict:
        """Gather every form field into a flat dict."""
        # Stash any new API keys into providers list
        for p in self.providers:
            if "_new_key" in p:
                p["api_key"] = p.pop("_new_key")
            # Sync base_url from text field to the selected provider
            if p["name"] == self.provider_combo.currentData():
                p["base_url"] = self.base_url.text().strip()

        return {
            # LLM
            "default_provider": self.provider_combo.currentData(),
            "default_model": self.model_combo.currentText(),
            "base_url": self.base_url.text().strip(),
            "temperature": self.temperature.value(),
            "timeout": self.timeout_spin.value(),
            "max_retries": self.retry_spin.value(),
            "max_parallel": self.parallel_spin.value(),
            "enable_cache": self.cache_toggle.isChecked(),
            "enable_token_tracking": self.token_toggle.isChecked(),
            "enable_cost_estimation": self.cost_toggle.isChecked(),
            # Storage
            "default_project_dir": self.project_dir.text().strip(),
            "temp_dir": self.temp_dir.text().strip(),
            "autosave_interval": self.autosave_spin.value(),
            "restore_last_project": self.restore_toggle.isChecked(),
            "enable_local_cache": self.local_cache_toggle.isChecked(),
            # Export
            "export_format": self.export_format.currentText(),
            "auto_trace": self.trace_toggle.isChecked(),
            "auto_audit": self.audit_toggle.isChecked(),
            "calc_archive_format": self.calc_format.currentText(),
            # Review
            "high_risk_force_review": self.high_risk_toggle.isChecked(),
            "auto_apply_rules": self.auto_rule_toggle.isChecked(),
            "review_threshold": self.threshold.value(),
            "teaching_enabled": self.teaching_toggle.isChecked(),
            # UI
            "compact_mode": self.dense_toggle.isChecked(),
            "notifications": self.notify_toggle.isChecked(),
            # Providers (with updated keys)
            "providers": [
                {
                    "name": p["name"],
                    "display_name": p["display_name"],
                    "api_key": p.get("api_key", p.get("api_key_ref", "")),
                    "base_url": p.get("base_url", ""),
                    "key_status": p.get("key_status", "not_configured"),
                }
                for p in self.providers
            ],
        }

    # ------------------------------------------------------------------
    # Cache management
    # ------------------------------------------------------------------

    def _clean_cache(self) -> None:
        """Clean up cache/temp files."""
        cache_paths = []
        temp_dir = Path(self.temp_dir.text()) if self.temp_dir.text() else None
        project_dir = Path(self.project_dir.text()) if self.project_dir.text() else None

        if temp_dir and temp_dir.exists():
            cache_paths.append(temp_dir)
        if project_dir:
            cache_dir = project_dir / ".cache"
            if cache_dir.exists():
                cache_paths.append(cache_dir)

        if not cache_paths:
            QMessageBox.information(self, "清理缓存", "没有找到可清理的缓存目录。")
            return

        total_size = sum(
            p.stat().st_size for path in cache_paths for p in path.rglob("*") if p.is_file()
        )
        reply = QMessageBox.question(
            self,
            "确认清理缓存",
            f"将清理以下目录的缓存文件：\n\n"
            + "\n".join(f"  • {p}" for p in cache_paths)
            + f"\n\n释放空间：{fmt_size(total_size)}\n\n确定继续吗？",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply == QMessageBox.Yes:
            cleaned = 0
            for path in cache_paths:
                try:
                    for item in path.iterdir():
                        if item.is_file():
                            item.unlink()
                            cleaned += 1
                        elif item.is_dir():
                            shutil.rmtree(item)
                            cleaned += 1
                except Exception as exc:
                    QMessageBox.warning(self, "清理出错", f"清理 {path} 时出错：{exc}")
            self.cache_size_label.setText("缓存大小：0 B")
            QMessageBox.information(self, "清理完成", f"已清理 {cleaned} 个项目。")

    # ------------------------------------------------------------------
    # Config import / export
    # ------------------------------------------------------------------

    def _export_config(self) -> None:
        """Export current config to a user-chosen file."""
        path, _ = QFileDialog.getSaveFileName(
            self, "导出配置文件", "geochem_config.yaml", "YAML Files (*.yaml *.yml);;All Files (*)"
        )
        if not path:
            return
        try:
            config_path = self.data.get("config_path", "config/settings.yaml")
            src = Path(config_path)
            if src.exists():
                shutil.copy2(src, path)
                QMessageBox.information(self, "导出成功", f"配置已导出到：\n{path}")
            else:
                # Generate from current form state
                settings = self.collect_all_settings()
                with open(path, "w", encoding="utf-8") as f:
                    yaml.dump(settings, f, default_flow_style=False, allow_unicode=True, sort_keys=False)
                QMessageBox.information(self, "导出成功", f"当前设置已导出到：\n{path}")
        except Exception as exc:
            QMessageBox.warning(self, "导出失败", str(exc))

    def _import_config(self) -> None:
        """Import config from a user-chosen file."""
        path, _ = QFileDialog.getOpenFileName(
            self, "导入配置文件", "", "YAML Files (*.yaml *.yml);;All Files (*)"
        )
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f)
            if not data:
                QMessageBox.warning(self, "导入失败", "文件为空或格式不正确。")
                return
            # Copy to config location
            config_path = self.data.get("config_path", "config/settings.yaml")
            shutil.copy2(path, config_path)
            QMessageBox.information(
                self,
                "导入成功",
                f"配置已从以下文件导入并保存：\n{path}\n\n请重新加载设置页面以查看更改。",
            )
        except Exception as exc:
            QMessageBox.warning(self, "导入失败", str(exc))

    def _reset_config(self) -> None:
        """Reset config to built-in defaults."""
        reply = QMessageBox.question(
            self,
            "恢复默认配置",
            "确定要将所有设置恢复为默认值吗？\n\n"
            "当前配置将被覆盖，自定义的 API Key 和供应商设置将丢失。\n"
            "建议先导出当前配置作为备份。",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return
        try:
            from ..core.config import _default_config

            config_path = self.data.get("config_path", "config/settings.yaml")
            default = _default_config()
            from ..core.config import save_config

            save_config(default, config_path)
            QMessageBox.information(
                self,
                "已恢复默认",
                f"配置已恢复为默认值并保存到：\n{config_path}\n\n请重新加载设置页面以查看更改。",
            )
        except Exception as exc:
            QMessageBox.warning(self, "恢复失败", str(exc))

    # ------------------------------------------------------------------
    # Theme & language (stubs with user feedback)
    # ------------------------------------------------------------------

    def _set_theme(self, theme: str) -> None:
        names = {"light": "浅色", "dark": "深色", "system": "跟随系统"}
        self.light_btn.setObjectName("primaryButton" if theme == "light" else "")
        self.dark_btn.setObjectName("primaryButton" if theme == "dark" else "")
        self.system_btn.setObjectName("primaryButton" if theme == "system" else "")
        # Force style refresh
        for btn in (self.light_btn, self.dark_btn, self.system_btn):
            btn.style().unpolish(btn)
            btn.style().polish(btn)
        QMessageBox.information(
            self,
            "主题切换",
            f"已选择「{names[theme]}」主题。\n\n完整深色主题将在后续版本中实现，当前仅记录偏好。",
        )

    def _set_language(self, lang: str) -> None:
        names = {"zh": "中文", "en": "English"}
        self.lang_zh_btn.setObjectName("primaryButton" if lang == "zh" else "")
        self.lang_en_btn.setObjectName("primaryButton" if lang == "en" else "")
        for btn in (self.lang_zh_btn, self.lang_en_btn):
            btn.style().unpolish(btn)
            btn.style().polish(btn)
        QMessageBox.information(
            self,
            "语言切换",
            f"已选择「{names[lang]}」。\n\n多语言支持将在后续版本中实现，当前仅记录偏好。",
        )

    # ------------------------------------------------------------------
    # Summary
    # ------------------------------------------------------------------

    def _render_summary(self) -> None:
        provider = self._selected_provider() or {}
        configured = sum(1 for p in self.providers if p.get("key_status") in {"configured", "inline"})
        lines = [
            "── LLM 服务 ──",
            f"服务商：{provider.get('display_name', '—')}",
            f"模型：{self.model_combo.currentText() or '—'}",
            f"温度：{self.temperature.value():.2f}    超时：{self.timeout_spin.value()}s    "
            f"重试：{self.retry_spin.value()}    并发：{self.parallel_spin.value()}",
            "",
            "── Token 统计与成本 ──",
            f"记录 Token：{'已启用' if self.token_toggle.isChecked() else '未启用'}    "
            f"成本估算：{'已启用' if self.cost_toggle.isChecked() else '未启用'}    "
            f"缓存响应：{'已启用' if self.cache_toggle.isChecked() else '未启用'}",
            "",
            "── 存储与缓存 ──",
            f"项目目录：{self.project_dir.text() or '—'}",
            f"缓存大小：{self.cache_size_label.text()}",
            "",
            "── 安全与密钥 ──",
            f"密钥状态：{configured}/{len(self.providers)} 已配置",
            "",
            "── 导出与追溯 ──",
            f"默认格式：{self.export_format.currentText()}    "
            f"Trace：{'已附加' if self.trace_toggle.isChecked() else '未附加'}    "
            f"审计报告：{'已生成' if self.audit_toggle.isChecked() else '未生成'}",
            "",
            "── 审核与规则 ──",
            f"高风险审核：{'强制' if self.high_risk_toggle.isChecked() else '不强制'}    "
            f"阈值：{self.threshold.value():.2f}    "
            f"教学模式：{'开启' if self.teaching_toggle.isChecked() else '关闭'}",
        ]
        self.summary.setText("\n".join(lines))

    # ------------------------------------------------------------------
    # File dialog helper
    # ------------------------------------------------------------------

    def _browse_dir(self, target: QLineEdit) -> None:
        path = QFileDialog.getExistingDirectory(self, "选择目录", target.text() or str(Path.home()))
        if path:
            target.setText(path)
