"""
ValidationTimeline — segment timeline for the RIB validation app.

Two modes:
  readonly=True  (annotator timelines):
      • Segments are displayed but not editable.
      • Double-click on a segment body emits segment_double_clicked(seg_idx).
      • Single click on a segment body highlights it.
      • Click on empty space seeks.
  readonly=False (result timeline):
      • Boundary drag (left/right edges) emits boundary_dragged / boundary_committed.
      • Right-click on a segment removes it and emits segment_removed(seg_idx).
      • Single click on body activates it.
      • Click on empty space deactivates + seeks.

Both modes support:
  • Ctrl+click on overlapping segments raises the lowest one under the cursor
    to the top and selects it; repeated ctrl+clicks cycle through the stack.
    Clicks, double-clicks and right-clicks always act on the topmost segment.
  • Optional stacked layout (set_stacked): overlapping segments are drawn in
    separate sub-rows so none is hidden.  Display only — data is unchanged.
  • Middle-mouse pan.
  • Scroll-wheel zoom (both emit visible_range_changed).
  • Animated red playhead cursor.
"""

from __future__ import annotations

import numpy as np
import matplotlib.patches as mpatches
import matplotlib.transforms as mtransforms
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
from matplotlib.figure import Figure
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QApplication, QSizePolicy, QVBoxLayout, QWidget

from config import BOUNDARY_THRESHOLD_PX

# Vertical extent of the segment band (axes fraction)
_BAND_Y0 = 0.05
_BAND_H  = 0.90
_LANE_GAP = 0.03


class ValidationTimeline(QWidget):
    seek_requested         = Signal(int)
    visible_range_changed  = Signal(int, int)
    segment_double_clicked = Signal(int)           # seg_idx (readonly mode)
    boundary_dragged       = Signal(int, str, int) # seg_idx, side, frame (editable)
    boundary_committed     = Signal(int, str, int) # seg_idx, side, frame (editable)
    segment_removed        = Signal(int)           # seg_idx before removal (editable)

    def __init__(self, parent: QWidget | None = None,
                 readonly: bool = False) -> None:
        super().__init__(parent)
        self._readonly = readonly
        self.setMinimumHeight(40)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        fig = Figure(figsize=(10, 0.5))
        fig.patch.set_facecolor("#1e1e1e")
        self._canvas = FigureCanvasQTAgg(fig)
        self._ax = fig.add_subplot(111)
        self._ax.set_facecolor("#2a2a2a")
        self._ax.set_yticks([])
        self._ax.set_ylim(0, 1)
        for spine in self._ax.spines.values():
            spine.set_color("#444444")
        # All timelines use the same left/right fractions so the axes pixel
        # extents are identical across widgets of the same width — this keeps
        # tick marks vertically aligned.  Only the bottom (Result) timeline
        # shows labels; the annotator timelines show tick lines only.
        if self._readonly:
            self._ax.tick_params(colors="grey", labelbottom=False,
                                 length=3, width=0.5)
            fig.subplots_adjust(left=0.0, right=1.0, bottom=0.12, top=0.98)
        else:
            self._ax.tick_params(colors="grey", labelsize=7, length=4)
            fig.subplots_adjust(left=0.0, right=1.0, bottom=0.30, top=0.98)

        # Blended transform: x = data coords, y = axes fraction
        self._xform = mtransforms.blended_transform_factory(
            self._ax.transData, self._ax.transAxes
        )

        # Segment data: [{rect, text, start, end, seg_id, color, y0, h}, ...]
        self._seg_data: list[dict] = []
        self._active_idx: int = -1

        # Stacking: each segment gets a higher zorder than the previous one;
        # ctrl+click raises a segment above all others.
        self._z_top: float = 2.0

        # Lane layout for overlapping segments (display only)
        self._stacked: bool = False

        # Animated playhead
        self._cursor = self._ax.axvline(
            x=0, color="red", lw=1.2, zorder=5, animated=True
        )

        # Drag state (editable mode only)
        self._drag: dict | None = None

        # Pan state (middle-mouse)
        self._pan_start_px:   float | None = None
        self._pan_start_xlim: tuple | None = None

        # Data range
        self._data_first: int = 0
        self._data_last:  int = 1

        self._bg = None
        self._canvas.mpl_connect("draw_event",           self._on_draw)
        self._canvas.mpl_connect("resize_event",         self._on_resize)
        self._canvas.mpl_connect("button_press_event",   self._on_press)
        self._canvas.mpl_connect("motion_notify_event",  self._on_motion)
        self._canvas.mpl_connect("button_release_event", self._on_release)
        self._canvas.mpl_connect("scroll_event",         self._on_scroll)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._canvas)

    # ------------------------------------------------------------------
    # Blit cache
    # ------------------------------------------------------------------

    def _on_draw(self, _event) -> None:
        self._bg = self._canvas.copy_from_bbox(self._ax.bbox)

    def _on_resize(self, _event) -> None:
        self._bg = None

    # ------------------------------------------------------------------
    # Range / zoom
    # ------------------------------------------------------------------

    def set_range(self, first: int, last: int) -> None:
        for entry in self._seg_data:
            entry["rect"].remove()
            entry["text"].remove()
        self._seg_data.clear()
        self._active_idx = -1
        self._data_first = first
        self._data_last  = last
        self._ax.set_xlim(first, last)
        self._bg = None
        self._canvas.draw_idle()

    def set_xlim(self, first: int, last: int) -> None:
        if first >= last:
            return
        self._ax.set_xlim(first, last)
        self._bg = None
        self._canvas.draw_idle()

    # ------------------------------------------------------------------
    # Segment management
    # ------------------------------------------------------------------

    def add_segment(self, start: int, end: int, seg_id: int,
                    label: str = "", color: str = "#AAAAAA") -> None:
        self._z_top += 1
        rect = mpatches.Rectangle(
            (start, _BAND_Y0), end - start, _BAND_H,
            transform=self._xform,
            facecolor=color, alpha=0.75, edgecolor="none", lw=0,
            zorder=self._z_top, clip_on=True,
        )
        self._ax.add_patch(rect)
        rect.set_clip_path(self._ax.patch)

        text = self._ax.text(
            (start + end) / 2, 0.5, label,
            transform=self._xform,
            ha="center", va="center",
            fontsize=8, color="white", fontweight="bold",
            zorder=self._z_top + 0.5, clip_on=True,
        )
        text.set_clip_path(self._ax.patch)
        self._seg_data.append(
            {"rect": rect, "text": text, "start": start, "end": end,
             "seg_id": seg_id, "color": color,
             "y0": _BAND_Y0, "h": _BAND_H}
        )
        if self._stacked:
            self._relayout_lanes()
        self._bg = None
        self._canvas.draw_idle()

    def remove_segment(self, seg_idx: int) -> None:
        """Remove segment visually (does not emit signal — caller does that)."""
        if not (0 <= seg_idx < len(self._seg_data)):
            return
        entry = self._seg_data.pop(seg_idx)
        entry["rect"].remove()
        entry["text"].remove()
        if self._active_idx >= len(self._seg_data):
            self._active_idx = -1
        elif self._active_idx > seg_idx:
            self._active_idx -= 1
        if self._stacked:
            self._relayout_lanes()
        self._bg = None
        self._canvas.draw_idle()

    def clear_segments(self) -> None:
        for entry in self._seg_data:
            entry["rect"].remove()
            entry["text"].remove()
        self._seg_data.clear()
        self._active_idx = -1
        self._bg = None
        self._canvas.draw_idle()

    def update_segment(self, seg_idx: int, label: str, color: str) -> None:
        if not (0 <= seg_idx < len(self._seg_data)):
            return
        entry = self._seg_data[seg_idx]
        entry["color"] = color
        entry["rect"].set_facecolor(color)
        entry["text"].set_text(label)
        self._bg = None
        self._canvas.draw_idle()

    def set_active_segment(self, seg_idx: int) -> None:
        if 0 <= self._active_idx < len(self._seg_data):
            self._seg_data[self._active_idx]["rect"].set_edgecolor("none")
            self._seg_data[self._active_idx]["rect"].set_linewidth(0)
        self._active_idx = seg_idx
        if 0 <= seg_idx < len(self._seg_data):
            self._seg_data[seg_idx]["rect"].set_edgecolor("white")
            self._seg_data[seg_idx]["rect"].set_linewidth(2.0)
        self._bg = None
        self._canvas.draw_idle()

    def raise_segment(self, seg_idx: int) -> None:
        """Draw segment *seg_idx* above all others (display only)."""
        if not (0 <= seg_idx < len(self._seg_data)):
            return
        self._z_top += 1
        entry = self._seg_data[seg_idx]
        entry["rect"].set_zorder(self._z_top)
        entry["text"].set_zorder(self._z_top + 0.5)
        self._bg = None
        self._canvas.draw_idle()

    # ------------------------------------------------------------------
    # Lane layout (stacked sub-rows for overlapping segments)
    # ------------------------------------------------------------------

    def set_stacked(self, stacked: bool) -> None:
        self._stacked = stacked
        self._relayout_lanes()
        self._bg = None
        self._canvas.draw_idle()

    def _relayout_lanes(self) -> None:
        """Assign each segment a lane so overlapping segments never share one.

        Segments are grouped into overlap clusters; within each cluster,
        greedy interval partitioning puts each segment (sorted by start) into
        the first lane whose last segment ends at or before its start.  Each
        cluster is split only into as many rows as it needs, so isolated
        segments stay full height.
        """
        n = len(self._seg_data)
        lanes   = [0] * n   # lane index per segment
        n_lanes = [1] * n   # lane count of the segment's cluster
        if self._stacked:
            order = sorted(range(n), key=lambda j: (self._seg_data[j]["start"],
                                                    self._seg_data[j]["end"]))
            cluster: list[int] = []
            lane_ends: list[int] = []
            cluster_end = None

            def close_cluster() -> None:
                for j in cluster:
                    n_lanes[j] = len(lane_ends)

            for i in order:
                entry = self._seg_data[i]
                if cluster_end is not None and entry["start"] >= cluster_end:
                    close_cluster()
                    cluster, lane_ends, cluster_end = [], [], None
                for lane, last_end in enumerate(lane_ends):
                    if last_end <= entry["start"]:
                        lanes[i] = lane
                        lane_ends[lane] = entry["end"]
                        break
                else:
                    lanes[i] = len(lane_ends)
                    lane_ends.append(entry["end"])
                cluster.append(i)
                cluster_end = (entry["end"] if cluster_end is None
                               else max(cluster_end, entry["end"]))
            close_cluster()

        for entry, lane, k in zip(self._seg_data, lanes, n_lanes):
            lane_h = _BAND_H / k
            h      = lane_h - _LANE_GAP if k > 1 else _BAND_H
            y0     = _BAND_Y0 + lane * lane_h
            entry["y0"], entry["h"] = y0, h
            entry["rect"].set_y(y0)
            entry["rect"].set_height(h)
            entry["text"].set_y(y0 + h / 2)
            entry["text"].set_fontsize(8 if k == 1 else max(5, 8 - k))

    # ------------------------------------------------------------------
    # Playhead
    # ------------------------------------------------------------------

    def update_cursor(self, frame: int) -> None:
        self._cursor.set_xdata([frame, frame])
        if self._bg is not None:
            self._canvas.restore_region(self._bg)
            self._ax.draw_artist(self._cursor)
            self._canvas.blit(self._ax.bbox)
        else:
            self._canvas.draw_idle()

    # ------------------------------------------------------------------
    # Interaction helpers
    # ------------------------------------------------------------------

    def _boundary_threshold(self) -> float:
        xlim    = self._ax.get_xlim()
        ax_w_px = self._ax.get_window_extent().width
        if ax_w_px <= 0:
            return (xlim[1] - xlim[0]) * 0.01
        return BOUNDARY_THRESHOLD_PX * (xlim[1] - xlim[0]) / ax_w_px

    def _hit_boundary(self, x: float):
        """Return (seg_idx, side) if near a boundary (editable only)."""
        if self._readonly:
            return None
        thresh = self._boundary_threshold()
        for i, entry in enumerate(self._seg_data):
            if abs(x - entry["start"]) <= thresh:
                return i, "start"
            if abs(x - entry["end"]) <= thresh:
                return i, "end"
        return None

    def _hits_at(self, x: float, y: float | None) -> list[int]:
        """Indices of segments under (x, y), topmost (highest zorder) first."""
        hits = [
            i for i, entry in enumerate(self._seg_data)
            if entry["start"] < x < entry["end"]
            and (y is None or entry["y0"] <= y <= entry["y0"] + entry["h"])
        ]
        hits.sort(key=lambda i: self._seg_data[i]["rect"].get_zorder(),
                  reverse=True)
        return hits

    def _hit_segment_body(self, x: float, y: float | None = None) -> int:
        hits = self._hits_at(x, y)
        return hits[0] if hits else -1

    # ------------------------------------------------------------------
    # Mouse events
    # ------------------------------------------------------------------

    def _on_press(self, event) -> None:
        if event.inaxes is not self._ax:
            return

        # Middle-mouse → start pan
        if event.button == 2:
            self._pan_start_px   = event.x
            self._pan_start_xlim = self._ax.get_xlim()
            return

        x = event.xdata
        if x is None:
            return
        y = event.ydata  # ylim is (0, 1), so this equals the axes fraction

        # Right-click in editable mode → remove segment
        if event.button == 3 and not self._readonly:
            body_idx = self._hit_segment_body(x, y)
            if body_idx >= 0:
                self.remove_segment(body_idx)
                self.segment_removed.emit(body_idx)
            return

        if event.button != 1:
            return

        # Ctrl+click → raise the lowest segment under the cursor (cycles stack)
        ctrl = bool(QApplication.keyboardModifiers()
                    & Qt.KeyboardModifier.ControlModifier)
        if ctrl and not event.dblclick:
            hits = self._hits_at(x, y)
            if hits:
                self.raise_segment(hits[-1])
                self.set_active_segment(hits[-1])
                return

        # Boundary drag (editable mode)
        if not self._readonly:
            hit = self._hit_boundary(x)
            if hit is not None:
                self._drag = {"seg_idx": hit[0], "side": hit[1]}
                return

        # Segment body
        body_idx = self._hit_segment_body(x, y)
        if body_idx >= 0:
            self.set_active_segment(body_idx)
            # Double-click in readonly mode → copy to result
            if event.dblclick and self._readonly:
                self.segment_double_clicked.emit(body_idx)
            return

        # Empty space → deactivate + seek
        self.set_active_segment(-1)
        self.seek_requested.emit(int(round(x)))

    def _on_motion(self, event) -> None:
        # Pan (middle-mouse)
        if self._pan_start_px is not None:
            xlim = self._pan_start_xlim
            span = xlim[1] - xlim[0]
            w_px = self._ax.get_window_extent().width
            if w_px > 0:
                dx    = -(event.x - self._pan_start_px) * span / w_px
                left  = max(float(self._data_first), xlim[0] + dx)
                left  = min(left, float(self._data_last) - span)
                right = left + span
                self._ax.set_xlim(left, right)
                self._bg = None
                self._canvas.draw_idle()
                self.visible_range_changed.emit(int(left), int(right))
            return

        # Boundary drag (editable)
        if self._drag is None or event.inaxes is not self._ax or event.xdata is None:
            return

        seg_idx = self._drag["seg_idx"]
        side    = self._drag["side"]
        frame   = int(round(event.xdata))
        frame   = max(self._data_first, min(self._data_last, frame))

        entry = self._seg_data[seg_idx]
        if side == "start":
            frame = min(frame, entry["end"] - 2)
        else:
            frame = max(frame, entry["start"] + 2)

        entry["start" if side == "start" else "end"] = frame
        self._update_rect(seg_idx)
        self._bg = None
        self._canvas.draw_idle()
        self.boundary_dragged.emit(seg_idx, side, frame)

    def _on_release(self, event) -> None:
        if event.button == 2:
            self._pan_start_px   = None
            self._pan_start_xlim = None
            return
        if self._drag is None or event.button != 1:
            return
        seg_idx = self._drag["seg_idx"]
        side    = self._drag["side"]
        frame   = self._seg_data[seg_idx][side]
        self._drag = None
        self.boundary_committed.emit(seg_idx, side, frame)

    def _update_rect(self, seg_idx: int) -> None:
        entry = self._seg_data[seg_idx]
        start, end = entry["start"], entry["end"]
        entry["rect"].set_x(start)
        entry["rect"].set_width(end - start)
        entry["text"].set_x((start + end) / 2)

    # ------------------------------------------------------------------
    # Scroll-wheel zoom
    # ------------------------------------------------------------------

    def _on_scroll(self, event) -> None:
        if event.inaxes is not self._ax:
            return
        factor      = 1.15 if event.step > 0 else 1 / 1.15
        left, right = self._ax.get_xlim()
        anchor      = event.xdata if event.xdata is not None else (left + right) / 2
        half_span   = (right - left) / 2
        target_span = 2.0 * half_span / factor
        new_left    = anchor - half_span / factor
        new_right   = anchor + half_span / factor
        # Slide window to stay within data bounds while preserving the target span.
        if new_right > self._data_last:
            new_right = float(self._data_last)
            new_left  = new_right - target_span
        if new_left < self._data_first:
            new_left  = float(self._data_first)
            new_right = min(float(self._data_last), new_left + target_span)
        if new_right <= new_left:
            return
        self._ax.set_xlim(new_left, new_right)
        self._bg = None
        self._canvas.draw_idle()
        self.visible_range_changed.emit(int(new_left), int(new_right))
