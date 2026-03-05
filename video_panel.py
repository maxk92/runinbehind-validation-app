"""
VideoPanel — wraps QMediaPlayer with frame-accurate seek/read,
playback controls, and a polling timer that emits frame_changed(int).

Video is displayed via QVideoSink → QLabel so it works on both
Wayland and X11 (QVideoWidget requires a native window handle that
Wayland doesn't provide reliably).
"""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer, QVideoFrame, QVideoSink
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from config import FPS


class VideoPanel(QWidget):
    """Left panel: video + playback controls."""

    frame_changed = Signal(int)   # emitted every timer tick with current tracking frame

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        self.offset_frames: int = 0

        # Media player — use QVideoSink so display works on Wayland
        self._player = QMediaPlayer(self)
        self._audio  = QAudioOutput(self)
        self._player.setAudioOutput(self._audio)

        self._sink = QVideoSink(self)
        self._player.setVideoOutput(self._sink)
        self._sink.videoFrameChanged.connect(self._on_video_frame)

        # Polling timer (drives frame_changed signal)
        self._timer = QTimer(self)
        self._timer.setInterval(80)
        self._timer.timeout.connect(self._on_timer)

        self._player.playbackStateChanged.connect(self._on_playback_state_changed)
        self._player.durationChanged.connect(self._on_duration_changed)
        self._player.errorOccurred.connect(self._on_error)

        self._build_ui()

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)

        # Video display label
        self._video_label = QLabel(self)
        self._video_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._video_label.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        self._video_label.setStyleSheet("background: black;")
        self._video_label.setText("No video loaded")
        layout.addWidget(self._video_label, stretch=1)

        # Seek slider
        self._seek_slider = QSlider(Qt.Orientation.Horizontal, self)
        self._seek_slider.setRange(0, 0)
        self._seek_slider.sliderMoved.connect(self._on_slider_moved)
        layout.addWidget(self._seek_slider)

        # Frame / time label
        self._label = QLabel("Frame: —   00:00:00", self)
        self._label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self._label)

        # Controls row
        ctrl = QHBoxLayout()
        ctrl.setSpacing(6)

        self._btn_step_back = QPushButton("◀", self)
        self._btn_step_back.setToolTip("Step back 1 frame")
        self._btn_step_back.setFixedWidth(32)
        self._btn_step_back.clicked.connect(lambda: self._step(-1))

        self._btn_play = QPushButton("▶", self)
        self._btn_play.setFixedWidth(40)
        self._btn_play.clicked.connect(self._toggle_play)

        self._btn_step_fwd = QPushButton("▶", self)
        self._btn_step_fwd.setToolTip("Step forward 1 frame")
        self._btn_step_fwd.setFixedWidth(32)
        self._btn_step_fwd.clicked.connect(lambda: self._step(1))

        self._btn_minus5 = QPushButton("−5s", self)
        self._btn_minus5.setFixedWidth(42)
        self._btn_minus5.clicked.connect(lambda: self._jump_seconds(-5))

        self._btn_plus5 = QPushButton("+5s", self)
        self._btn_plus5.setFixedWidth(42)
        self._btn_plus5.clicked.connect(lambda: self._jump_seconds(5))

        for label, rate in [("¼×", 0.25), ("½×", 0.5), ("1×", 1.0), ("2×", 2.0)]:
            btn = QPushButton(label, self)
            btn.setFixedWidth(36)
            btn.clicked.connect(lambda _=None, r=rate: self._player.setPlaybackRate(r))
            ctrl.addWidget(btn)

        ctrl.insertWidget(0, self._btn_minus5)
        ctrl.insertWidget(1, self._btn_step_back)
        ctrl.insertWidget(2, self._btn_play)
        ctrl.insertWidget(3, self._btn_step_fwd)
        ctrl.insertWidget(4, self._btn_plus5)
        ctrl.addStretch()

        layout.addLayout(ctrl)

        # Offset calibration row
        offset_row = QHBoxLayout()
        self._offset_label = QLabel("Offset: 0 frames", self)
        btn_set_offset = QPushButton("Set offset here", self)
        btn_set_offset.setToolTip(
            "Mark the current video position as tracking frame 0.\n"
            "Use this to align the video with the tracking data."
        )
        btn_set_offset.clicked.connect(self._set_offset_here)
        btn_reset_offset = QPushButton("Reset offset", self)
        btn_reset_offset.clicked.connect(self._reset_offset)
        offset_row.addWidget(self._offset_label)
        offset_row.addStretch()
        offset_row.addWidget(btn_set_offset)
        offset_row.addWidget(btn_reset_offset)
        layout.addLayout(offset_row)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def load_video(self, path: str, offset_frames: int = 0) -> None:
        self.offset_frames = offset_frames
        self._update_offset_label()
        self._video_label.setText("Loading…")
        self._player.setSource(QUrl.fromLocalFile(path))
        self._timer.start()

    def seek_to_frame(self, frame: int) -> None:
        ms = max(0, (frame - self.offset_frames) * 1000 // FPS)
        self._player.setPosition(ms)
        self._emit_frame()

    def current_frame(self) -> int:
        return int(self._player.position() * FPS / 1000) + self.offset_frames

    def is_loaded(self) -> bool:
        return self._player.source().isValid()

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _on_video_frame(self, frame: QVideoFrame) -> None:
        """Convert each decoded video frame to a QPixmap and show it."""
        if not frame.isValid():
            return
        img = frame.toImage()
        if img.isNull():
            return
        # Convert to a format Qt can display, then scale to label size
        img = img.convertToFormat(QImage.Format.Format_RGB32)
        pixmap = QPixmap.fromImage(img)
        label_size = self._video_label.size()
        self._video_label.setPixmap(
            pixmap.scaled(
                label_size,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.FastTransformation,
            )
        )

    def _on_timer(self) -> None:
        self._emit_frame()
        self._update_seek_slider()

    def _emit_frame(self) -> None:
        frame = self.current_frame()
        self.frame_changed.emit(frame)
        pos_ms = self._player.position()
        h = pos_ms // 3_600_000
        m = (pos_ms % 3_600_000) // 60_000
        s = (pos_ms % 60_000) // 1000
        self._label.setText(f"Frame: {frame}   {h:02d}:{m:02d}:{s:02d}")

    def _toggle_play(self) -> None:
        if self._player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self._player.pause()
        else:
            self._player.play()

    def _on_playback_state_changed(self, state: QMediaPlayer.PlaybackState) -> None:
        playing = state == QMediaPlayer.PlaybackState.PlayingState
        self._btn_play.setText("⏸" if playing else "▶")

    def _on_error(self, error: QMediaPlayer.Error, error_string: str) -> None:
        self._video_label.setText(f"Video error: {error_string}")

    def _step(self, frames: int) -> None:
        self._player.pause()
        new_ms = self._player.position() + int(frames * 1000 / FPS)
        self._player.setPosition(max(0, new_ms))
        self._emit_frame()

    def _jump_seconds(self, seconds: float) -> None:
        new_ms = self._player.position() + int(seconds * 1000)
        self._player.setPosition(max(0, new_ms))

    def _on_duration_changed(self, duration_ms: int) -> None:
        self._seek_slider.setRange(0, duration_ms)

    def _on_slider_moved(self, value: int) -> None:
        self._player.setPosition(value)
        self._emit_frame()

    def _update_seek_slider(self) -> None:
        self._seek_slider.blockSignals(True)
        self._seek_slider.setValue(self._player.position())
        self._seek_slider.blockSignals(False)

    def _set_offset_here(self) -> None:
        self.offset_frames = -int(self._player.position() * FPS / 1000)
        self._update_offset_label()
        self._emit_frame()

    def _reset_offset(self) -> None:
        self.offset_frames = 0
        self._update_offset_label()
        self._emit_frame()

    def _update_offset_label(self) -> None:
        self._offset_label.setText(f"Offset: {self.offset_frames} frames")
