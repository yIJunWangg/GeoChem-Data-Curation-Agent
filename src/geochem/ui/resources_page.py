"""Resources page with actual table/image preview and schema-relevant paragraphs."""

from __future__ import annotations

from html import escape
import re
from pathlib import Path
from typing import Any, Callable

from PySide6.QtCore import Qt
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import (
    QCheckBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from .app_widgets import HeaderPage, MetricCard, wrap_in_scroll


_NORMAL_STYLE = "QFrame { border: 1px solid #dfe5ee; border-radius: 8px; background: #ffffff; }"
_SELECTED_STYLE = "QFrame { border: 2px solid #1468d8; border-radius: 8px; background: #f0f6ff; }"


def _is_checked(checkbox: QCheckBox) -> bool:
    return checkbox.checkState() == Qt.CheckState.Checked


def parse_markdown_table(markdown: str) -> tuple[list[str], list[list[str]]]:
    """Parse the small markdown previews produced by pdfplumber."""
    lines = [line.strip() for line in (markdown or "").splitlines() if line.strip().startswith("|")]
    if len(lines) < 2:
        return [], []
    rows: list[list[str]] = []
    for line in lines:
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        if cells and all(set(cell) <= {"-", ":", " "} for cell in cells):
            continue
        rows.append(cells)
    if not rows:
        return [], []
    headers = rows[0]
    return headers, rows[1:]


def _set_card_style(card: QFrame, selected: bool) -> None:
    card.setStyleSheet(_SELECTED_STYLE if selected else _NORMAL_STYLE)


def highlight_terms(text: str, terms: list[str]) -> str:
    """Return rich text with target header/geochemistry terms highlighted."""
    highlighted = escape(text or "")
    usable = [t.strip() for t in terms if t and len(t.strip()) > 1]
    fallback_terms = [
        "geochemistry", "whole-rock", "sample", "samples", "supplementary",
        "ppm", "wt%", "element", "concentration", "isotope", "pyrite", "sulfur",
        "TOC", "REE", "CIA", "Table", "Figure", "Fig.",
    ]
    for term in sorted(set(usable + fallback_terms), key=len, reverse=True):
        pattern = re.compile(re.escape(escape(term)), re.IGNORECASE)
        highlighted = pattern.sub(
            lambda m: (
                '<b style="color:#1468d8;background:#eaf3ff;'
                'padding:1px 3px;border-radius:2px;">'
                f"{m.group(0)}</b>"
            ),
            highlighted,
        )
    return highlighted


def relevant_snippet(text: str, terms: list[str], radius: int = 130) -> str:
    """Return the key part around the first target/geochemistry term."""
    source = " ".join((text or "").split())
    if len(source) <= radius * 2:
        return source
    needles = [t.strip() for t in terms if t and len(t.strip()) > 1]
    needles.extend([
        "geochemistry", "whole-rock", "sample", "supplementary", "ppm",
        "wt%", "element", "concentration", "isotope", "pyrite", "sulfur",
        "TOC", "REE", "CIA", "Table", "Figure", "Fig.",
    ])
    lowered = source.lower()
    hit = -1
    for term in sorted(set(needles), key=len, reverse=True):
        idx = lowered.find(term.lower())
        if idx >= 0:
            hit = idx
            break
    if hit < 0:
        return source[: radius * 2] + "..."
    start = max(0, hit - radius)
    end = min(len(source), hit + radius)
    prefix = "..." if start > 0 else ""
    suffix = "..." if end < len(source) else ""
    return prefix + source[start:end] + suffix


class TablePreviewCard(QFrame):
    """Card showing actual table content with checkbox."""

    def __init__(
        self,
        title: str,
        page: str,
        preview_md: str,
        asset_id: str,
        selected: bool = False,
        row_count: int | None = None,
        image_path: str = "",
        parent=None,
    ):
        super().__init__(parent)
        self.asset_id = asset_id
        self.display_name = title
        self.page = page
        self._selected = False
        self.setCursor(Qt.PointingHandCursor)
        self.setStyleSheet(_NORMAL_STYLE)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(6)

        header = QHBoxLayout()
        self._cb = QCheckBox()
        self._cb.setText("选择此项")
        self._cb.setMinimumWidth(86)
        self._cb.stateChanged.connect(self._on_check)
        header.addWidget(self._cb)
        title_lbl = QLabel(f"📊 <b>{title}</b>")
        title_lbl.setWordWrap(True)
        header.addWidget(title_lbl, 1)
        page_lbl = QLabel(f'<span style="color:#53637a;">{page}</span>')
        header.addWidget(page_lbl)
        layout.addLayout(header)

        headers, rows = parse_markdown_table(preview_md)
        if headers and rows:
            table = QTableWidget(min(len(rows), 8), len(headers))
            table.setHorizontalHeaderLabels(headers)
            table.verticalHeader().setVisible(False)
            table.setEditTriggers(QTableWidget.NoEditTriggers)
            table.setSelectionMode(QTableWidget.NoSelection)
            table.setAlternatingRowColors(True)
            table.setMaximumHeight(210)
            table.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            for r, row in enumerate(rows[:8]):
                for c, value in enumerate(row[:len(headers)]):
                    table.setItem(r, c, QTableWidgetItem(value))
            table.resizeColumnsToContents()
            layout.addWidget(table)
        elif image_path and Path(image_path).exists():
            img = QLabel()
            img.setAlignment(Qt.AlignCenter)
            img.setStyleSheet("border: 1px solid #dfe5ee; border-radius: 6px; background: #f8fafd;")
            pixmap = QPixmap(image_path)
            if not pixmap.isNull():
                img.setPixmap(pixmap.scaled(1100, 520, Qt.KeepAspectRatio, Qt.SmoothTransformation))
            else:
                img.setText("页面截图无法加载")
            img.setMaximumHeight(540)
            layout.addWidget(img)
            if preview_md:
                cap = QLabel(highlight_terms(preview_md.replace("图片格式表格", "").strip(), []))
                cap.setTextFormat(Qt.RichText)
                cap.setWordWrap(True)
                cap.setStyleSheet("color: #53637a; padding-top: 4px;")
                layout.addWidget(cap)
        else:
            is_clue = "图片格式表格" in (preview_md or "") or not row_count
            hint_text = preview_md.replace("图片格式表格", "").strip() if preview_md else ""
            hint = QLabel(
                ("表格线索，尚未解析出真实单元格。发送到数据抽取后，AI/Reader 会优先处理此线索。\n"
                 if is_clue else "无表格数据预览。\n")
                + hint_text[:500]
            )
            hint.setWordWrap(True)
            hint.setMinimumHeight(86)
            hint.setStyleSheet(
                "color: #53637a; background: #f8fafd; border: 1px dashed #cfd8e6; "
                "border-radius: 6px; padding: 10px; font-size: 12px;"
            )
            layout.addWidget(hint)
        self._cb.setChecked(selected)

    def _on_check(self, state):
        self._selected = _is_checked(self._cb)
        _set_card_style(self, self._selected)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._cb.setChecked(not self._cb.isChecked())
            event.accept()
            return
        super().mousePressEvent(event)

    def is_selected(self):
        return self._cb.isChecked()


class FigureCard(QFrame):
    """Card showing an extracted figure image with checkbox."""

    def __init__(self, caption: str, page: str, image_path: str, evidence_id: str, selected: bool = False, parent=None):
        super().__init__(parent)
        self.evidence_id = evidence_id
        self.display_name = caption
        self.page = page
        self._selected = False
        self.setCursor(Qt.PointingHandCursor)
        self.setStyleSheet(_NORMAL_STYLE)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(10)

        self._cb = QCheckBox()
        self._cb.setText("选择")
        self._cb.setMinimumWidth(58)
        self._cb.stateChanged.connect(self._on_check)
        layout.addWidget(self._cb)

        img_label = QLabel()
        img_label.setFixedSize(120, 90)
        img_label.setStyleSheet("border: 1px solid #dfe5ee; border-radius: 4px; background: #f8fafd;")
        img_label.setAlignment(Qt.AlignCenter)
        if image_path and Path(image_path).exists():
            pixmap = QPixmap(image_path)
            if not pixmap.isNull():
                img_label.setPixmap(pixmap.scaled(120, 90, Qt.KeepAspectRatio, Qt.SmoothTransformation))
            else:
                img_label.setText("无法加载")
        else:
            img_label.setText("无图片")
            img_label.setStyleSheet(img_label.styleSheet() + "color: #53637a;")
        layout.addWidget(img_label)

        text_layout = QVBoxLayout()
        text_layout.setSpacing(3)
        cap = QLabel(f"<b>🖼 {caption[:80]}</b>")
        cap.setWordWrap(True)
        text_layout.addWidget(cap)
        pg = QLabel(f'<span style="color:#53637a;">{page}</span>')
        text_layout.addWidget(pg)
        if len(caption) > 80:
            detail = QLabel(caption[80:200])
            detail.setWordWrap(True)
            detail.setStyleSheet("color: #53637a; font-size: 11px;")
            detail.setMaximumHeight(30)
            text_layout.addWidget(detail)
        text_layout.addStretch(1)
        layout.addLayout(text_layout, 1)
        self._cb.setChecked(selected)

    def _on_check(self, state):
        self._selected = _is_checked(self._cb)
        _set_card_style(self, self._selected)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._cb.setChecked(not self._cb.isChecked())
            event.accept()
            return
        super().mousePressEvent(event)

    def is_selected(self):
        return self._cb.isChecked()


class ParagraphCard(QFrame):
    """Paragraph card with schema field highlighting and field badges."""

    def __init__(self, text: str, page: str, evidence_id: str,
                 matched_fields: list[str], selected: bool = False, parent=None):
        super().__init__(parent)
        self.evidence_id = evidence_id
        self.display_name = text[:100]
        self.page = page
        self.source_text = text
        self._selected = False
        self.setCursor(Qt.PointingHandCursor)
        self.setStyleSheet(_NORMAL_STYLE)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(10)

        self._cb = QCheckBox()
        self._cb.setText("选择")
        self._cb.setMinimumWidth(58)
        self._cb.stateChanged.connect(self._on_check)
        layout.addWidget(self._cb)

        content = QVBoxLayout()
        content.setSpacing(4)

        top = QHBoxLayout()
        top.addWidget(QLabel(f'<span style="color:#53637a;font-size:11px;">{page}</span>'))
        for field in matched_fields[:5]:
            badge = QLabel(field)
            badge.setStyleSheet(
                "background: #eaf3ff; color: #1468d8; border: 1px solid #b8d7ff; "
                "border-radius: 3px; padding: 1px 6px; font-size: 10px; font-weight: 700;"
            )
            top.addWidget(badge)
        top.addStretch(1)
        content.addLayout(top)

        snippet = relevant_snippet(text, matched_fields)
        highlighted = highlight_terms(snippet, matched_fields)
        text_lbl = QLabel(highlighted)
        text_lbl.setTextFormat(Qt.RichText)
        text_lbl.setWordWrap(True)
        text_lbl.setStyleSheet("font-size: 12px; color: #172033;")
        text_lbl.setMaximumHeight(55)
        content.addWidget(text_lbl)

        layout.addLayout(content, 1)
        self._cb.setChecked(selected)

    def _on_check(self, state):
        self._selected = _is_checked(self._cb)
        _set_card_style(self, self._selected)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._cb.setChecked(not self._cb.isChecked())
            event.accept()
            return
        super().mousePressEvent(event)

    def is_selected(self):
        return self._cb.isChecked()


class ResourcesPage(HeaderPage):
    """Resource discovery with actual table/image preview and schema-relevant paragraphs."""

    def __init__(self):
        self.discover_requested: Callable[[str], None] | None = None
        self.extract_selected_requested: Callable[[list], None] | None = None
        self.refresh_requested: Callable[[], None] | None = None

        self._schema_fields: list[str] = []
        self._table_cards: list[TablePreviewCard] = []
        self._figure_cards: list[FigureCard] = []
        self._paragraph_cards: list[ParagraphCard] = []
        self._current_resource_id: str | None = None
        self._all_rows: list[dict[str, Any]] = []

        super().__init__("资源清单", "导入文献后自动发现资源。勾选需要的表格、图片和段落，点击抽取进入下一步。")
        self._build_body()

    def set_schema_fields(self, fields: list[str]) -> None:
        self._schema_fields = [f for f in fields if f and len(f) > 1]

    def set_items(self, rows: list[dict[str, Any]]) -> None:
        self._all_rows = rows
        # Auto-select the first resource for discover
        if rows and not self._current_resource_id:
            self._current_resource_id = rows[0].get("resource_id")

    def _build_body(self) -> None:
        cards = QHBoxLayout()
        self._table_metric = MetricCard("表格", "0", accent="#1468d8")
        self._figure_metric = MetricCard("图片", "0", accent="#7c3aed")
        self._link_metric = MetricCard("链接", "0", accent="#027a48")
        self._para_metric = MetricCard("相关段落", "0", accent="#b54708")
        for c in (self._table_metric, self._figure_metric, self._link_metric, self._para_metric):
            cards.addWidget(c)
        self._content_layout.addLayout(cards)

        toolbar = QHBoxLayout()
        toolbar.setSpacing(10)
        self._discover_btn = QPushButton("重新发现")
        self._discover_btn.clicked.connect(self._do_discover)
        self._extract_btn = QPushButton("发送选中项到数据抽取")
        self._extract_btn.setObjectName("primaryButton")
        self._extract_btn.clicked.connect(self._do_extract)
        self._extract_btn.setEnabled(False)
        self._select_all = QPushButton("全选")
        self._select_all.clicked.connect(lambda: self._toggle_all(True))
        self._deselect_all = QPushButton("取消全选")
        self._deselect_all.clicked.connect(lambda: self._toggle_all(False))
        self._search = QLineEdit()
        self._search.setPlaceholderText("搜索段落...")
        self._search.setMaximumWidth(180)
        self._search.textChanged.connect(self._filter_paras)
        self._refresh = QPushButton("刷新")
        self._refresh.clicked.connect(lambda: self.refresh_requested and self.refresh_requested())

        toolbar.addWidget(self._discover_btn)
        toolbar.addWidget(self._extract_btn)
        toolbar.addWidget(self._select_all)
        toolbar.addWidget(self._deselect_all)
        toolbar.addStretch(1)
        toolbar.addWidget(self._search)
        toolbar.addWidget(self._refresh)
        self._content_layout.addLayout(toolbar)

        self._sel_label = QLabel("已选: 0 表格, 0 图片, 0 段落")
        self._sel_label.setStyleSheet("color: #1468d8; font-weight: 650;")
        self._content_layout.addWidget(self._sel_label)
        self._selected_view = QTextEdit()
        self._selected_view.setReadOnly(True)
        self._selected_view.setMaximumHeight(78)
        self._selected_view.setPlaceholderText("已选来源会显示在这里。")
        self._selected_view.setStyleSheet(
            "QTextEdit { background: #f8fafd; border: 1px solid #dfe5ee; "
            "border-radius: 8px; color: #172033; }"
        )
        self._content_layout.addWidget(self._selected_view)

        self._tabs = QTabWidget()

        # Tables tab
        self._tables_area = QScrollArea()
        self._tables_area.setWidgetResizable(True)
        self._tables_area.setFrameShape(QFrame.NoFrame)
        self._tables_widget = QWidget()
        self._tables_layout = QVBoxLayout(self._tables_widget)
        self._tables_layout.setSpacing(8)
        self._tables_layout.setContentsMargins(10, 10, 10, 10)
        self._tables_area.setWidget(self._tables_widget)
        self._tabs.addTab(self._tables_area, "📊 表格 (0)")

        # Figures tab
        self._figures_area = QScrollArea()
        self._figures_area.setWidgetResizable(True)
        self._figures_area.setFrameShape(QFrame.NoFrame)
        self._figures_widget = QWidget()
        self._figures_layout = QVBoxLayout(self._figures_widget)
        self._figures_layout.setSpacing(8)
        self._figures_layout.setContentsMargins(10, 10, 10, 10)
        self._figures_area.setWidget(self._figures_widget)
        self._tabs.addTab(self._figures_area, "🖼 图片 (0)")

        # Links tab
        self._links_view = QTextEdit()
        self._links_view.setReadOnly(True)
        self._tabs.addTab(self._links_view, "🔗 链接 (0)")

        # Paragraphs tab
        self._paras_area = QScrollArea()
        self._paras_area.setWidgetResizable(True)
        self._paras_area.setFrameShape(QFrame.NoFrame)
        self._paras_widget = QWidget()
        self._paras_layout = QVBoxLayout(self._paras_widget)
        self._paras_layout.setSpacing(6)
        self._paras_layout.setContentsMargins(10, 10, 10, 10)
        self._paras_area.setWidget(self._paras_widget)
        self._tabs.addTab(self._paras_area, "📝 相关段落 (0)")

        self._content_layout.addWidget(self._tabs, 1)

    def set_discovered_elements(self, elements: dict[str, list[dict[str, Any]]]) -> None:
        tables = elements.get("tables", [])
        figures = elements.get("figures", [])
        links = elements.get("links", [])
        text = elements.get("text", [])

        # Filter paragraphs: prioritize schema matches, but still show useful
        # captions and geochemical paragraphs when no schema has been loaded yet.
        relevant = []
        for t in text:
            content = (t.get("evidence_text") or "")
            content_lower = content.lower()
            matched = [f for f in self._schema_fields if f.lower() in content_lower]
            hint_fields = [f for f in (t.get("target_header"), t.get("target_field")) if f]
            if matched or hint_fields or not self._schema_fields or t.get("evidence_type") == "table_caption":
                t["_matched_fields"] = matched or hint_fields
                relevant.append(t)

        self._table_metric.set_value(len(tables))
        self._figure_metric.set_value(len(figures))
        self._link_metric.set_value(len(links))
        self._para_metric.set_value(len(relevant))

        self._tabs.setTabText(0, f"📊 表格 ({len(tables)})")
        self._tabs.setTabText(1, f"🖼 图片 ({len(figures)})")
        self._tabs.setTabText(2, f"🔗 链接 ({len(links)})")
        self._tabs.setTabText(3, f"📝 相关段落 ({len(relevant)})")

        self._build_tables(tables)
        self._build_figures(figures)
        self._build_links(links)
        self._build_paragraphs(relevant)
        self._update_sel_label()

    def _build_tables(self, tables: list[dict]) -> None:
        self._table_cards.clear()
        self._clear_layout(self._tables_layout)
        for t in tables:
            title = t.get("title", "Table")
            page = t.get("page_or_sheet", "")
            preview = t.get("caption", "")  # This is the markdown preview
            selected = t.get("status") in {"selected_for_extraction", "selected"}
            card = TablePreviewCard(
                title,
                page,
                preview,
                t.get("asset_id", ""),
                selected=selected,
                row_count=t.get("row_count"),
                image_path=t.get("raw_file_path") or "",
            )
            card._cb.stateChanged.connect(lambda _: self._update_sel_label())
            self._table_cards.append(card)
            self._tables_layout.addWidget(card)
        if not tables:
            self._tables_layout.addWidget(self._placeholder("未发现表格。请先导入 PDF 并运行资源发现。"))
        self._tables_layout.addStretch(1)

    def _build_figures(self, figures: list[dict]) -> None:
        self._figure_cards.clear()
        self._clear_layout(self._figures_layout)
        for f in figures:
            text = f.get("evidence_text", "Figure")
            page = f.get("page_or_section", "")
            # Extract image path from evidence_text if present
            img_path = ""
            if "\n[PATH]" in text:
                parts = text.split("\n[PATH]")
                text = parts[0]
                img_path = parts[1]
            selected = f.get("status") in {"selected_for_extraction", "selected"}
            card = FigureCard(text, page, img_path, f.get("evidence_id", ""), selected=selected)
            card._cb.stateChanged.connect(lambda _: self._update_sel_label())
            self._figure_cards.append(card)
            self._figures_layout.addWidget(card)
        if not figures:
            self._figures_layout.addWidget(self._placeholder("未发现图片。"))
        self._figures_layout.addStretch(1)

    def _build_links(self, links: list[dict]) -> None:
        seen = set()
        lines = []
        for l in links:
            url = l.get("url", "")
            page = l.get("page_or_section", "")
            if url and url not in seen:
                seen.add(url)
                lines.append(f'<span style="color:#53637a;">[{page}]</span> <a href="{url}">{url[:120]}</a>')
        self._links_view.setHtml("<br>".join(lines) or '<span style="color:#53637a;">未发现链接。</span>')

    def _build_paragraphs(self, paras: list[dict]) -> None:
        self._paragraph_cards.clear()
        self._clear_layout(self._paras_layout)
        for p in paras:
            text = p.get("evidence_text", "")
            page = p.get("page_or_section", "")
            matched = p.get("_matched_fields", [])
            selected = p.get("status") in {"selected_for_extraction", "selected"}
            card = ParagraphCard(text, page, p.get("evidence_id", ""), matched, selected=selected)
            card._cb.stateChanged.connect(lambda _: self._update_sel_label())
            self._paragraph_cards.append(card)
            self._paras_layout.addWidget(card)
        if not paras:
            self._paras_layout.addWidget(self._placeholder("未发现与表头相关的段落。"))
        self._paras_layout.addStretch(1)

    def _placeholder(self, text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setStyleSheet("color: #53637a; padding: 30px;")
        lbl.setAlignment(Qt.AlignCenter)
        return lbl

    def _clear_layout(self, layout):
        while layout.count():
            item = layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

    def _toggle_all(self, v: bool):
        tab = self._tabs.currentIndex()
        cards = {0: self._table_cards, 1: self._figure_cards, 3: self._paragraph_cards}.get(tab, [])
        for c in cards:
            c._cb.setChecked(v)

    def _filter_paras(self, query: str):
        q = query.lower().strip()
        for card in self._paragraph_cards:
            if not q:
                card.setVisible(True)
            else:
                found = q in card.source_text.lower()
                card.setVisible(found)

    def _update_sel_label(self):
        t = sum(1 for c in self._table_cards if c.is_selected())
        f = sum(1 for c in self._figure_cards if c.is_selected())
        p = sum(1 for c in self._paragraph_cards if c.is_selected())
        self._sel_label.setText(f"已选: {t} 表格, {f} 图片, {p} 段落")
        self._extract_btn.setEnabled(t + f + p > 0)
        selected_lines = []
        for label, cards in (("表格", self._table_cards), ("图片", self._figure_cards), ("段落", self._paragraph_cards)):
            for card in cards:
                if card.is_selected():
                    name = getattr(card, "display_name", "") or ""
                    page = getattr(card, "page", "") or ""
                    selected_lines.append(f"<b>{label}</b> {escape(page)} - {escape(name[:120])}")
        self._selected_view.setHtml("<br>".join(selected_lines) if selected_lines else "")

    def _do_discover(self):
        rid = self._current_resource_id
        if not rid and self._all_rows:
            rid = self._all_rows[0].get("resource_id")
        if rid and self.discover_requested:
            self.discover_requested(rid)

    def _do_extract(self):
        selected = []
        for c in self._table_cards:
            if c.is_selected():
                selected.append(("table", c.asset_id))
        for c in self._figure_cards:
            if c.is_selected():
                selected.append(("figure", c.evidence_id))
        for c in self._paragraph_cards:
            if c.is_selected():
                selected.append(("text", c.evidence_id))
        if selected and self.extract_selected_requested:
            self.extract_selected_requested(selected)
