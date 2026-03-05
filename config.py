import os

FPS = 25

OUTPUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "output")

PITCH_LENGTH = 105.0
PITCH_WIDTH  = 68.0

# Trail: current frame + this many previous frames
TRAIL_STEPS = 26         # indices 0..25, 25 = current frame

HOME_COLOR = "#5B9BD5"   # blue
AWAY_COLOR = "#FF6B6B"   # red/salmon
BALL_COLOR = "#FFFFFF"   # white

# Player-click detection radius (metres in DFL coordinate system)
CLICK_THRESHOLD_M = 3.5

# Boundary drag detection (pixels)
BOUNDARY_THRESHOLD_PX = 10
