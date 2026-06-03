"""Shared small widgets for the PySide desktop app."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QLabel, QScrollArea, QSizePolicy, QVBoxLayout, QWidget


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
