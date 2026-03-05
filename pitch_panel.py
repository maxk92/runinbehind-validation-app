"""
PitchPanel — shows all players (home + away + ball) with a 26-step trail.

One RGBA scatter per team covers all trail steps.
A separate, larger scatter handles the "current frame" positions and
is used for player click-to-assign detection.
Jersey IDs are displayed as animated text labels at current positions.
"""

from __future__ import annotations

import numpy as np
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
from matplotlib.figure import Figure
from PySide6.QtCore import Signal
from PySide6.QtWidgets import QSizePolicy, QVBoxLayout, QWidget

from config import (
    AWAY_COLOR, BALL_COLOR, HOME_COLOR,
    CLICK_THRESHOLD_M, PITCH_LENGTH, PITCH_WIDTH, TRAIL_STEPS,
)


def _hex_to_rgb(hex_color: str) -> tuple[float, float, float]:
    h = hex_color.lstrip("#")
    return tuple(int(h[i: i + 2], 16) / 255.0 for i in (0, 2, 4))


# Non-linear alpha ramp: oldest = very faint, current = opaque
_TRAIL_ALPHAS = (np.linspace(0, 1, TRAIL_STEPS) ** 2) * 0.95 + 0.05


class PitchPanel(QWidget):
    player_clicked = Signal(str, int)   # (team "Home"/"Away", player_idx)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        fig = Figure(figsize=(5, 4), tight_layout=True)
        fig.patch.set_facecolor("#1a472a")
        self._canvas = FigureCanvasQTAgg(fig)
        self._ax = fig.add_subplot(111)
        self._draw_pitch()

        # Trail scatters — RGBA, one per team, covers all TRAIL_STEPS at once
        self._home_trail = self._ax.scatter([], [], s=80,  zorder=3, animated=True)
        self._away_trail = self._ax.scatter([], [], s=80,  zorder=3, animated=True)
        self._ball_trail = self._ax.scatter([], [], s=55,  zorder=3, animated=True)

        # Current-frame scatters (larger, on top)
        self._home_curr  = self._ax.scatter([], [], s=160, zorder=4, animated=True)
        self._away_curr  = self._ax.scatter([], [], s=160, zorder=4, animated=True)
        self._ball_curr  = self._ax.scatter([], [], s=100, zorder=5, animated=True)

        # Jersey ID text labels (created per-load in initialize())
        self._home_texts: list = []
        self._away_texts: list = []

        # Current frame positions — used for click detection
        self._home_curr_x: np.ndarray | None = None
        self._home_curr_y: np.ndarray | None = None
        self._away_curr_x: np.ndarray | None = None
        self._away_curr_y: np.ndarray | None = None

        self._home_color = HOME_COLOR
        self._away_color = AWAY_COLOR

        self._bg = None
        self._canvas.mpl_connect("draw_event",         self._on_draw)
        self._canvas.mpl_connect("resize_event",       self._on_resize)
        self._canvas.mpl_connect("button_press_event", self._on_click)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._canvas)

    # ------------------------------------------------------------------
    # Pitch drawing
    # ------------------------------------------------------------------

    def _draw_pitch(self) -> None:
        import matplotlib.patches as mpatches
        ax  = self._ax
        ax.set_facecolor("#2d6a2d")
        ax.set_aspect("equal")
        ax.set_xlim(-PITCH_LENGTH / 2 - 2, PITCH_LENGTH / 2 + 2)
        ax.set_ylim(-PITCH_WIDTH  / 2 - 2, PITCH_WIDTH  / 2 + 2)
        ax.axis("off")

        lw, col = 1.2, "white"
        hw, hh  = PITCH_LENGTH / 2, PITCH_WIDTH / 2

        ax.plot([-hw, hw, hw, -hw, -hw], [-hh, -hh, hh, hh, -hh], color=col, lw=lw)
        ax.plot([0, 0], [-hh, hh], color=col, lw=lw)
        ax.add_patch(mpatches.Circle((0, 0), 9.15, fill=False, color=col, lw=lw))
        ax.plot(0, 0, ".", color=col, markersize=3)

        for sign in (-1, 1):
            x0 = sign * hw
            ax.plot([x0, sign*(hw-16.5), sign*(hw-16.5), x0],
                    [-20.16, -20.16, 20.16, 20.16], color=col, lw=lw)
            ax.plot([x0, sign*(hw-5.5),  sign*(hw-5.5),  x0],
                    [-9.16,  -9.16,  9.16,  9.16],  color=col, lw=lw)
            ax.plot(sign*(hw-11), 0, ".", color=col, markersize=3)
            theta = np.linspace(-np.pi/2, np.pi/2, 80)
            arc_x = sign*(hw-11) + 9.15*np.cos(theta)*(-sign)
            arc_y = 9.15*np.sin(theta)
            outside = (arc_x * sign) < (sign * (hw-16.5)) * sign
            if np.any(outside):
                ax.plot(arc_x[outside], arc_y[outside], color=col, lw=lw)

    # ------------------------------------------------------------------
    # Blit cache
    # ------------------------------------------------------------------

    def _on_draw(self, _event) -> None:
        self._bg = self._canvas.copy_from_bbox(self._ax.bbox)

    def _on_resize(self, _event) -> None:
        self._bg = None

    # ------------------------------------------------------------------
    # Initialization (call after data load)
    # ------------------------------------------------------------------

    def initialize(self, home_jids: list[str], away_jids: list[str],
                   home_color: str, away_color: str) -> None:
        self._home_color = home_color
        self._away_color = away_color

        # Remove old text artists
        for t in self._home_texts + self._away_texts:
            t.remove()
        self._home_texts.clear()
        self._away_texts.clear()

        # Create jersey-number labels (animated, positioned at player locations)
        for jid in home_jids:
            t = self._ax.text(0, 0, jid, ha="center", va="center",
                              fontsize=6, color="white", fontweight="bold",
                              zorder=6, animated=True)
            self._home_texts.append(t)

        for jid in away_jids:
            t = self._ax.text(0, 0, jid, ha="center", va="center",
                              fontsize=6, color="white", fontweight="bold",
                              zorder=6, animated=True)
            self._away_texts.append(t)

        # Clear current scatter data
        for sc in (self._home_trail, self._away_trail, self._ball_trail,
                   self._home_curr,  self._away_curr,  self._ball_curr):
            sc.set_offsets(np.empty((0, 2)))

        self._home_curr_x = None
        self._home_curr_y = None
        self._away_curr_x = None
        self._away_curr_y = None

        self._canvas.draw()

    # ------------------------------------------------------------------
    # Team color update
    # ------------------------------------------------------------------

    def set_team_colors(self, home_color: str, away_color: str) -> None:
        self._home_color = home_color
        self._away_color = away_color
        self._bg = None      # force rebuild on next frame tick

    # ------------------------------------------------------------------
    # Per-frame update
    # ------------------------------------------------------------------

    def update_frame(
        self,
        home_x: np.ndarray,   # (TRAIL_STEPS, n_home)
        home_y: np.ndarray,
        away_x: np.ndarray,   # (TRAIL_STEPS, n_away)
        away_y: np.ndarray,
        ball_x,               # (TRAIL_STEPS, 1) or None
        ball_y,
    ) -> None:
        # Store current frame positions for click detection
        self._home_curr_x = home_x[-1]
        self._home_curr_y = home_y[-1]
        self._away_curr_x = away_x[-1]
        self._away_curr_y = away_y[-1]

        self._update_team_scatter(
            self._home_trail, self._home_curr,
            home_x, home_y, self._home_color,
        )
        self._update_team_scatter(
            self._away_trail, self._away_curr,
            away_x, away_y, self._away_color,
        )
        self._update_ball_scatter(ball_x, ball_y)
        self._update_texts(home_x[-1], home_y[-1], away_x[-1], away_y[-1])

        all_artists = [
            self._home_trail, self._away_trail, self._ball_trail,
            self._home_curr,  self._away_curr,  self._ball_curr,
            *self._home_texts, *self._away_texts,
        ]
        if self._bg is not None:
            self._canvas.restore_region(self._bg)
            for a in all_artists:
                self._ax.draw_artist(a)
            self._canvas.blit(self._ax.bbox)
        else:
            self._canvas.draw_idle()

    def _update_team_scatter(self, trail_sc, curr_sc, xs, ys, hex_color):
        r, g, b = _hex_to_rgb(hex_color)

        # ---- trail ----
        trail_pts, trail_colors = [], []
        for step_i in range(TRAIL_STEPS - 1):   # all but the last (current)
            x, y  = xs[step_i], ys[step_i]
            valid = np.isfinite(x) & np.isfinite(y)
            if not valid.any():
                continue
            alpha = float(_TRAIL_ALPHAS[step_i])
            for xi, yi in zip(x[valid], y[valid]):
                trail_pts.append([xi, yi])
                trail_colors.append([r, g, b, alpha])

        if trail_pts:
            trail_sc.set_offsets(np.array(trail_pts))
            trail_sc.set_facecolors(np.array(trail_colors))
            trail_sc.set_edgecolors("none")
        else:
            trail_sc.set_offsets(np.empty((0, 2)))

        # ---- current frame ----
        cx, cy = xs[-1], ys[-1]
        valid  = np.isfinite(cx) & np.isfinite(cy)
        if valid.any():
            curr_sc.set_offsets(np.c_[cx[valid], cy[valid]])
            curr_sc.set_facecolors([[r, g, b, 1.0]] * valid.sum())
            curr_sc.set_edgecolors("white")
            curr_sc.set_linewidths(0.5)
        else:
            curr_sc.set_offsets(np.empty((0, 2)))

    def _update_ball_scatter(self, ball_x, ball_y) -> None:
        r, g, b = _hex_to_rgb(BALL_COLOR)
        if ball_x is None:
            for sc in (self._ball_trail, self._ball_curr):
                sc.set_offsets(np.empty((0, 2)))
            return

        trail_pts, trail_colors = [], []
        for step_i in range(TRAIL_STEPS - 1):
            bx, by = ball_x[step_i].ravel(), ball_y[step_i].ravel()
            valid = np.isfinite(bx) & np.isfinite(by)
            if not valid.any():
                continue
            alpha = float(_TRAIL_ALPHAS[step_i])
            for xi, yi in zip(bx[valid], by[valid]):
                trail_pts.append([xi, yi])
                trail_colors.append([r, g, b, alpha])

        if trail_pts:
            self._ball_trail.set_offsets(np.array(trail_pts))
            self._ball_trail.set_facecolors(np.array(trail_colors))
            self._ball_trail.set_edgecolors("none")
        else:
            self._ball_trail.set_offsets(np.empty((0, 2)))

        bx_curr = ball_x[-1].ravel()
        by_curr = ball_y[-1].ravel()
        valid   = np.isfinite(bx_curr) & np.isfinite(by_curr)
        if valid.any():
            self._ball_curr.set_offsets(np.c_[bx_curr[valid], by_curr[valid]])
            self._ball_curr.set_facecolors([[r, g, b, 1.0]] * valid.sum())
            self._ball_curr.set_edgecolors("#888888")
            self._ball_curr.set_linewidths(0.5)
        else:
            self._ball_curr.set_offsets(np.empty((0, 2)))

    def _update_texts(self, home_x, home_y, away_x, away_y) -> None:
        for i, t in enumerate(self._home_texts):
            if i < len(home_x) and np.isfinite(home_x[i]) and np.isfinite(home_y[i]):
                t.set_position((home_x[i], home_y[i]))
                t.set_visible(True)
            else:
                t.set_visible(False)

        for i, t in enumerate(self._away_texts):
            if i < len(away_x) and np.isfinite(away_x[i]) and np.isfinite(away_y[i]):
                t.set_position((away_x[i], away_y[i]))
                t.set_visible(True)
            else:
                t.set_visible(False)

    # ------------------------------------------------------------------
    # Click-to-assign player
    # ------------------------------------------------------------------

    def _on_click(self, event) -> None:
        if event.inaxes is not self._ax or event.button != 1:
            return
        cx, cy = event.xdata, event.ydata

        best_team, best_idx, best_dist = None, -1, float("inf")

        for team, xs, ys in (
            ("Home", self._home_curr_x, self._home_curr_y),
            ("Away", self._away_curr_x, self._away_curr_y),
        ):
            if xs is None:
                continue
            for i, (x, y) in enumerate(zip(xs, ys)):
                if not (np.isfinite(x) and np.isfinite(y)):
                    continue
                d = np.hypot(x - cx, y - cy)
                if d < best_dist:
                    best_dist, best_team, best_idx = d, team, i

        if best_team is not None and best_dist <= CLICK_THRESHOLD_M:
            self.player_clicked.emit(best_team, best_idx)
