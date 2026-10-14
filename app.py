"""
RIB Validation App — compare and validate runs-in-behind annotations.

Usage:
    cd rib-validation-app
    python app.py

Workflow:
  1. Load the tracking position XML files and (optionally) a video.
  2. Load two (or, via "Add annotation source", up to six) annotation CSV
     files produced by the rib-annotation-app.
  3. Each annotation set appears in its own timeline above the Result.
     Overlapping segments are stacked in sub-rows; ctrl+click cycles through
     segments drawn on top of each other.
  4. Double-click a segment in any annotator timeline to copy it to
     the Result timeline.
  5. Drag segment boundaries in the Result timeline to adjust.
  6. Right-click a segment in the Result timeline to remove it.
  7. Save the validated result.
"""

from __future__ import annotations

import os
# PySide6's bundled FFmpeg is compiled against a newer libva than most distros
# ship; disable VA-API to avoid the "undefined symbol: vaMapBuffer2" crash.
os.environ.setdefault("LIBVA_DRIVER_NAME", "null")

import sys

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QColorDialog,
    QComboBox,
    QFileDialog,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QScrollBar,
    QSizePolicy,
    QSplitter,
    QStatusBar,
    QVBoxLayout,
    QWidget,
)

from config import (
    AWAY_COLOR,
    DEFAULT_ANNOTATION_SOURCES,
    FPS,
    HOME_COLOR,
    MAX_ANNOTATION_SOURCES,
)
from data_manager import DataManager
from pitch_panel import PitchPanel
from validation_timeline import ValidationTimeline
from video_panel import VideoPanel


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("RIB Validation App")
        self.resize(1440, 960)

        self._dm = DataManager()

        # Annotation sources: one dict per channel with keys
        # picker, label, edit, remove_btn, title, timeline, segs
        self._sources: list[dict] = []

        # True once the first full Load has happened (enables per-source reload)
        self._loaded: bool = False

        # Team colours
        self._home_color = HOME_COLOR
        self._away_color = AWAY_COLOR

        # Guard against re-entrant range-sync / scrollbar feedback
        self._syncing_range: bool = False

        # Total loaded data range (frames) — used to drive the scrollbar
        self._data_first: int = 0
        self._data_last:  int = 1

        self._build_ui()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        central = QWidget(self)
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(6, 6, 6, 6)
        root.setSpacing(4)

        # ── Row 1: position / matchinfo / video pickers ───────────────
        file_row = QHBoxLayout()
        file_row.setSpacing(6)

        self._pos_edit = QLineEdit(self)
        self._pos_edit.setPlaceholderText("Positions XML…")
        self._pos_edit.setMinimumWidth(180)
        btn_pos = QPushButton("…", self)
        btn_pos.setFixedWidth(28)
        btn_pos.clicked.connect(self._browse_positions)
        file_row.addWidget(QLabel("Positions:", self))
        file_row.addWidget(self._pos_edit)
        file_row.addWidget(btn_pos)

        file_row.addSpacing(8)

        self._mat_edit = QLineEdit(self)
        self._mat_edit.setPlaceholderText("Matchinfo XML…")
        self._mat_edit.setMinimumWidth(180)
        btn_mat = QPushButton("…", self)
        btn_mat.setFixedWidth(28)
        btn_mat.clicked.connect(self._browse_matinfo)
        file_row.addWidget(QLabel("Matchinfo:", self))
        file_row.addWidget(self._mat_edit)
        file_row.addWidget(btn_mat)

        file_row.addSpacing(8)

        self._vid_edit = QLineEdit(self)
        self._vid_edit.setPlaceholderText("Video file…")
        self._vid_edit.setMinimumWidth(140)
        btn_vid = QPushButton("…", self)
        btn_vid.setFixedWidth(28)
        btn_vid.clicked.connect(self._browse_video)
        file_row.addWidget(QLabel("Video:", self))
        file_row.addWidget(self._vid_edit)
        file_row.addWidget(btn_vid)

        root.addLayout(file_row)

        # ── Row 2: annotation CSV pickers (grid, 3 per row) ───────────
        src_row = QHBoxLayout()
        src_row.setSpacing(6)

        self._picker_grid = QGridLayout()
        self._picker_grid.setHorizontalSpacing(8)
        self._picker_grid.setVerticalSpacing(2)
        src_row.addLayout(self._picker_grid, stretch=1)

        self._btn_add_source = QPushButton("Add annotation source", self)
        self._btn_add_source.setToolTip(
            f"Add another annotation CSV channel (max. {MAX_ANNOTATION_SOURCES})."
        )
        self._btn_add_source.clicked.connect(self._add_source)
        src_row.addWidget(self._btn_add_source, alignment=Qt.AlignmentFlag.AlignTop)

        root.addLayout(src_row)

        # ── Row 3: load / display / save controls ─────────────────────
        ann_row = QHBoxLayout()
        ann_row.setSpacing(6)

        ann_row.addWidget(QLabel("Half:", self))
        self._half_combo = QComboBox(self)
        self._half_combo.addItems(["firstHalf", "secondHalf"])
        self._half_combo.setFixedWidth(110)
        ann_row.addWidget(self._half_combo)

        btn_load = QPushButton("Load", self)
        btn_load.setFixedWidth(80)
        btn_load.clicked.connect(self._on_load)
        ann_row.addWidget(btn_load)

        ann_row.addSpacing(16)

        self._chk_stacked = QCheckBox("Stack overlaps", self)
        self._chk_stacked.setChecked(True)
        self._chk_stacked.setToolTip(
            "Draw overlapping annotator segments in separate sub-rows.\n"
            "Ctrl+click cycles through segments drawn on top of each other."
        )
        self._chk_stacked.toggled.connect(self._on_stacked_toggled)
        ann_row.addWidget(self._chk_stacked)

        ann_row.addSpacing(16)

        self._btn_home_color = QPushButton("Home colour", self)
        self._btn_home_color.setFixedWidth(100)
        self._btn_home_color.clicked.connect(self._pick_home_color)
        self._apply_btn_color(self._btn_home_color, self._home_color)
        ann_row.addWidget(self._btn_home_color)

        self._btn_away_color = QPushButton("Away colour", self)
        self._btn_away_color.setFixedWidth(100)
        self._btn_away_color.clicked.connect(self._pick_away_color)
        self._apply_btn_color(self._btn_away_color, self._away_color)
        ann_row.addWidget(self._btn_away_color)

        ann_row.addStretch()

        self._btn_remove = QPushButton("Remove", self)
        self._btn_remove.setFixedWidth(80)
        self._btn_remove.setToolTip(
            "Remove the active (highlighted) segment from the Result timeline.\n"
            "You can also right-click any segment in the Result timeline."
        )
        self._btn_remove.clicked.connect(self._on_remove_active)
        ann_row.addWidget(self._btn_remove)

        btn_save = QPushButton("Save", self)
        btn_save.setFixedWidth(80)
        btn_save.clicked.connect(self._on_save)
        ann_row.addWidget(btn_save)

        root.addLayout(ann_row)

        # ── Main splitter: top (video+pitch) / bottom (timelines) ─────
        v_splitter = QSplitter(Qt.Orientation.Vertical, self)
        v_splitter.setStretchFactor(0, 6)
        v_splitter.setStretchFactor(1, 4)

        # Top: video (left) + pitch (right)
        self._video_panel = VideoPanel(self)
        self._pitch_panel = PitchPanel(self)

        h_splitter = QSplitter(Qt.Orientation.Horizontal, self)
        h_splitter.addWidget(self._video_panel)
        h_splitter.addWidget(self._pitch_panel)
        h_splitter.setStretchFactor(0, 3)
        h_splitter.setStretchFactor(1, 2)
        v_splitter.addWidget(h_splitter)

        # Bottom: labelled annotator timelines + Result timeline
        bottom        = QWidget(self)
        bottom_layout = QVBoxLayout(bottom)
        bottom_layout.setContentsMargins(0, 2, 0, 2)
        bottom_layout.setSpacing(2)

        # Annotator timelines (read-only) — populated by _add_source()
        self._ann_layout = QVBoxLayout()
        self._ann_layout.setContentsMargins(0, 0, 0, 0)
        self._ann_layout.setSpacing(2)
        bottom_layout.addLayout(self._ann_layout, stretch=DEFAULT_ANNOTATION_SOURCES)
        self._bottom_layout = bottom_layout

        # Result timeline (editable)
        bottom_layout.addWidget(self._make_timeline_label(
            "Result  —  drag boundaries to adjust  |  right-click to remove"
        ))
        self._result_timeline = ValidationTimeline(self, readonly=False)
        bottom_layout.addWidget(self._result_timeline, stretch=1)

        # Shared scrollbar for all three timelines
        self._timeline_scrollbar = QScrollBar(Qt.Orientation.Horizontal, self)
        self._timeline_scrollbar.setRange(0, 1)
        self._timeline_scrollbar.setPageStep(1)
        self._timeline_scrollbar.setSingleStep(1)
        self._timeline_scrollbar.setFixedHeight(16)
        bottom_layout.addWidget(self._timeline_scrollbar)

        self._status_label = QLabel("No data loaded", self)
        self._status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        bottom_layout.addWidget(self._status_label)

        v_splitter.addWidget(bottom)
        v_splitter.setSizes([580, 380])

        root.addWidget(v_splitter, stretch=1)

        # ── Signal wiring ─────────────────────────────────────────────
        self._video_panel.frame_changed.connect(self._on_frame_changed)

        self._result_timeline.seek_requested.connect(self._video_panel.seek_to_frame)
        self._result_timeline.visible_range_changed.connect(self._on_range_changed)

        self._timeline_scrollbar.valueChanged.connect(self._on_scrollbar_changed)

        self._result_timeline.boundary_dragged.connect(self._on_result_boundary_dragged)
        self._result_timeline.boundary_committed.connect(self._on_result_boundary_committed)
        self._result_timeline.segment_removed.connect(self._on_result_segment_removed)

        self.setStatusBar(QStatusBar(self))

        for _ in range(DEFAULT_ANNOTATION_SOURCES):
            self._add_source()

    # ------------------------------------------------------------------
    # Annotation source channels
    # ------------------------------------------------------------------

    def _add_source(self) -> None:
        if len(self._sources) >= MAX_ANNOTATION_SOURCES:
            return

        # File picker: label + path + browse + (for extra sources) remove
        picker = QWidget(self)
        p_lay  = QHBoxLayout(picker)
        p_lay.setContentsMargins(0, 0, 0, 0)
        p_lay.setSpacing(4)
        label = QLabel(self)
        edit  = QLineEdit(self)
        edit.setMinimumWidth(160)
        browse_btn = QPushButton("…", self)
        browse_btn.setFixedWidth(28)
        remove_btn = QPushButton("✕", self)
        remove_btn.setFixedWidth(24)
        remove_btn.setToolTip("Remove this annotation source")
        remove_btn.setVisible(len(self._sources) >= DEFAULT_ANNOTATION_SOURCES)
        for w in (label, edit, browse_btn, remove_btn):
            p_lay.addWidget(w)

        # Timeline channel
        title    = self._make_timeline_label("")
        timeline = ValidationTimeline(self, readonly=True)
        timeline.set_stacked(self._chk_stacked.isChecked())
        self._ann_layout.addWidget(title)
        self._ann_layout.addWidget(timeline, stretch=1)

        entry = {
            "picker": picker, "label": label, "edit": edit,
            "remove_btn": remove_btn, "title": title,
            "timeline": timeline, "segs": [],
        }
        self._sources.append(entry)

        browse_btn.clicked.connect(lambda: self._browse_source(entry))
        edit.returnPressed.connect(
            lambda: self._load_source(entry, edit.text().strip())
        )
        remove_btn.clicked.connect(lambda: self._remove_source(entry))
        timeline.seek_requested.connect(self._video_panel.seek_to_frame)
        timeline.visible_range_changed.connect(self._on_range_changed)
        timeline.segment_double_clicked.connect(
            lambda idx: self._copy_to_result(entry, idx)
        )

        # Match the current data range / view if data is already loaded
        if self._loaded:
            timeline.set_range(self._data_first, self._data_last)
            left, right = self._result_timeline._ax.get_xlim()
            timeline.set_xlim(int(left), int(right))

        self._relayout_sources()

    def _remove_source(self, entry: dict) -> None:
        if entry not in self._sources:
            return
        self._sources.remove(entry)
        for key in ("picker", "title", "timeline"):
            self._picker_grid.removeWidget(entry[key])
            self._ann_layout.removeWidget(entry[key])
            entry[key].deleteLater()
        self._relayout_sources()
        self._update_status_label()

    def _relayout_sources(self) -> None:
        """Renumber sources and re-place pickers in the grid (3 per row)."""
        for entry in self._sources:
            self._picker_grid.removeWidget(entry["picker"])
        for i, entry in enumerate(self._sources):
            n = i + 1
            entry["label"].setText(f"Ann. {n}:")
            entry["edit"].setPlaceholderText(f"Annotator {n} CSV…")
            entry["title"].setText(
                f"Annotator {n}  —  double-click a segment to add it to the Result"
                "  |  ctrl+click to cycle overlapping segments"
            )
            self._picker_grid.addWidget(entry["picker"], i // 3, i % 3)
        self._bottom_layout.setStretchFactor(
            self._ann_layout, max(1, len(self._sources))
        )
        self._btn_add_source.setEnabled(len(self._sources) < MAX_ANNOTATION_SOURCES)

    def _source_number(self, entry: dict) -> int:
        return self._sources.index(entry) + 1

    def _browse_source(self, entry: dict) -> None:
        n = self._source_number(entry)
        path, _ = QFileDialog.getOpenFileName(
            self, f"Select Annotator {n} CSV", "", "CSV files (*.csv);;All files (*)"
        )
        if path:
            entry["edit"].setText(path)
            self._load_source(entry, path)

    def _load_source(self, entry: dict, path: str) -> None:
        """Reload a single channel after the initial Load (Result is kept)."""
        if not self._loaded or entry not in self._sources:
            return
        n = self._source_number(entry)
        segs: list[dict] = []
        if path:
            try:
                segs = DataManager.load_annotations(path)
            except Exception as exc:
                QMessageBox.critical(self, f"Annotator {n} CSV load failed", str(exc))
                return
        entry["segs"] = segs
        self._populate_source_timeline(entry)
        self.statusBar().showMessage(
            f"Annotator {n}: loaded {len(segs)} segment{'s' if len(segs) != 1 else ''}"
            "  (Result unchanged)", 5000
        )
        self._update_status_label()

    def _populate_source_timeline(self, entry: dict) -> None:
        tl = entry["timeline"]
        tl.clear_segments()
        for seg in entry["segs"]:
            tl.add_segment(
                seg["start_frame"], seg["end_frame"],
                seg["segment_id"], seg["player_jid"],
                self._team_color(seg["team"]),
            )

    def _on_stacked_toggled(self, checked: bool) -> None:
        for entry in self._sources:
            entry["timeline"].set_stacked(checked)

    def _all_timelines(self) -> list[ValidationTimeline]:
        return [e["timeline"] for e in self._sources] + [self._result_timeline]

    @staticmethod
    def _make_timeline_label(text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setStyleSheet(
            "color: #aaaaaa; font-size: 10px; padding: 1px 2px;"
        )
        return lbl

    # ------------------------------------------------------------------
    # Colour utilities
    # ------------------------------------------------------------------

    @staticmethod
    def _apply_btn_color(btn: QPushButton, hex_color: str) -> None:
        qc   = QColor(hex_color)
        text = "black" if qc.lightness() > 128 else "white"
        btn.setStyleSheet(
            f"background-color: {hex_color}; color: {text}; font-size: 12px;"
        )

    def _pick_home_color(self) -> None:
        qc = QColorDialog.getColor(QColor(self._home_color), self, "Home team colour")
        if qc.isValid():
            self._home_color = qc.name()
            self._apply_btn_color(self._btn_home_color, self._home_color)
            self._pitch_panel.set_team_colors(self._home_color, self._away_color)
            self._repaint_annotator_segments()

    def _pick_away_color(self) -> None:
        qc = QColorDialog.getColor(QColor(self._away_color), self, "Away team colour")
        if qc.isValid():
            self._away_color = qc.name()
            self._apply_btn_color(self._btn_away_color, self._away_color)
            self._pitch_panel.set_team_colors(self._home_color, self._away_color)
            self._repaint_annotator_segments()

    def _repaint_annotator_segments(self) -> None:
        """Repaint annotator timeline colours after a team colour change."""
        for entry in self._sources:
            for i, seg in enumerate(entry["segs"]):
                color = self._team_color(seg["team"])
                entry["timeline"].update_segment(i, seg["player_jid"], color)
        # Result segments also carry team info from the source annotator
        for i, seg in enumerate(self._dm.segments):
            if seg.team:
                color = self._team_color(seg.team)
                self._result_timeline.update_segment(i, seg.player_jid, color)

    def _team_color(self, team: str) -> str:
        if team == "Home":
            return self._home_color
        if team == "Away":
            return self._away_color
        return "#AAAAAA"

    # ------------------------------------------------------------------
    # File pickers
    # ------------------------------------------------------------------

    def _browse_positions(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Select Positions XML", "", "XML files (*.xml);;All files (*)"
        )
        if path:
            self._pos_edit.setText(path)

    def _browse_matinfo(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Select Matchinfo XML", "", "XML files (*.xml);;All files (*)"
        )
        if path:
            self._mat_edit.setText(path)

    def _browse_video(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Select Video", "",
            "Video files (*.mp4 *.mkv *.avi *.mov);;All files (*)"
        )
        if path:
            self._vid_edit.setText(path)

    # ------------------------------------------------------------------
    # Load
    # ------------------------------------------------------------------

    def _on_load(self) -> None:
        pos_path  = self._pos_edit.text().strip()
        mat_path  = self._mat_edit.text().strip()
        vid_path  = self._vid_edit.text().strip()
        ann_paths = [e["edit"].text().strip() for e in self._sources]
        half      = self._half_combo.currentText()

        if not any(ann_paths):
            self.statusBar().showMessage(
                "Select at least one annotation CSV.", 4000
            )
            return

        # ── Load position data (optional) ─────────────────────────────
        if pos_path and mat_path:
            self.statusBar().showMessage("Loading position data — please wait…")
            QApplication.processEvents()
            try:
                self._dm.load(pos_path, mat_path, half)
            except Exception as exc:
                QMessageBox.critical(self, "Position data load failed", str(exc))
                self.statusBar().showMessage("Position load failed.", 4000)
                return

            # Sync half combo to what was actually loaded
            current = self._half_combo.currentText()
            self._half_combo.blockSignals(True)
            self._half_combo.clear()
            self._half_combo.addItems(self._dm.available_halves)
            if current in self._dm.available_halves:
                self._half_combo.setCurrentText(current)
            self._half_combo.blockSignals(False)

            home_jids = self._dm.get_jersey_ids("Home")
            away_jids = self._dm.get_jersey_ids("Away")
            self._pitch_panel.initialize(
                home_jids, away_jids, self._home_color, self._away_color
            )
        else:
            # Reset result segments only (keep position data if already loaded)
            self._dm.segments.clear()
            self._dm._next_id = 0

        # ── Load annotation CSVs ──────────────────────────────────────
        for n, (entry, path) in enumerate(zip(self._sources, ann_paths), start=1):
            entry["segs"] = []
            if path:
                try:
                    entry["segs"] = DataManager.load_annotations(path)
                except Exception as exc:
                    QMessageBox.critical(self, f"Annotator {n} CSV load failed", str(exc))

        # ── Determine frame range ──────────────────────────────────────
        if self._dm.xy_home is not None:
            first = self._dm.first_frame
            last  = self._dm.last_frame
        else:
            all_segs   = [s for e in self._sources for s in e["segs"]]
            all_frames = (
                [s["start_frame"] for s in all_segs] +
                [s["end_frame"]   for s in all_segs]
            )
            first = min(all_frames) if all_frames else 0
            last  = max(all_frames) if all_frames else 100

        # ── Reset all timelines ────────────────────────────────────────
        self._data_first = first
        self._data_last  = last
        for tl in self._all_timelines():
            tl.set_range(first, last)
        self._update_scrollbar_state(first, last)
        self._loaded = True

        # ── Populate annotator timelines ───────────────────────────────
        for entry in self._sources:
            self._populate_source_timeline(entry)

        # ── Load video ─────────────────────────────────────────────────
        if vid_path and os.path.exists(vid_path):
            self._video_panel.load_video(vid_path, offset_frames=0)
        elif vid_path:
            self.statusBar().showMessage(
                f"Video not found: {vid_path} — continuing without video.", 6000
            )

        counts = "   ".join(
            f"Ann. {n}: {len(e['segs'])} segment{'s' if len(e['segs']) != 1 else ''}"
            for n, e in enumerate(self._sources, start=1)
        )
        msg = f"Loaded {half}   |   {counts}   Range: frames {first}–{last}"
        self.statusBar().showMessage(msg, 8000)
        self._update_status_label()

    # ------------------------------------------------------------------
    # Frame update
    # ------------------------------------------------------------------

    def _on_frame_changed(self, frame: int) -> None:
        if self._dm.xy_home is not None:
            frame_idx = max(self._dm.first_frame, min(self._dm.last_frame, frame))
            home_x, home_y, away_x, away_y, ball_x, ball_y = \
                self._dm.get_trail_positions(frame_idx)
            if home_x is not None:
                self._pitch_panel.update_frame(
                    home_x, home_y, away_x, away_y, ball_x, ball_y
                )
        else:
            frame_idx = frame

        for tl in self._all_timelines():
            tl.update_cursor(frame_idx)

    # ------------------------------------------------------------------
    # Timeline range synchronisation
    # ------------------------------------------------------------------

    def _on_range_changed(self, first: int, last: int) -> None:
        if self._syncing_range:
            return
        self._syncing_range = True
        for tl in self._all_timelines():
            tl.set_xlim(first, last)
        self._syncing_range = False
        self._update_scrollbar_state(first, last)

    def _update_scrollbar_state(self, view_left: int, view_right: int) -> None:
        """Sync the shared scrollbar to the current visible range (no feedback)."""
        sb        = self._timeline_scrollbar
        view_span = max(1, view_right - view_left)
        max_val   = max(self._data_first, self._data_last - view_span)
        sb.blockSignals(True)
        sb.setPageStep(view_span)
        sb.setSingleStep(max(1, view_span // 10))
        sb.setRange(self._data_first, max_val)
        sb.setValue(view_left)
        sb.blockSignals(False)

    def _on_scrollbar_changed(self, value: int) -> None:
        """User dragged the shared scrollbar — pan all timelines together."""
        if self._syncing_range:
            return
        view_span = self._timeline_scrollbar.pageStep()
        new_left  = value
        new_right = new_left + view_span
        self._syncing_range = True
        for tl in self._all_timelines():
            tl.set_xlim(new_left, new_right)
        self._syncing_range = False

    # ------------------------------------------------------------------
    # Double-click: copy annotator segment → result
    # ------------------------------------------------------------------

    def _copy_to_result(self, entry: dict, seg_idx: int) -> None:
        if entry not in self._sources:
            return
        segs      = entry["segs"]
        annotator = self._source_number(entry)
        if not (0 <= seg_idx < len(segs)):
            return
        src = segs[seg_idx]
        seg = self._dm.add_segment(src["start_frame"], src["end_frame"])
        result_idx = len(self._dm.segments) - 1
        self._dm.assign_player(result_idx, src["player"], src["player_jid"], src["team"])

        color = self._team_color(src["team"])
        self._result_timeline.add_segment(
            seg.start_frame, seg.end_frame,
            seg.segment_id, src["player_jid"], color,
        )

        dur = (seg.end_frame - seg.start_frame) / FPS
        self.statusBar().showMessage(
            f"Added to Result: frames {seg.start_frame}–{seg.end_frame} "
            f"({dur:.2f} s)  [{src['player_jid']}]  from Annotator {annotator}",
            4000,
        )
        self._update_status_label()

    # ------------------------------------------------------------------
    # Result timeline interactions
    # ------------------------------------------------------------------

    def _on_result_boundary_dragged(self, seg_idx: int, side: str, frame: int) -> None:
        self._dm.update_boundary(seg_idx, side, frame)
        self._video_panel.seek_to_frame(frame)

    def _on_result_boundary_committed(self, seg_idx: int, side: str, frame: int) -> None:
        self._dm.update_boundary(seg_idx, side, frame)
        self._update_status_label()

    def _on_result_segment_removed(self, seg_idx: int) -> None:
        removed = self._dm.remove_segment(seg_idx)
        if removed is not None:
            self.statusBar().showMessage(
                f"Removed segment #{removed.segment_id} from Result.", 3000
            )
        self._update_status_label()

    def _on_remove_active(self) -> None:
        """Remove the currently highlighted segment in the Result timeline."""
        idx = self._result_timeline._active_idx
        if idx < 0:
            self.statusBar().showMessage(
                "Click a segment in the Result timeline to select it first.", 3000
            )
            return
        self._result_timeline.remove_segment(idx)
        self._on_result_segment_removed(idx)

    # ------------------------------------------------------------------
    # Save
    # ------------------------------------------------------------------

    def _on_save(self) -> None:
        if not self._dm.segments:
            self.statusBar().showMessage("Result timeline is empty — nothing to save.", 3000)
            return
        try:
            out_path = self._dm.save(annotation_source="validated")
            self.statusBar().showMessage(
                f"Saved {len(self._dm.segments)} validated segments → {out_path}", 6000
            )
        except Exception as exc:
            QMessageBox.critical(self, "Save failed", str(exc))

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _update_status_label(self) -> None:
        parts = [
            f"Ann. {n}: {len(e['segs'])} seg."
            for n, e in enumerate(self._sources, start=1)
        ]
        parts.append(f"Result: {len(self._dm.segments)} seg.")
        self._status_label.setText("   |   ".join(parts))


# -----------------------------------------------------------------------
# Entry point
# -----------------------------------------------------------------------

def main() -> None:
    app_dir = os.path.dirname(os.path.abspath(__file__))
    if app_dir not in sys.path:
        sys.path.insert(0, app_dir)

    app = QApplication(sys.argv)
    app.setStyle("Fusion")

    from PySide6.QtGui import QPalette
    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Window,          QColor(40,  40,  40))
    palette.setColor(QPalette.ColorRole.WindowText,      QColor(220, 220, 220))
    palette.setColor(QPalette.ColorRole.Base,            QColor(30,  30,  30))
    palette.setColor(QPalette.ColorRole.AlternateBase,   QColor(50,  50,  50))
    palette.setColor(QPalette.ColorRole.Text,            QColor(220, 220, 220))
    palette.setColor(QPalette.ColorRole.Button,          QColor(60,  60,  60))
    palette.setColor(QPalette.ColorRole.ButtonText,      QColor(220, 220, 220))
    palette.setColor(QPalette.ColorRole.Highlight,       QColor(42,  130, 218))
    palette.setColor(QPalette.ColorRole.HighlightedText, QColor(255, 255, 255))
    app.setPalette(palette)

    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
