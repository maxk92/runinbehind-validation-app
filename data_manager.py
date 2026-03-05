"""
DataManager for rib-validation-app.

Same as rib-annotation-app, extended with:
  - load_annotations(csv_path) → list[dict]
  - save() accepts annotation_source kwarg (default "validated")
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Optional

import numpy as np

from config import FPS, OUTPUT_DIR, TRAIL_STEPS


@dataclass
class Segment:
    segment_id:  int
    start_frame: int
    end_frame:   int
    player_idx:  int  = -1
    player_name: str  = ""
    player_jid:  str  = ""
    team:        str  = ""


class DataManager:
    def __init__(self) -> None:
        self.xy_home = None
        self.xy_away = None
        self.xy_ball = None
        self.pitch   = None
        self.teamsheets: dict = {}

        self.n_frames: int = 0
        self.half:     str = "firstHalf"
        self.available_halves: list[str] = []

        self._positions_path: str = ""

        self.segments:  list[Segment] = []
        self._next_id:  int = 0

    # ------------------------------------------------------------------
    # Loading position data
    # ------------------------------------------------------------------

    def load(self, filepath_positions: str, filepath_mat_info: str, half: str) -> None:
        from floodlight.io.dfl import read_position_data_xml

        xy_objects, _, _, teamsheets, pitch = read_position_data_xml(
            filepath_positions=filepath_positions,
            filepath_mat_info=filepath_mat_info,
        )

        self._positions_path  = filepath_positions
        self.available_halves = list(xy_objects.keys())
        self.half = half if half in xy_objects else self.available_halves[0]

        half_data = xy_objects[self.half]
        self.xy_home    = half_data.get("Home")
        self.xy_away    = half_data.get("Away")
        self.xy_ball    = half_data.get("Ball")
        self.pitch      = pitch
        self.teamsheets = teamsheets

        self.n_frames  = len(self.xy_home.xy) if self.xy_home is not None else 0
        self.segments  = []
        self._next_id  = 0

    # ------------------------------------------------------------------
    # Loading annotation CSVs
    # ------------------------------------------------------------------

    @staticmethod
    def load_annotations(csv_path: str) -> list[dict]:
        """Load a rib-annotation-app CSV and return a list of segment dicts."""
        import pandas as pd
        df = pd.read_csv(csv_path)
        segs = []
        for _, row in df.iterrows():
            segs.append({
                "segment_id":  int(row.get("segment_id", -1)),
                "start_frame": int(row["start_frame"]),
                "end_frame":   int(row["end_frame"]),
                "player":      str(row.get("player", "")),
                "player_jid":  str(row.get("player_jid", "")),
                "team":        str(row.get("team", "")),
                "half":        str(row.get("half", "")),
            })
        return segs

    # ------------------------------------------------------------------
    # Teamsheet helpers
    # ------------------------------------------------------------------

    def _sorted_teamsheet(self, team: str):
        ts = self.teamsheets.get(team)
        if ts is None:
            return None
        df = ts.teamsheet if hasattr(ts, "teamsheet") else ts
        if "xID" in df.columns:
            return df.sort_values("xID").reset_index(drop=True)
        return df.reset_index(drop=True)

    def get_jersey_ids(self, team: str) -> list[str]:
        df = self._sorted_teamsheet(team)
        if df is None or "jID" not in df.columns:
            return []
        xy = self.xy_home if team == "Home" else self.xy_away
        n  = xy.N if xy is not None else len(df)
        return [str(int(v)) for v in df["jID"].iloc[:n]]

    def get_player_at_index(self, team: str, player_idx: int) -> dict:
        df = self._sorted_teamsheet(team)
        if df is None or player_idx < 0 or player_idx >= len(df):
            return {}
        row = df.iloc[player_idx]
        return {
            "player": str(row.get("player", f"P{player_idx}")),
            "jID":    str(int(row.get("jID", player_idx))),
            "team":   team,
        }

    # ------------------------------------------------------------------
    # Position queries
    # ------------------------------------------------------------------

    def get_trail_positions(self, frame_idx: int):
        if self.xy_home is None:
            return None, None, None, None, None, None

        frames = [
            max(0, min(self.n_frames - 1, frame_idx - (TRAIL_STEPS - 1 - i)))
            for i in range(TRAIL_STEPS)
        ]

        home_x = np.stack([self.xy_home.x[t] for t in frames])
        home_y = np.stack([self.xy_home.y[t] for t in frames])
        away_x = np.stack([self.xy_away.x[t] for t in frames])
        away_y = np.stack([self.xy_away.y[t] for t in frames])

        if self.xy_ball is not None:
            ball_x = np.stack([self.xy_ball.x[t] for t in frames])
            ball_y = np.stack([self.xy_ball.y[t] for t in frames])
        else:
            ball_x = ball_y = None

        return home_x, home_y, away_x, away_y, ball_x, ball_y

    @property
    def first_frame(self) -> int:
        return 0

    @property
    def last_frame(self) -> int:
        return max(0, self.n_frames - 1)

    # ------------------------------------------------------------------
    # Result segment management
    # ------------------------------------------------------------------

    def add_segment(self, start_frame: int, end_frame: int) -> Segment:
        seg = Segment(
            segment_id  = self._next_id,
            start_frame = min(start_frame, end_frame),
            end_frame   = max(start_frame, end_frame),
        )
        self.segments.append(seg)
        self._next_id += 1
        return seg

    def remove_segment(self, seg_idx: int) -> Optional[Segment]:
        if 0 <= seg_idx < len(self.segments):
            return self.segments.pop(seg_idx)
        return None

    def assign_player(self, seg_idx: int, player_name: str,
                      player_jid: str, team: str) -> None:
        if 0 <= seg_idx < len(self.segments):
            s = self.segments[seg_idx]
            s.player_name = player_name
            s.player_jid  = player_jid
            s.team        = team

    def update_boundary(self, seg_idx: int, side: str, frame: int) -> None:
        if not (0 <= seg_idx < len(self.segments)):
            return
        s = self.segments[seg_idx]
        if side == "start":
            s.start_frame = max(self.first_frame, min(s.end_frame - 1, frame))
        else:
            s.end_frame = min(self.last_frame, max(s.start_frame + 1, frame))

    # ------------------------------------------------------------------
    # Saving
    # ------------------------------------------------------------------

    def save(self, custom_path: str = "",
             annotation_source: str = "validated") -> str:
        import pandas as pd

        rows = [
            {
                "segment_id":        s.segment_id,
                "half":              self.half,
                "start_frame":       s.start_frame,
                "end_frame":         s.end_frame,
                "start_time_s":      round(s.start_frame / FPS, 3),
                "end_time_s":        round(s.end_frame   / FPS, 3),
                "duration_s":        round((s.end_frame - s.start_frame) / FPS, 3),
                "player":            s.player_name,
                "player_jid":        s.player_jid,
                "team":              s.team,
                "annotation_source": annotation_source,
            }
            for s in self.segments
        ]
        df = pd.DataFrame(rows)

        if custom_path:
            out_path = custom_path
        else:
            os.makedirs(OUTPUT_DIR, exist_ok=True)
            if self._positions_path:
                stem = os.path.splitext(os.path.basename(self._positions_path))[0]
                out_path = os.path.join(OUTPUT_DIR, f"{stem}_{self.half}_rib_validated.csv")
            else:
                out_path = os.path.join(OUTPUT_DIR, "rib_validated.csv")

        df.to_csv(out_path, index=False)
        return out_path
