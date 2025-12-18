"""
Animation helpers for smooth UI transitions in chat interface.
"""

from PySide6.QtCore import QPropertyAnimation, QEasingCurve, QAbstractAnimation, QPoint
from PySide6.QtWidgets import QWidget, QGraphicsOpacityEffect


class FadeSlideInAnimation:
    """Combined fade and slide in animation."""

    @staticmethod
    def apply(widget: QWidget, direction: str = "up", duration: int = 300):
        """
        Apply combined fade + slide in effect.

        Args:
            widget: Widget to animate
            direction: Direction to slide from
            duration: Animation duration in milliseconds
        """
        # Inline fade animation
        opacity_effect = QGraphicsOpacityEffect(widget)
        widget.setGraphicsEffect(opacity_effect)

        fade_anim = QPropertyAnimation(opacity_effect, b"opacity")
        fade_anim.setDuration(duration)
        fade_anim.setStartValue(0.0)
        fade_anim.setEndValue(1.0)
        fade_anim.setEasingCurve(QEasingCurve.InOutQuad)
        fade_anim.start(QAbstractAnimation.DeleteWhenStopped)
        widget._fade_animation = fade_anim

        # Inline slide animation
        initial_pos = widget.pos()
        distance = 15

        # Calculate start position
        if direction == "up":
            start_pos = QPoint(initial_pos.x(), initial_pos.y() + distance)
        elif direction == "down":
            start_pos = QPoint(initial_pos.x(), initial_pos.y() - distance)
        elif direction == "left":
            start_pos = QPoint(initial_pos.x() + distance, initial_pos.y())
        else:  # right
            start_pos = QPoint(initial_pos.x() - distance, initial_pos.y())

        widget.move(start_pos)

        slide_anim = QPropertyAnimation(widget, b"pos")
        slide_anim.setDuration(duration)
        slide_anim.setStartValue(start_pos)
        slide_anim.setEndValue(initial_pos)
        slide_anim.setEasingCurve(QEasingCurve.OutCubic)
        slide_anim.start(QAbstractAnimation.DeleteWhenStopped)
        widget._slide_animation = slide_anim

        return (fade_anim, slide_anim)


