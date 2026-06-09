import ctypes
from PyQt6.QtWidgets import QWidget
from PyQt6.QtCore import Qt, QTimer, QRectF, QPointF, pyqtSignal
from PyQt6.QtGui import QPainter, QColor, QPen

class OverlayRadar(QWidget):
    positionChanged = pyqtSignal(int, int)   # committed position (persist to disk)
    positionPreview = pyqtSignal(int, int)   # live drag frames (UI readout only)

    def __init__(self):
        super().__init__()
        
        self.setWindowTitle("Visual Audio Overlay")
        self.base_window_flags = (
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.WindowStaysOnTopHint |
            Qt.WindowType.Tool
        )
        self.drag_enabled = False
        self.drag_start_global = None
        self.drag_start_window = None
        self._apply_window_flags()
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        
        self.resize(300, 300)
        
        self.accent_color = QColor(255, 80, 20)
        self.stroke_width = 6
        self.blips = []
        
        self.decay_timer = QTimer(self)
        self.decay_timer.timeout.connect(self.decay_signal)
        self.decay_timer.start(30)

    def _apply_window_flags(self):
        flags = self.base_window_flags
        if not self.drag_enabled:
            flags |= Qt.WindowType.WindowTransparentForInput
        self.setWindowFlags(flags)

    def set_drag_enabled(self, enabled):
        enabled = bool(enabled)
        if self.drag_enabled == enabled:
            return

        was_visible = self.isVisible()
        pos = self.pos()
        self.drag_enabled = enabled
        self.drag_start_global = None
        self.drag_start_window = None
        self.setCursor(Qt.CursorShape.OpenHandCursor if enabled else Qt.CursorShape.ArrowCursor)
        self._apply_window_flags()
        self.move(pos)

        if was_visible:
            self.show()
            self.raise_()
            self._remove_win11_border()
        self.update()

    def showEvent(self, event):
        super().showEvent(event)
        self._remove_win11_border()

    def _remove_win11_border(self):
        """
        Windows 11 applies rounded corners and a border/shadow to ALL windows,
        including frameless ones. This opts out via the DWM API.
        Safe on non-Windows - the try/except swallows it silently.
        """
        try:
            hwnd = int(self.winId())

            # 1. Disable rounded corners
            #    DWMWA_WINDOW_CORNER_PREFERENCE = 33, DWMWCP_DONOTROUND = 1
            ctypes.windll.dwmapi.DwmSetWindowAttribute(
                hwnd, 33,
                ctypes.byref(ctypes.c_int(1)),
                ctypes.sizeof(ctypes.c_int)
            )

            # 2. Remove drop shadow / border glow
            class MARGINS(ctypes.Structure):
                _fields_ = [
                    ("cxLeftWidth",    ctypes.c_int),
                    ("cxRightWidth",   ctypes.c_int),
                    ("cyTopHeight",    ctypes.c_int),
                    ("cyBottomHeight", ctypes.c_int),
                ]
            ctypes.windll.dwmapi.DwmExtendFrameIntoClientArea(
                hwnd, ctypes.byref(MARGINS(0, 0, 0, 0))
            )
        except Exception:
            pass

    def set_accent_color(self, hex_color):
        self.accent_color = QColor(hex_color)
        self.update()
        
    def set_stroke_width(self, width):
        self.stroke_width = width
        self.update()

    def mousePressEvent(self, event):
        if not self.drag_enabled or event.button() != Qt.MouseButton.LeftButton:
            return super().mousePressEvent(event)

        self.drag_start_global = event.globalPosition().toPoint()
        self.drag_start_window = self.pos()
        self.setCursor(Qt.CursorShape.ClosedHandCursor)
        event.accept()

    def mouseMoveEvent(self, event):
        if not self.drag_enabled or self.drag_start_global is None or self.drag_start_window is None:
            return super().mouseMoveEvent(event)

        delta = event.globalPosition().toPoint() - self.drag_start_global
        new_pos = self.drag_start_window + delta
        self.move(new_pos)
        # Live update only - persisting every frame hammers the disk (issue #3).
        self.positionPreview.emit(new_pos.x(), new_pos.y())
        event.accept()

    def mouseReleaseEvent(self, event):
        if not self.drag_enabled or event.button() != Qt.MouseButton.LeftButton:
            return super().mouseReleaseEvent(event)

        self.drag_start_global = None
        self.drag_start_window = None
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        pos = self.pos()
        self.positionChanged.emit(pos.x(), pos.y())
        event.accept()
        
    def decay_signal(self):
        decay_rate = 0.04
        active_blips = []
        for blip in self.blips:
            blip['life'] -= decay_rate
            if blip['life'] > 0:
                active_blips.append(blip)
        self.blips = active_blips
        self.update()
        
    def update_audio_data(self, angle, intensity):
        visual_gain = 5.0
        clamped_intensity = min(1.0, intensity * visual_gain)
        
        found = False
        for blip in self.blips:
            if abs(blip['angle'] - angle) < 20.0:
                blip['life'] = max(blip['life'], clamped_intensity)
                found = True
                break
                
        if not found:
            self.blips.append({'angle': angle, 'life': clamped_intensity})
            
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        if self.drag_enabled:
            # Layered windows can be hard to hit-test on fully transparent pixels.
            # A nearly invisible fill makes the whole radar box draggable in setup mode.
            painter.fillRect(self.rect(), QColor(255, 255, 255, 8))
        
        width = self.width()
        height = self.height()
        center = QPointF(width / 2, height / 2)
        radius = min(width, height) / 2 * 0.8
        
        base_pen = QPen(QColor(255, 255, 255, 30))
        base_pen.setWidth(2)
        painter.setPen(base_pen)
        painter.drawEllipse(center, radius, radius)
        
        for blip in self.blips:
            opacity = int(blip['life'] * 255)
            arc_color = QColor(
                self.accent_color.red(),
                self.accent_color.green(),
                self.accent_color.blue(),
                opacity
            )
            pen = QPen(arc_color)
            pen.setWidth(self.stroke_width)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            painter.setPen(pen)
            
            center_pyqt_angle = 90 - blip['angle']
            span_degrees = 35
            start_deg = center_pyqt_angle - (span_degrees / 2)
            
            start_angle_16 = int(start_deg * 16)
            span_angle_16  = int(span_degrees * 16)
            
            rect = QRectF(center.x() - radius, center.y() - radius, radius * 2, radius * 2)
            painter.drawArc(rect, start_angle_16, span_angle_16)

        if self.drag_enabled:
            setup_pen = QPen(QColor(255, 255, 255, 120))
            setup_pen.setWidth(1)
            setup_pen.setStyle(Qt.PenStyle.DashLine)
            painter.setPen(setup_pen)
            painter.drawEllipse(center, radius + 8, radius + 8)
