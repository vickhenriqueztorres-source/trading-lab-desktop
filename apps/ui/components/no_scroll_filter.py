from __future__ import annotations

from PySide6.QtCore import QEvent, QObject
from PySide6.QtWidgets import (
    QAbstractSpinBox,
    QApplication,
    QComboBox,
    QScrollArea,
    QSlider,
)


class NoScrollConfigFilter(QObject):
    """
    Global event filter that prevents mouse wheel from modifying form inputs
    (QComboBox, QAbstractSpinBox, QSlider) and smoothly redirects the wheel
    event to the parent scroll area so page navigation is never interrupted.
    """

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if event.type() == QEvent.Type.Wheel and isinstance(
            watched, (QComboBox, QAbstractSpinBox, QSlider)
        ):
            # If combo box dropdown popup list is currently visible, allow scrolling inside it
            if isinstance(watched, QComboBox):
                view = watched.view()
                if view is not None and view.isVisible():
                    return False

            # Ignore the event on the control so it does NOT change selection or value
            event.ignore()

            # Propagate wheel event to the enclosing QScrollArea viewport so the page scrolls
            parent = watched.parentWidget()
            while parent is not None:
                if isinstance(parent, QScrollArea):
                    viewport = parent.viewport()
                    if viewport is not None:
                        return QApplication.sendEvent(viewport, event)
                parent = parent.parentWidget()
            return True

        return super().eventFilter(watched, event)


__all__ = ["NoScrollConfigFilter"]
