import ctypes
import time

from PyQt6.QtWidgets import QWidget
from PyQt6.QtCore import Qt, QTimer, QRectF, QPointF, pyqtSignal
from PyQt6.QtGui import QPainter, QColor, QPen

from direction import angle_diff

# Radar behaviour. These numbers define how the radar looks to the player, so
# changing any of them is a visual change, not a performance tweak.
TICK_MS = 30            # decay/repaint tick while at least one blip is visible
DECAY_PER_TICK = 0.04   # full-strength blip fades in 25 ticks (about 750 ms)
VISUAL_GAIN = 5.0       # audio intensity -> blip strength (clamped to 1.0)
BLIP_MERGE_DEG = 20.0   # a sound this close to an existing blip refreshes it
ARC_SPAN_DEG = 35       # angular width of one arc


class OverlayRadar(QWidget):
    positionChanged = pyqtSignal(int, int)   # committed position (persist to disk)
    positionPreview = pyqtSignal(int, int)   # live drag frames (UI readout only)

    def __init__(self, perf=None):
        super().__init__()

        # Optional perf_stats.PerfStats (VAO_PERF=1). None in normal use, so the
        # hot paths below pay one `is not None` check and nothing else.
        self._perf = perf

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

        # Default accent must match the dashboard's default swatch (#9751F2) so the
        # first Start looks the same as the UI shows, even before the user touches
        # the colour picker.
        self.accent_color = QColor("#9751F2")
        self.stroke_width = 6
        # Each blip is {'angle': deg, 'life': 0..1, 'fresh': bool}. 'fresh' marks
        # a life value set by an audio event while the timer was running; it has
        # not been painted yet, so the next tick shows it at full value before
        # decaying it (see decay_signal and update_audio_data).
        self.blips = []

        self.resize(300, 300)
        self._rebuild_paint_cache()

        # The overlay is a per-pixel-alpha (layered) window kept on top of the
        # game, and each repaint has to be re-composited by Windows over the
        # game's frames. So this timer only runs while something is fading: the
        # first audio event after silence starts it, and it stops itself once
        # the last blip is gone, so a silent overlay does not repaint at all.
        # It never runs while the overlay is hidden (hideEvent stops it and
        # hidden events are dropped).
        self.decay_timer = QTimer(self)
        self.decay_timer.setInterval(TICK_MS)
        self.decay_timer.timeout.connect(self.decay_signal)

    # ── Paint cache ────────────────────────────────────────────────────
    def _rebuild_paint_cache(self):
        """Build every pen, colour and rectangle the paint path needs.

        This is the ONE place that derives paint objects from the widget size
        and the appearance settings. Anything that changes those (resize, accent
        colour, stroke width, and future settings such as a size scale, a
        maximum alert opacity or turning the base ring off) must call this, then
        update(). paintEvent itself only adjusts the arc alpha per blip.
        """
        w, h = self.width(), self.height()
        self._full_rect = self.rect()
        self._center = QPointF(w / 2, h / 2)
        self._radius = min(w, h) / 2 * 0.8
        r = self._radius
        self._arc_rect = QRectF(self._center.x() - r, self._center.y() - r, r * 2, r * 2)

        self._base_pen = QPen(QColor(255, 255, 255, 30))
        self._base_pen.setWidth(2)

        # Accent without its own alpha; paintEvent sets the alpha per blip.
        self._arc_color = QColor(
            self.accent_color.red(),
            self.accent_color.green(),
            self.accent_color.blue(),
        )
        self._arc_pen = QPen(self._arc_color)
        self._arc_pen.setWidth(self.stroke_width)
        self._arc_pen.setCapStyle(Qt.PenCapStyle.RoundCap)

        # Move mode decorations.
        self._drag_fill = QColor(255, 255, 255, 8)
        self._setup_pen = QPen(QColor(255, 255, 255, 120))
        self._setup_pen.setWidth(1)
        self._setup_pen.setStyle(Qt.PenStyle.DashLine)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._rebuild_paint_cache()

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
        # No unconditional timer start here: an empty radar (and the static Move
        # mode decorations) is painted once by the show itself. Blips are always
        # empty at this point (hideEvent clears them and hidden events are
        # dropped); the check only keeps the invariant "blips present -> ticking".
        if self.blips:
            self.decay_timer.start()
        self._remove_win11_border()

    def hideEvent(self, event):
        super().hideEvent(event)
        self.decay_timer.stop()
        self.blips = []

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
        self._rebuild_paint_cache()
        self.update()

    def set_stroke_width(self, width):
        self.stroke_width = width
        self._rebuild_paint_cache()
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
        """Timer tick: fade every blip one step and repaint.

        While the timer runs this is the only place that repaints, so the
        overlay repaints once per tick no matter how many audio events arrive.
        """
        if self._perf is not None:
            self._perf.record_tick()

        active_blips = []
        for blip in self.blips:
            if blip.get('fresh'):
                # Set by an audio event since the last paint: show it once at
                # full value first. Without this, moving the repaint from the
                # audio event to the tick would make every new arc start one
                # step dimmer and fade one tick sooner than before.
                blip['fresh'] = False
            else:
                blip['life'] -= DECAY_PER_TICK
            if blip['life'] > 0:
                active_blips.append(blip)
        self.blips = active_blips
        self.update()

        if not active_blips:
            # The last blip just faded. The update() above is the final repaint
            # that clears its arc; after that there is nothing to animate, so
            # stop ticking until the next sound (zero repaints in silence).
            self.decay_timer.stop()

    def update_audio_data(self, angle, intensity):
        if not self.isVisible():
            # Hidden: drop the event. Nothing would show it, and keeping it would
            # let stale blips pile up and flash on the next show.
            if self._perf is not None:
                self._perf.record_dropped()
            return
        if self._perf is not None:
            self._perf.record_event()

        clamped_intensity = min(1.0, intensity * VISUAL_GAIN)
        # While ticking, the next tick paints this event (fresh=True keeps it at
        # full value for that paint). When idle, it is painted right now below.
        ticking = self.decay_timer.isActive()

        for blip in self.blips:
            # angle_diff wraps at +-180 so a sound directly behind the player
            # (surround: -179 vs +179) refreshes one blip instead of two.
            if angle_diff(blip['angle'], angle) < BLIP_MERGE_DEG:
                # Same result as max(); written out so a refresh that does not
                # raise the life (and so changes nothing on screen) is not
                # marked fresh and does not delay the fade.
                if clamped_intensity > blip['life']:
                    blip['life'] = clamped_intensity
                    blip['fresh'] = ticking
                break
        else:
            self.blips.append({'angle': angle, 'life': clamped_intensity, 'fresh': ticking})

        if ticking:
            # No update() here: the tick is the only repaint source while sound
            # plays. Repainting per audio event as well used to add up to 20
            # extra repaints per second on top of the ~33 ticks.
            return

        # First sound after silence: paint it now and start ticking. Waiting
        # for the first tick instead measured 30-47 ms of extra delay (Windows
        # rounds timers to 15.6 ms), on exactly the alert that matters most.
        # This costs one paint per silence-to-sound transition, and the timer
        # only stops after at least one tick, so it can never outpace the tick.
        # (Start only an idle timer: restarting a running one on every event
        # would keep pushing its next timeout back.)
        self.update()
        self.decay_timer.start()

    def paintEvent(self, event):
        if self._perf is None:
            self._paint()
            return
        t0 = time.perf_counter()
        self._paint()
        self._perf.record_paint((time.perf_counter() - t0) * 1000.0)

    def _paint(self):
        # All pens, colours and rects come from _rebuild_paint_cache(); the only
        # per-frame work is setting each arc's alpha.
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        if self.drag_enabled:
            # Layered windows can be hard to hit-test on fully transparent pixels.
            # A nearly invisible fill makes the whole radar box draggable in setup mode.
            painter.fillRect(self._full_rect, self._drag_fill)

        center = self._center
        radius = self._radius

        painter.setPen(self._base_pen)
        painter.drawEllipse(center, radius, radius)

        if self.blips:
            arc_rect = self._arc_rect
            arc_color = self._arc_color
            arc_pen = self._arc_pen
            span_angle_16 = int(ARC_SPAN_DEG * 16)
            for blip in self.blips:
                # A future max-opacity setting scales this alpha.
                arc_color.setAlpha(int(blip['life'] * 255))
                arc_pen.setColor(arc_color)
                painter.setPen(arc_pen)   # QPainter copies the pen, so reuse is safe

                center_pyqt_angle = 90 - blip['angle']
                start_deg = center_pyqt_angle - (ARC_SPAN_DEG / 2)
                painter.drawArc(arc_rect, int(start_deg * 16), span_angle_16)

        if self.drag_enabled:
            painter.setPen(self._setup_pen)
            painter.drawEllipse(center, radius + 8, radius + 8)

        painter.end()
