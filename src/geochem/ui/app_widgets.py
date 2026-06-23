"""Shared small widgets for the PySide desktop app."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)


def wrap_in_scroll(widget: QWidget) -> QScrollArea:
    """Wrap a page body in a vertical scroll area."""
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
    """Compact metric card used across dashboard-style UI pages."""

    def __init__(self, label: str, value: str = "0", hint: str = "较上次  --", accent: str = "#1468d8"):
        super().__init__()
        self.setProperty("class", "card")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        self.label = QLabel(label)
        self.label.setProperty("class", "metricLabel")
        self.value = QLabel(value)
        self.value.setProperty("class", "metricValue")
        self.hint = QLabel(hint)
        self.hint.setStyleSheet(f"color: {accent};")
        layout.addWidget(self.label)
        layout.addWidget(self.value)
        layout.addWidget(self.hint)

    def set_value(self, value) -> None:
        self.value.setText(str(value))


_RISK_COLORS = {
    "high": ("#d92d20", "#fff8f7", "#f5b5ad"),
    "medium": ("#b54708", "#fffaeb", "#fedf89"),
    "low": ("#027a48", "#ecfdf3", "#abefc6"),
}

_STATUS_COLORS = {
    "confirmed": ("#027a48", "#ecfdf3"),
    "pending": ("#b54708", "#fffaeb"),
    "rejected": ("#d92d20", "#fff8f7"),
    "deferred": ("#53637a", "#f8fafd"),
}


class RiskBadge(QLabel):
    """Colored badge for risk_level display."""

    def __init__(self, text: str = ""):
        super().__init__(text)
        self.setAlignment(Qt.AlignCenter)
        self.setFixedHeight(24)
        self.setMinimumWidth(48)
        self._apply(text)

    def set_risk(self, level: str) -> None:
        self._apply(level)

    def _apply(self, level: str) -> None:
        level = (level or "").lower()
        colors = _RISK_COLORS.get(level, ("#53637a", "#f8fafd", "#dfe5ee"))
        self.setText(level.upper() if level else "—")
        self.setStyleSheet(
            f"color: {colors[0]}; background: {colors[1]}; border: 1px solid {colors[2]};"
            f"border-radius: 4px; padding: 2px 8px; font-size: 11px; font-weight: 650;"
        )


class StatusBadge(QLabel):
    """Colored badge for review/rule status."""

    def __init__(self, text: str = ""):
        super().__init__(text)
        self.setAlignment(Qt.AlignCenter)
        self.setFixedHeight(24)
        self.setMinimumWidth(48)
        self._apply(text)

    def set_status(self, status: str) -> None:
        self._apply(status)

    def _apply(self, status: str) -> None:
        status = (status or "").lower()
        fg, bg = _STATUS_COLORS.get(status, ("#53637a", "#f8fafd"))
        self.setText(status if status else "—")
        self.setStyleSheet(
            f"color: {fg}; background: {bg}; border: 1px solid {fg}33;"
            f"border-radius: 4px; padding: 2px 8px; font-size: 11px; font-weight: 650;"
        )


class HeaderPage(QWidget):
    """Base for pages with a standard title/subtitle header block."""

    def __init__(self, title: str, subtitle: str):
        super().__init__()
        self._root = QVBoxLayout(self)
        self._root.setContentsMargins(0, 0, 0, 0)
        self._root.setSpacing(0)

        header = QWidget()
        header_layout = QVBoxLayout(header)
        header_layout.setContentsMargins(20, 18, 20, 10)
        header_layout.setSpacing(4)

        title_row = QHBoxLayout()
        title_label = QLabel(title)
        title_label.setObjectName("pageTitle")
        title_row.addWidget(title_label)
        title_row.addStretch(1)
        self._title_row = title_row
        header_layout.addLayout(title_row)

        subtitle_label = QLabel(subtitle)
        subtitle_label.setObjectName("pageSubtitle")
        subtitle_label.setWordWrap(True)
        header_layout.addWidget(subtitle_label)

        self._root.addWidget(header)

        self._content = QWidget()
        self._content_layout = QVBoxLayout(self._content)
        self._content_layout.setContentsMargins(20, 10, 20, 18)
        self._content_layout.setSpacing(14)
        self._root.addWidget(wrap_in_scroll(self._content), 1)

    def add_title_widget(self, widget: QWidget) -> None:
        self._title_row.addWidget(widget)
