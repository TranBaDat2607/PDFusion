"""
Animation helpers for smooth UI transitions in chat interface.
"""

from PySide6.QtCore import QPropertyAnimation, QEasingCurve, QAbstractAnimation, QPoint, Property
from PySide6.QtWidgets import QWidget, QGraphicsOpacityEffect


class FadeInAnimation:
    """Fade in animation for widgets."""

    @staticmethod
    def apply(widget: QWidget, duration: int = 300):
        """
        Apply fade-in effect to widget.

        Args:
            widget: Widget to animate
            duration: Animation duration in milliseconds
        """
        # Create opacity effect
        opacity_effect = QGraphicsOpacityEffect(widget)
        widget.setGraphicsEffect(opacity_effect)

        # Create animation
        animation = QPropertyAnimation(opacity_effect, b"opacity")
        animation.setDuration(duration)
        animation.setStartValue(0.0)
        animation.setEndValue(1.0)
        animation.setEasingCurve(QEasingCurve.InOutQuad)

        # Start animation
        animation.start(QAbstractAnimation.DeleteWhenStopped)

        # Store animation reference to prevent garbage collection
        widget._fade_animation = animation

        return animation


class SlideInAnimation:
    """Slide in animation for widgets."""

    @staticmethod
    def apply(widget: QWidget, direction: str = "up", duration: int = 300, distance: int = 20):
        """
        Apply slide-in effect to widget.

        Args:
            widget: Widget to animate
            direction: Direction to slide from ("up", "down", "left", "right")
            duration: Animation duration in milliseconds
            distance: Distance to slide in pixels
        """
        # Get initial position
        initial_pos = widget.pos()

        # Calculate start position based on direction
        if direction == "up":
            start_pos = QPoint(initial_pos.x(), initial_pos.y() + distance)
        elif direction == "down":
            start_pos = QPoint(initial_pos.x(), initial_pos.y() - distance)
        elif direction == "left":
            start_pos = QPoint(initial_pos.x() + distance, initial_pos.y())
        else:  # right
            start_pos = QPoint(initial_pos.x() - distance, initial_pos.y())

        # Set start position
        widget.move(start_pos)

        # Create animation
        animation = QPropertyAnimation(widget, b"pos")
        animation.setDuration(duration)
        animation.setStartValue(start_pos)
        animation.setEndValue(initial_pos)
        animation.setEasingCurve(QEasingCurve.OutCubic)

        # Start animation
        animation.start(QAbstractAnimation.DeleteWhenStopped)

        # Store animation reference
        widget._slide_animation = animation

        return animation


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
        # Apply both animations
        fade_anim = FadeInAnimation.apply(widget, duration)
        slide_anim = SlideInAnimation.apply(widget, direction, duration, distance=15)

        return (fade_anim, slide_anim)


class HeightAnimation:
    """Animate widget height changes (for collapsible sections)."""

    @staticmethod
    def apply(widget: QWidget, start_height: int, end_height: int, duration: int = 250):
        """
        Animate widget height change.

        Args:
            widget: Widget to animate
            start_height: Starting height
            end_height: Ending height
            duration: Animation duration in milliseconds
        """
        animation = QPropertyAnimation(widget, b"maximumHeight")
        animation.setDuration(duration)
        animation.setStartValue(start_height)
        animation.setEndValue(end_height)
        animation.setEasingCurve(QEasingCurve.InOutQuad)

        animation.start(QAbstractAnimation.DeleteWhenStopped)

        widget._height_animation = animation

        return animation


class PulseAnimation:
    """Pulse animation for drawing attention."""

    @staticmethod
    def apply(widget: QWidget, duration: int = 1000, pulses: int = 3):
        """
        Apply pulse effect (opacity oscillation).

        Args:
            widget: Widget to animate
            duration: Duration of one pulse cycle
            pulses: Number of pulses
        """
        opacity_effect = QGraphicsOpacityEffect(widget)
        widget.setGraphicsEffect(opacity_effect)

        animation = QPropertyAnimation(opacity_effect, b"opacity")
        animation.setDuration(duration)
        animation.setStartValue(1.0)
        animation.setKeyValueAt(0.5, 0.5)
        animation.setEndValue(1.0)
        animation.setLoopCount(pulses)
        animation.setEasingCurve(QEasingCurve.InOutSine)

        animation.start(QAbstractAnimation.DeleteWhenStopped)

        widget._pulse_animation = animation

        return animation
