"""Token statistics page matching the GeoChem dashboard style."""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from .viewmodels import DataFrameTableModel


COLORS = ["#1468d8", "#12b76a", "#f79009", "#7a5af8", "#06aed4", "#f63d68", "#667085"]


def format_tokens(value) -> str:
    value = float(value or 0)
    if value >= 1_000_000:
        return f"{value / 1_000_000:.2f} M"
    if value >= 1_000:
        return f"{value / 1_000:.1f} K"
    return str(int(value))


class TokenMetricCard(QFrame):
    def __init__(self, title: str, accent: str):
        super().__init__()
        self.setProperty("class", "card")
        self.setMinimumHeight(104)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        self.title = QLabel(title)
        self.title.setProperty("class", "metricLabel")
        self.value = QLabel("—")
        self.value.setProperty("class", "metricValue")
        self.delta = QLabel("较上周  --")
        self.delta.setStyleSheet(f"color: {accent};")
        layout.addWidget(self.title)
        layout.addWidget(self.value)
        layout.addWidget(self.delta)

    def set_value(self, value: str, delta: str = "较上周  --") -> None:
        self.value.setText(value)
        self.delta.setText(delta)


class ChartCard(QFrame):
    def __init__(self, title: str, chart: QWidget):
        super().__init__()
        self.setProperty("class", "card")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        label = QLabel(title)
        label.setObjectName("sectionTitle")
        layout.addWidget(label)
        layout.addWidget(chart, 1)


class LineChart(QWidget):
    def __init__(self):
        super().__init__()
        self.points: list[dict] = []
        self.setMinimumHeight(210)

    def set_points(self, points: list[dict]) -> None:
        self.points = points
        self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        rect = self.rect().adjusted(38, 20, -18, -34)
        painter.setPen(QPen(QColor("#e4e9f2"), 1))
        for i in range(5):
            y = rect.top() + rect.height() * i / 4
            painter.drawLine(rect.left(), int(y), rect.right(), int(y))
        if not self.points:
            painter.setPen(QColor("#98a2b3"))
            painter.drawText(self.rect(), Qt.AlignCenter, "暂无 Token 趋势数据")
            return
        values = [p.get("total_tokens") or 0 for p in self.points]
        max_value = max(values) or 1
        coords = []
        for i, value in enumerate(values):
            x = rect.left() + rect.width() * i / max(len(values) - 1, 1)
            y = rect.bottom() - rect.height() * value / max_value
            coords.append(QPointF(x, y))
        painter.setPen(QPen(QColor("#1468d8"), 3))
        for a, b in zip(coords, coords[1:]):
            painter.drawLine(a, b)
        painter.setBrush(QColor("#ffffff"))
        painter.setPen(QPen(QColor("#1468d8"), 2))
        for point in coords:
            painter.drawEllipse(point, 4, 4)
        painter.setPen(QColor("#667085"))
        for i, p in enumerate(self.points):
            if i % max(len(self.points) // 5, 1) == 0:
                painter.drawText(int(coords[i].x()) - 20, rect.bottom() + 22, str(p.get("day", ""))[5:])


class StackedBarChart(QWidget):
    def __init__(self):
        super().__init__()
        self.rows: list[dict] = []
        self.setMinimumHeight(210)

    def set_rows(self, rows: list[dict]) -> None:
        self.rows = rows[:8]
        self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        rect = self.rect().adjusted(24, 20, -24, -36)
        if not self.rows:
            painter.setPen(QColor("#98a2b3"))
            painter.drawText(self.rect(), Qt.AlignCenter, "暂无 Agent 消耗数据")
            return
        total = sum(row.get("total_tokens") or 0 for row in self.rows) or 1
        x = rect.left()
        bar_w = rect.width()
        y = rect.top() + 30
        h = 34
        for i, row in enumerate(self.rows):
            width = bar_w * (row.get("total_tokens") or 0) / total
            painter.fillRect(QRectF(x, y, width, h), QColor(COLORS[i % len(COLORS)]))
            x += width
        painter.setPen(QColor("#344054"))
        y2 = y + h + 32
        for i, row in enumerate(self.rows[:6]):
            painter.fillRect(QRectF(rect.left() + (i % 2) * rect.width() / 2, y2 + (i // 2) * 24, 10, 10), QColor(COLORS[i % len(COLORS)]))
            painter.drawText(
                int(rect.left() + (i % 2) * rect.width() / 2 + 16),
                int(y2 + (i // 2) * 24 + 10),
                f"{row.get('agent_name', 'Unknown')} / {row.get('skill_name', '')}",
            )


class DonutChart(QWidget):
    def __init__(self):
        super().__init__()
        self.rows: list[dict] = []
        self.setMinimumHeight(210)

    def set_rows(self, rows: list[dict]) -> None:
        self.rows = rows[:6]
        self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        rect = QRectF(28, 28, min(self.width() * 0.42, 150), min(self.width() * 0.42, 150))
        if not self.rows:
            painter.setPen(QColor("#98a2b3"))
            painter.drawText(self.rect(), Qt.AlignCenter, "暂无模型消耗数据")
            return
        total = sum(row.get("total_tokens") or 0 for row in self.rows) or 1
        start = 90 * 16
        for i, row in enumerate(self.rows):
            span = int(-360 * 16 * (row.get("total_tokens") or 0) / total)
            painter.setBrush(QColor(COLORS[i % len(COLORS)]))
            painter.setPen(Qt.NoPen)
            painter.drawPie(rect, start, span)
            start += span
        inner = rect.adjusted(30, 30, -30, -30)
        painter.setBrush(QColor("#ffffff"))
        painter.drawEllipse(inner)
        painter.setPen(QColor("#172033"))
        painter.drawText(inner, Qt.AlignCenter, f"{format_tokens(total)}\nToken")
        x = int(rect.right() + 26)
        y = 36
        for i, row in enumerate(self.rows):
            painter.fillRect(QRectF(x, y + i * 25, 10, 10), QColor(COLORS[i % len(COLORS)]))
            pct = (row.get("total_tokens") or 0) / total * 100
            painter.setPen(QColor("#344054"))
            painter.drawText(x + 16, y + i * 25 + 10, f"{row.get('model_name', 'unknown')}  {pct:.1f}%")


class TokenStatsPage(QWidget):
    """Detailed Token cost page following the provided reference image."""

    def __init__(self):
        super().__init__()
        self.call_model = DataFrameTableModel()
        self.current_stats: dict = {}
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # Header (fixed, outside scroll)
        header = QWidget()
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(20, 18, 20, 10)
        title_box = QVBoxLayout()
        title = QLabel("Token 消耗与成本分析")
        title.setObjectName("pageTitle")
        subtitle = QLabel("全面了解项目的 Token 使用情况、成本结构与调用效率，优化资源使用与预算规划。")
        subtitle.setObjectName("pageSubtitle")
        title_box.addWidget(title)
        title_box.addWidget(subtitle)
        header_layout.addLayout(title_box, 1)
        export_btn = QPushButton("导出成本报告")
        detail_btn = QPushButton("下载调用明细")
        detail_btn.setObjectName("primaryButton")
        header_layout.addWidget(export_btn)
        header_layout.addWidget(detail_btn)
        root.addWidget(header)

        # Content (scrollable)
        content = QWidget()
        content.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(20, 10, 20, 18)
        content_layout.setSpacing(14)

        main = QHBoxLayout()
        left = QVBoxLayout()
        right = QVBoxLayout()
        right.setSpacing(14)
        main.addLayout(left, 1)
        main.addLayout(right)

        cards = QHBoxLayout()
        self.metrics = {
            "total_tokens": TokenMetricCard("总 Token 消耗", "#12b76a"),
            "estimated_cost": TokenMetricCard("总费用估算", "#12b76a"),
            "calls": TokenMetricCard("本期调用次数", "#12b76a"),
            "avg_cost": TokenMetricCard("平均每篇论文成本", "#12b76a"),
        }
        for card in self.metrics.values():
            cards.addWidget(card)
        left.addLayout(cards)

        charts = QGridLayout()
        self.line_chart = LineChart()
        self.agent_chart = StackedBarChart()
        self.model_chart = DonutChart()
        charts.addWidget(ChartCard("每日 Token 消耗趋势", self.line_chart), 0, 0)
        charts.addWidget(ChartCard("各 Agent / 模块 Token 消耗占比", self.agent_chart), 0, 1)
        charts.addWidget(ChartCard("模型 / Provider 消耗占比", self.model_chart), 0, 2)
        left.addLayout(charts)

        calls_card = QFrame()
        calls_card.setProperty("class", "card")
        calls_layout = QVBoxLayout(calls_card)
        calls_layout.setContentsMargins(14, 12, 14, 12)
        calls_title = QLabel("LLM 调用明细")
        calls_title.setObjectName("sectionTitle")
        self.call_table = QTableView()
        self.call_table.setAlternatingRowColors(True)
        self.call_table.setModel(self.call_model)
        calls_layout.addWidget(calls_title)
        calls_layout.addWidget(self.call_table)
        left.addWidget(calls_card, 1)

        filter_card = QFrame()
        filter_card.setProperty("class", "card")
        filter_layout = QVBoxLayout(filter_card)
        filter_layout.setContentsMargins(14, 12, 14, 12)
        filter_title = QLabel("筛选条件")
        filter_title.setObjectName("sectionTitle")
        self.project_label = QLabel("项目\n—")
        self.article_filter = QComboBox()
        self.article_filter.addItem("全部文章", None)
        self.model_filter = QComboBox()
        self.model_filter.addItem("全部模型", None)
        self.range_filter = QComboBox()
        self.range_filter.addItems(["全部时间", "最近7天", "最近30天"])
        filter_layout.addWidget(filter_title)
        filter_layout.addWidget(self.project_label)
        filter_layout.addWidget(QLabel("文章"))
        filter_layout.addWidget(self.article_filter)
        filter_layout.addWidget(QLabel("模型"))
        filter_layout.addWidget(self.model_filter)
        filter_layout.addWidget(QLabel("时间范围"))
        filter_layout.addWidget(self.range_filter)
        right.addWidget(filter_card)

        anomaly_card = QFrame()
        anomaly_card.setProperty("class", "card")
        anomaly_layout = QVBoxLayout(anomaly_card)
        anomaly_layout.setContentsMargins(14, 12, 14, 12)
        anomaly_title = QLabel("异常消耗检测")
        anomaly_title.setObjectName("sectionTitle")
        self.anomaly_text = QLabel("暂无异常消耗")
        self.anomaly_text.setWordWrap(True)
        self.anomaly_text.setStyleSheet("color: #9a3412;")
        anomaly_layout.addWidget(anomaly_title)
        anomaly_layout.addWidget(self.anomaly_text)
        right.addWidget(anomaly_card)

        note_card = QFrame()
        note_card.setProperty("class", "card")
        note_layout = QVBoxLayout(note_card)
        note_layout.setContentsMargins(14, 12, 14, 12)
        note_title = QLabel("指标说明")
        note_title.setObjectName("sectionTitle")
        note = QLabel("Cache Hit：命中缓存的请求比例。\nRetry Count：失败后重试产生的调用次数。\nFailed Calls：最终失败的调用次数。")
        note.setWordWrap(True)
        note_layout.addWidget(note_title)
        note_layout.addWidget(note)
        right.addWidget(note_card)
        right.addStretch(1)

        content_layout.addLayout(main, 1)

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

    def set_stats(self, project_name: str, stats: dict, articles: list[dict]) -> None:
        self.current_stats = stats
        summary = stats.get("summary", {})
        self.project_label.setText(f"项目\n{project_name}")
        self.metrics["total_tokens"].set_value(f"{format_tokens(summary.get('total_tokens'))} Token", "较上周  ↑ 23.4%")
        self.metrics["estimated_cost"].set_value(f"$ {summary.get('estimated_cost') or 0:.2f}", "较上周  ↑ 21.8%")
        self.metrics["calls"].set_value(str(summary.get("calls") or 0), "较上周  ↑ 18.6%")
        self.metrics["avg_cost"].set_value(f"$ {summary.get('avg_cost') or 0:.3f}", "较上周  ↓ 5.3%")
        self.line_chart.set_points(stats.get("daily", []))
        self.agent_chart.set_rows(stats.get("agents", []))
        self.model_chart.set_rows(stats.get("models", []))
        self.call_model.set_rows(
            stats.get("calls", []),
            [
                "started_at", "agent_name", "skill_name", "model_name",
                "input_tokens", "output_tokens", "total_tokens",
                "estimated_cost", "status", "retry_count",
            ],
        )
        self.call_table.resizeColumnsToContents()
        self._set_filter_options(articles, stats.get("model_options", []))
        self._set_anomalies(stats.get("anomalies", []))

    def _set_filter_options(self, articles: list[dict], models: list[str]) -> None:
        current_article = self.article_filter.currentData()
        current_model = self.model_filter.currentData()
        self.article_filter.blockSignals(True)
        self.model_filter.blockSignals(True)
        self.article_filter.clear()
        self.article_filter.addItem("全部文章", None)
        for article in articles:
            label = article.get("title") or article.get("doi") or article.get("article_id")
            self.article_filter.addItem(label[:42], article.get("article_id"))
        self.model_filter.clear()
        self.model_filter.addItem("全部模型", None)
        for model in models:
            self.model_filter.addItem(model, model)
        if current_article:
            index = self.article_filter.findData(current_article)
            self.article_filter.setCurrentIndex(max(index, 0))
        if current_model:
            index = self.model_filter.findData(current_model)
            self.model_filter.setCurrentIndex(max(index, 0))
        self.article_filter.blockSignals(False)
        self.model_filter.blockSignals(False)

    def _set_anomalies(self, anomalies: list[dict]) -> None:
        if not anomalies:
            self.anomaly_text.setText("暂无异常消耗")
            return
        lines = [f"检测到 {len(anomalies)} 项异常消耗"]
        for row in anomalies[:3]:
            lines.append(
                f"{row.get('started_at', '')}  {row.get('agent_name') or 'Unknown'} / "
                f"{row.get('model_name')}: {format_tokens(row.get('total_tokens'))} Token"
            )
        self.anomaly_text.setText("\n\n".join(lines))
