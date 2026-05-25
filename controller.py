# controller.py
# Injects mouse events into the DOSBox window using pynput.
# Window discovery and focus management use wmctrl (apt install wmctrl).
#
# Design notes:
# - focus_window() is called once per episode (in env.reset()), not on every action.
#   This avoids the ~150 ms wmctrl overhead adding up at 20 Hz.
# - Mouse clicks go to the window centre.  DOSBox does not require a specific
#   in-game pixel for clicks to register.
# - Pitch control (mouse_up / mouse_down) uses RELATIVE movement so the skier's
#   pitch responds to how much the mouse moved, not where it is on screen.
# - After the IN_FLIGHT phase the environment calls reset_position() to re-centre
#   the mouse before the landing phase begins.

import subprocess
import time
from typing import Optional, Tuple

from pynput.mouse import Button, Controller as MouseController

import config


class DSJ2Controller:
    """Handles all mouse input injection for the DSJ2 RL agent."""

    def __init__(self):
        self.mouse = MouseController()
        self._win_x: Optional[int] = None
        self._win_y: Optional[int] = None
        self._win_w: Optional[int] = None
        self._win_h: Optional[int] = None
        self._refresh_window_geometry()

    # ── Window management ─────────────────────────────────────────────────────

    def _refresh_window_geometry(self) -> bool:
        """
        Query wmctrl for the DOSBox window position and size.
        Returns True if the window was found, False otherwise.
        wmctrl -lG output format:
            WIN_ID  DESKTOP  X  Y  W  H  HOST  TITLE...
        """
        try:
            result = subprocess.run(
                ["wmctrl", "-lG"],
                capture_output=True, text=True, timeout=3,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return False

        for line in result.stdout.splitlines():
            if config.DOSBOX_WINDOW_TITLE.lower() in line.lower():
                try:
                    parts = line.split()
                    self._win_x = int(parts[2])
                    self._win_y = int(parts[3])
                    self._win_w = int(parts[4])
                    self._win_h = int(parts[5])
                    return True
                except (IndexError, ValueError):
                    continue

        return False

    @property
    def window_center(self) -> Tuple[int, int]:
        """Screen coordinates of the DOSBox window centre."""
        if None in (self._win_x, self._win_y, self._win_w, self._win_h):
            if not self._refresh_window_geometry():
                raise RuntimeError(
                    "Cannot locate DOSBox window. "
                    "Is DOSBox running and wmctrl installed?"
                )
        cx = self._win_x + self._win_w // 2
        cy = self._win_y + self._win_h // 2
        return cx, cy

    def focus_window(self) -> None:
        """
        Bring the DOSBox window to the foreground.
        Call once at the start of each episode (env.reset()), not on every action.
        """
        try:
            subprocess.run(
                ["wmctrl", "-a", config.DOSBOX_WINDOW_TITLE],
                capture_output=True, timeout=2,
            )
            time.sleep(config.WINDOW_FOCUS_SLEEP_S)
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass
        # Refresh geometry in case the window moved
        self._refresh_window_geometry()

    # ── Position helpers ──────────────────────────────────────────────────────

    def reset_position(self) -> None:
        """
        Move the mouse cursor to the window centre.
        Call after IN_FLIGHT phase ends so accumulated pitch-control drift
        does not carry over into the landing phase.
        """
        cx, cy = self.window_center
        self.mouse.position = (cx, cy)

    # ── Actions ───────────────────────────────────────────────────────────────

    def do_nothing(self) -> None:
        """No-op action."""
        pass

    def click_lmb(self) -> None:
        """Left mouse button click at the window centre."""
        cx, cy = self.window_center
        self.mouse.position = (cx, cy)
        self.mouse.press(Button.left)
        time.sleep(config.CLICK_DURATION_S)
        self.mouse.release(Button.left)

    def click_rmb(self) -> None:
        """Right mouse button click at the window centre."""
        cx, cy = self.window_center
        self.mouse.position = (cx, cy)
        self.mouse.press(Button.right)
        time.sleep(config.CLICK_DURATION_S)
        self.mouse.release(Button.right)

    def mouse_up(self, delta: Optional[int] = None) -> None:
        """
        Move the mouse upward by `delta` pixels for pitch-up (lean back) control.
        Uses absolute positioning clamped to the window so the cursor can never
        drift outside DOSBox regardless of how many consecutive moves are made.
        X is always held at window centre to prevent horizontal drift.
        """
        delta = delta if delta is not None else config.MOUSE_DELTA_PX
        cx, _cy       = self.window_center
        _curr_x, curr_y = self.mouse.position
        new_y = max(self._win_y, curr_y - delta)
        self.mouse.position = (cx, new_y)

    def mouse_down(self, delta: Optional[int] = None) -> None:
        """
        Move the mouse downward by `delta` pixels for pitch-down (lean forward).
        Clamped to window bottom; X held at window centre.
        """
        delta = delta if delta is not None else config.MOUSE_DELTA_PX
        cx, _cy       = self.window_center
        _curr_x, curr_y = self.mouse.position
        new_y = min(self._win_y + self._win_h, curr_y + delta)
        self.mouse.position = (cx, new_y)

    # ── Reset helpers ─────────────────────────────────────────────────────────

    def send_reset_clicks(self) -> None:
        """
        Send the two LMB clicks that dismiss the post-jump results screen
        and return the game to the top of the ramp for the next episode.

        Call from env.reset() AFTER the terminal reward has been computed
        (i.e. after RESULTS_WAIT_S from touchdown has elapsed).
        """
        self.focus_window()
        self.click_lmb()
        time.sleep(config.RESET_CLICK_DELAY_S)
        self.click_lmb()
        # Re-centre mouse so the next episode starts from a known cursor position
        self.reset_position()


# ── Quick smoke-test ──────────────────────────────────────────────────────────
if __name__ == "__main__":
    import sys

    ctrl = DSJ2Controller()

    try:
        cx, cy = ctrl.window_center
        print(f"DOSBox window centre: ({cx}, {cy})")
        print(f"Window geometry: x={ctrl._win_x} y={ctrl._win_y} "
              f"w={ctrl._win_w} h={ctrl._win_h}")
    except RuntimeError as e:
        print(f"[ERROR] {e}")
        sys.exit(1)

    print("\nFocusing window and running action smoke-test in 2 s...")
    time.sleep(2)

    ctrl.focus_window()
    print("  mouse_up    ×3")
    for _ in range(3):
        ctrl.mouse_up()
        time.sleep(0.1)

    print("  mouse_down  ×3")
    for _ in range(3):
        ctrl.mouse_down()
        time.sleep(0.1)

    print("  reset_position")
    ctrl.reset_position()

    print("\nSmoke-test complete.  No clicks were sent.")
    print("Run with --click to also test LMB/RMB clicks (will interact with the game).")

    if "--click" in sys.argv:
        print("\n  click_lmb")
        ctrl.click_lmb()
        time.sleep(0.5)
        print("  click_rmb")
        ctrl.click_rmb()
