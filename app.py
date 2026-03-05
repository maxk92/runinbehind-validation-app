"""
RIB Validation App — compare and validate runs-in-behind annotations.

Usage:
    cd rib-validation-app
    python app.py

Workflow:
  1. Load the tracking position XML files and (optionally) a video.
  2. Load two annotation CSV files produced by the rib-annotation-app.
  3. Both annotation sets appear in the top two timelines.
  4. Double-click a segment in either annotator timeline to copy it to
     the Result timeline.
  5. Drag segment boundaries in the Result timeline to adjust.
  6. Right-click a segment in the Result timeline to remove it.
  7. Save the validated result.
"""

from __future__ import annotations

import os
import sys

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QApplication,
    QColorDialog,
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QStatusBar,
    QVBoxLayout,
    QWidget,
)

from config import AWAY_COLOR, FPS, HOME_COLOR
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

        # Annotator segment lists (loaded from CSVs)
        self._ann1_segs: list[dict] = []
        self._ann2_segs: list[dict] = []

        # Team colours
        self._home_color = HOME_COLOR
        self._away_color = AWAY_COLOR

        # Guard against re-entrant range-sync
        self._syncing_range: bool = False

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

        # ── Row 2: annotation CSV pickers ─────────────────────────────
        ann_row = QHBoxLayout()
        ann_row.setSpacing(6)

        self._ann1_edit = QLineEdit(self)
        self._ann1_edit.setPlaceholderText("Annotator 1 CSV…")
        self._ann1_edit.setMinimumWidth(200)
        btn_ann1 = QPushButton("…", self)
        btn_ann1.setFixedWidth(28)
        btn_ann1.clicked.connect(self._browse_ann1)
        ann_row.addWidget(QLabel("Ann. 1:", self))
        ann_row.addWidget(self._ann1_edit)
        ann_row.addWidget(btn_ann1)

        ann_row.addSpacing(8)

        self._ann2_edit = QLineEdit(self)
        self._ann2_edit.setPlaceholderText("Annotator 2 CSV…")
        self._ann2_edit.setMinimumWidth(200)
        btn_ann2 = QPushButton("…", self)
        btn_ann2.setFixedWidth(28)
        btn_ann2.clicked.connect(self._browse_ann2)
        ann_row.addWidget(QLabel("Ann. 2:", self))
        ann_row.addWidget(self._ann2_edit)
        ann_row.addWidget(btn_ann2)

        ann_row.addSpacing(16)

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

        # Bottom: three labelled timelines
        bottom        = QWidget(self)
        bottom_layout = QVBoxLayout(bottom)
        bottom_layout.setContentsMargins(0, 2, 0, 2)
        bottom_layout.setSpacing(2)

        # Annotator 1 timeline (read-only)
        bottom_layout.addWidget(self._make_timeline_label(
            "Annotator 1  —  double-click a segment to add it to the Result"
        ))
        self._ann1_timeline = ValidationTimeline(self, readonly=True)
        bottom_layout.addWidget(self._ann1_timeline, stretch=1)

        # Annotator 2 timeline (read-only)
        bottom_layout.addWidget(self._make_timeline_label(
            "Annotator 2  —  double-click a segment to add it to the Result"
        ))
        self._ann2_timeline = ValidationTimeline(self, readonly=True)
        bottom_layout.addWidget(self._ann2_timeline, stretch=1)

        # Result timeline (editable)
        bottom_layout.addWidget(self._make_timeline_label(
            "Result  —  drag boundaries to adjust  |  right-click to remove"
        ))
        self._result_timeline = ValidationTimeline(self, readonly=False)
        bottom_layout.addWidget(self._result_timeline, stretch=1)

        self._status_label = QLabel("No data loaded", self)
        self._status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        bottom_layout.addWidget(self._status_label)

        v_splitter.addWidget(bottom)
        v_splitter.setSizes([580, 380])

        root.addWidget(v_splitter, stretch=1)

        # ── Signal wiring ─────────────────────────────────────────────
        self._video_panel.frame_changed.connect(self._on_frame_changed)

        for tl in (self._ann1_timeline, self._ann2_timeline, self._result_timeline):
            tl.seek_requested.connect(self._video_panel.seek_to_frame)
            tl.visible_range_changed.connect(self._on_range_changed)

        self._ann1_timeline.segment_double_clicked.connect(self._on_ann1_double_clicked)
        self._ann2_timeline.segment_double_clicked.connect(self._on_ann2_double_clicked)

        self._result_timeline.boundary_dragged.connect(self._on_result_boundary_dragged)
        self._result_timeline.boundary_committed.connect(self._on_result_boundary_committed)
        self._result_timeline.segment_removed.connect(self._on_result_segment_removed)

        self.setStatusBar(QStatusBar(self))

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
        for i, seg in enumerate(self._ann1_segs):
            color = self._team_color(seg["team"])
            self._ann1_timeline.update_segment(i, seg["player_jid"], color)
        for i, seg in enumerate(self._ann2_segs):
            color = self._team_color(seg["team"])
            self._ann2_timeline.update_segment(i, seg["player_jid"], color)
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

    def _browse_ann1(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Select Annotator 1 CSV", "", "CSV files (*.csv);;All files (*)"
        )
        if path:
            self._ann1_edit.setText(path)

    def _browse_ann2(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Select Annotator 2 CSV", "", "CSV files (*.csv);;All files (*)"
        )
        if path:
            self._ann2_edit.setText(path)

    # ------------------------------------------------------------------
    # Load
    # ------------------------------------------------------------------

    def _on_load(self) -> None:
        pos_path  = self._pos_edit.text().strip()
        mat_path  = self._mat_edit.text().strip()
        vid_path  = self._vid_edit.text().strip()
        ann1_path = self._ann1_edit.text().strip()
        ann2_path = self._ann2_edit.text().strip()
        half      = self._half_combo.currentText()

        if not ann1_path and not ann2_path:
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
        self._ann1_segs = []
        self._ann2_segs = []

        if ann1_path:
            try:
                self._ann1_segs = DataManager.load_annotations(ann1_path)
            except Exception as exc:
                QMessageBox.critical(self, "Annotator 1 CSV load failed", str(exc))

        if ann2_path:
            try:
                self._ann2_segs = DataManager.load_annotations(ann2_path)
            except Exception as exc:
                QMessageBox.critical(self, "Annotator 2 CSV load failed", str(exc))

        # ── Determine frame range ──────────────────────────────────────
        if self._dm.xy_home is not None:
            first = self._dm.first_frame
            last  = self._dm.last_frame
        else:
            all_frames = (
                [s["start_frame"] for s in self._ann1_segs + self._ann2_segs] +
                [s["end_frame"]   for s in self._ann1_segs + self._ann2_segs]
            )
            first = min(all_frames) if all_frames else 0
            last  = max(all_frames) if all_frames else 100

        # ── Reset all timelines ────────────────────────────────────────
        for tl in (self._ann1_timeline, self._ann2_timeline, self._result_timeline):
            tl.set_range(first, last)

        # ── Populate annotator timelines ───────────────────────────────
        for seg in self._ann1_segs:
            color = self._team_color(seg["team"])
            self._ann1_timeline.add_segment(
                seg["start_frame"], seg["end_frame"],
                seg["segment_id"], seg["player_jid"], color,
            )

        for seg in self._ann2_segs:
            color = self._team_color(seg["team"])
            self._ann2_timeline.add_segment(
                seg["start_frame"], seg["end_frame"],
                seg["segment_id"], seg["player_jid"], color,
            )

        # ── Load video ─────────────────────────────────────────────────
        if vid_path and os.path.exists(vid_path):
            self._video_panel.load_video(vid_path, offset_frames=0)
        elif vid_path:
            self.statusBar().showMessage(
                f"Video not found: {vid_path} — continuing without video.", 6000
            )

        n1  = len(self._ann1_segs)
        n2  = len(self._ann2_segs)
        msg = (
            f"Loaded {half}   |   "
            f"Ann. 1: {n1} segment{'s' if n1 != 1 else ''}   "
            f"Ann. 2: {n2} segment{'s' if n2 != 1 else ''}   "
            f"Range: frames {first}–{last}"
        )
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

        for tl in (self._ann1_timeline, self._ann2_timeline, self._result_timeline):
            tl.update_cursor(frame_idx)

    # ------------------------------------------------------------------
    # Timeline range synchronisation
    # ------------------------------------------------------------------

    def _on_range_changed(self, first: int, last: int) -> None:
        if self._syncing_range:
            return
        self._syncing_range = True
        for tl in (self._ann1_timeline, self._ann2_timeline, self._result_timeline):
            tl.set_xlim(first, last)
        self._syncing_range = False

    # ------------------------------------------------------------------
    # Double-click: copy annotator segment → result
    # ------------------------------------------------------------------

    def _on_ann1_double_clicked(self, seg_idx: int) -> None:
        self._copy_to_result(self._ann1_segs, seg_idx, annotator=1)

    def _on_ann2_double_clicked(self, seg_idx: int) -> None:
        self._copy_to_result(self._ann2_segs, seg_idx, annotator=2)

    def _copy_to_result(self, segs: list[dict], seg_idx: int, annotator: int) -> None:
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
        n1 = len(self._ann1_segs)
        n2 = len(self._ann2_segs)
        nr = len(self._dm.segments)
        self._status_label.setText(
            f"Ann. 1: {n1} seg.   |   Ann. 2: {n2} seg.   |   Result: {nr} seg."
        )


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
