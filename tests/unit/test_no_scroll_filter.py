import pytest
from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QWheelEvent
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDoubleSpinBox,
    QLabel,
    QScrollArea,
    QSlider,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from apps.ui.components.no_scroll_filter import NoScrollConfigFilter


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def test_no_scroll_filter_blocks_combo_wheel_and_scrolls_parent(qapp):
    scroll = QScrollArea()
    container = QWidget()
    layout = QVBoxLayout(container)

    combo = QComboBox()
    combo.addItems(["Option 1", "Option 2", "Option 3"])
    combo.setCurrentIndex(0)
    layout.addWidget(combo)

    for i in range(50):
        layout.addWidget(QLabel(f"Row {i}"))

    scroll.setWidget(container)
    scroll.resize(300, 200)
    scroll.show()

    no_scroll_filter = NoScrollConfigFilter()
    qapp.installEventFilter(no_scroll_filter)

    try:
        initial_scroll = scroll.verticalScrollBar().value()
        assert combo.currentIndex() == 0

        # Simulate wheel event (delta = -120, scrolling down)
        wheel_event = QWheelEvent(
            QPointF(10, 10),
            QPointF(10, 10),
            QPoint(0, 0),
            QPoint(0, -120),
            Qt.MouseButton.NoButton,
            Qt.KeyboardModifier.NoModifier,
            Qt.ScrollPhase.NoScrollPhase,
            False,
        )

        qapp.sendEvent(combo, wheel_event)

        # The combo selection must NOT have changed
        assert combo.currentIndex() == 0

        # The parent scrollbar must have moved down
        assert scroll.verticalScrollBar().value() > initial_scroll
    finally:
        qapp.removeEventFilter(no_scroll_filter)
        scroll.close()


def test_no_scroll_filter_blocks_spinbox_wheel(qapp):
    scroll = QScrollArea()
    container = QWidget()
    layout = QVBoxLayout(container)

    spin = QSpinBox()
    spin.setValue(10)
    layout.addWidget(spin)

    dspin = QDoubleSpinBox()
    dspin.setValue(5.5)
    layout.addWidget(dspin)

    slider = QSlider(Qt.Orientation.Horizontal)
    slider.setValue(50)
    layout.addWidget(slider)

    for i in range(50):
        layout.addWidget(QLabel(f"Row {i}"))

    scroll.setWidget(container)
    scroll.resize(300, 200)
    scroll.show()

    no_scroll_filter = NoScrollConfigFilter()
    qapp.installEventFilter(no_scroll_filter)

    try:
        assert spin.value() == 10
        assert dspin.value() == 5.5
        assert slider.value() == 50

        wheel_event = QWheelEvent(
            QPointF(10, 10),
            QPointF(10, 10),
            QPoint(0, 0),
            QPoint(0, -120),
            Qt.MouseButton.NoButton,
            Qt.KeyboardModifier.NoModifier,
            Qt.ScrollPhase.NoScrollPhase,
            False,
        )

        qapp.sendEvent(spin, wheel_event)
        qapp.sendEvent(dspin, wheel_event)
        qapp.sendEvent(slider, wheel_event)

        # None of the values should change
        assert spin.value() == 10
        assert dspin.value() == 5.5
        assert slider.value() == 50
    finally:
        qapp.removeEventFilter(no_scroll_filter)
        scroll.close()
