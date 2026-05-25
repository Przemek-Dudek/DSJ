# dsj2_env.py
# Gymnasium environment for Deluxe Ski Jump 2 (DSJ2) running in DOSBox.
#
# Observation space  : Box(9,) float32 — all values normalised to [-1, 1]
#   [x_vel, y_vel, speed, y_pos, x_pos, tilt, wind_speed, wind_dir, phase]
#
# Action space       : Discrete(5)
#   0 = do nothing | 1 = LMB click | 2 = RMB click
#   3 = mouse up    | 4 = mouse down
#
# Action masking     : action_masks() → bool[5] for use with MaskablePPO.
#   Each game phase allows only a subset of the 5 actions (see config.PHASE_ACTION_MASKS).
#
# Episode lifecycle  :
#   WAITING → ON_RAMP → IN_FLIGHT → LANDING → (terminated)
#   reset() sends the two LMB clicks to dismiss results and restart the ramp.

import os
import signal
import subprocess
import time
from statistics import median
from typing import Any, Dict, Optional, Tuple

import numpy as np
import gymnasium as gym
from gymnasium import spaces

import config
from controller import DSJ2Controller
from telemetry import DSJ2MemoryDirect, find_base_auto, find_pid, verify_base


# Human-readable phase names for logging
_PHASE_NAMES = {
    config.PHASE_WAITING:   "WAITING",
    config.PHASE_ON_RAMP:   "ON_RAMP",
    config.PHASE_IN_FLIGHT: "IN_FLIGHT",
    config.PHASE_LANDING:   "LANDING",
}


class DSJ2Env(gym.Env):
    """
    Gymnasium environment wrapping Deluxe Ski Jump 2 via DOSBox memory telemetry
    and pynput mouse injection.
    """

    metadata = {"render_modes": []}

    # ── Construction ──────────────────────────────────────────────────────────

    def __init__(self, verbose: bool = True):
        super().__init__()
        self.verbose = verbose

        # Spaces
        self.observation_space = spaces.Box(
            low=-1.0, high=1.0, shape=(9,), dtype=np.float32
        )
        self.action_space = spaces.Discrete(config.N_ACTIONS)

        # Sub-systems
        self.telemetry: Optional[DSJ2MemoryDirect] = None
        self._dosbox_proc: Optional[subprocess.Popen] = None
        self._spawn_and_connect()
        self.controller = DSJ2Controller()

        # Episode state (initialised properly in reset())
        self.phase: int = config.PHASE_WAITING
        self.step_count: int = 0
        self.landing_step_count: int = 0
        self.landing_start_time: float = 0.0

        self.prev_x_pos: float = 0.0
        self.prev_y_pos: float = 0.0
        self.prev_y_vel: float = 0.0

        self.flight_frames: int = 0
        self.rising_streak: int = 0

        self._prev_phase: int = config.PHASE_WAITING
        self._last_raw_state: Dict[str, float] = self._zero_state()

        # Accumulates obs clip events for diagnostic logging
        self._obs_clip_count: int = 0

    # ── DOSBox spawn + telemetry connect ──────────────────────────────────────

    @staticmethod
    def _kill_existing_dosbox() -> None:
        """Terminate any already-running DOSBox processes so we can spawn a fresh child."""
        existing = find_pid(config.DOSBOX_PROCESS_NAME)
        if existing is not None:
            try:
                os.kill(existing, signal.SIGTERM)
                time.sleep(1.0)
                # SIGKILL if still alive
                try:
                    os.kill(existing, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            except ProcessLookupError:
                pass

    def _spawn_dosbox(self) -> subprocess.Popen:
        """
        Launch DOSBox as a direct child of this Python process.
        Being a child means we can read its /proc/<pid>/mem regardless of
        the system's kernel.yama.ptrace_scope setting.
        """
        self._kill_existing_dosbox()
        time.sleep(0.5)   # let the OS clean up the old process

        proc = subprocess.Popen(
            [
                config.DOSBOX_BIN,
                "-conf", config.DOSBOX_MAIN_CONF,
                "-conf", config.DOSBOX_TRAIN_CONF,
            ],
            cwd=config.DOSBOX_GAME_DIR,
            # Detach stdout/stderr so DOSBox console noise doesn't flood the terminal
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        if self.verbose:
            print(f"[DSJ2Env] DOSBox spawned (PID={proc.pid}). "
                  f"Waiting up to {config.DOSBOX_LOAD_TIMEOUT_S}s for game to load...")
        return proc

    def _wait_for_game(self, pid: int) -> int:
        """
        Poll until DSJ2's wind string is present in RAM, confirming the correct
        DOS RAM base address.  The wind string only appears once the game has
        loaded a hill, so this doubles as the "navigate to hill" gate.

        Prints navigation instructions on the first iteration so the user knows
        to switch to the DOSBox window and select a hill while we wait.

        Returns the confirmed base address.
        Raises RuntimeError if the game does not load within DOSBOX_LOAD_TIMEOUT_S.
        """
        deadline = time.time() + config.DOSBOX_LOAD_TIMEOUT_S
        first = True
        while time.time() < deadline:
            base, ws = find_base_auto(pid)
            if base is not None and ws is not None and "readable" not in str(ws):
                if self.verbose:
                    print(f"\n[DSJ2Env] Game detected — wind string confirmed: \"{ws}\"")
                return base

            if first and self.verbose:
                print()
                print("=" * 60)
                print("  DOSBox is open. In the game window:")
                print("    1. Select your country / jumper")
                print("    2. Select a hill")
                print("    3. Stand at the top of the ramp")
                print(f"  Waiting up to {config.DOSBOX_LOAD_TIMEOUT_S}s for wind")
                print("  string to appear (confirms correct hill is loaded)...")
                print("=" * 60)
                first = False

            time.sleep(0.5)

        # Last-ditch: accept a merely-readable base so we at least connect
        base, ws = find_base_auto(pid)
        if base is not None:
            if self.verbose:
                print("\n[DSJ2Env] WARNING: wind string not confirmed after "
                      f"{config.DOSBOX_LOAD_TIMEOUT_S}s. "
                      "Telemetry may read garbage until you navigate to a hill.")
            return base

        raise RuntimeError(
            f"DSJ2 did not load within {config.DOSBOX_LOAD_TIMEOUT_S}s. "
            "Check that DOSBox can find the game files."
        )

    def _spawn_and_connect(self) -> None:
        """Spawn DOSBox as a child, wait for the game to load, connect telemetry."""
        self._dosbox_proc = self._spawn_dosbox()
        pid = self._dosbox_proc.pid
        base = self._wait_for_game(pid)
        self.telemetry = DSJ2MemoryDirect(pid, base)
        if self.verbose:
            print(f"[DSJ2Env] Telemetry connected: PID={pid}, BASE=0x{base:016x}")

    # ── Observation helpers ───────────────────────────────────────────────────

    @staticmethod
    def _zero_state() -> Dict[str, float]:
        return {k: 0.0 for k in (
            "x_vel", "y_vel", "speed", "y_pos", "x_pos",
            "tilt", "wind_speed", "wind_dir",
        )}

    def _normalise(self, key: str, value: float) -> float:
        """Map a raw value to [-1, 1] using bounds from config.OBS_BOUNDS."""
        lo, hi = config.OBS_BOUNDS[key]
        if hi == lo:
            return 0.0
        normalised = 2.0 * (value - lo) / (hi - lo) - 1.0
        clipped = float(np.clip(normalised, -1.0, 1.0))
        if clipped != normalised:
            self._obs_clip_count += 1
        return clipped

    def _build_obs(self, state: Dict[str, float]) -> np.ndarray:
        """Build the 9-element normalised observation array."""
        self._last_raw_state = state
        return np.array([
            self._normalise("x_vel",      state["x_vel"]),
            self._normalise("y_vel",      state["y_vel"]),
            self._normalise("speed",      state["speed"]),
            self._normalise("y_pos",      state["y_pos"]),
            self._normalise("x_pos",      state["x_pos"]),
            self._normalise("tilt",       state["tilt"]),
            self._normalise("wind_speed", state["wind_speed"]),
            self._normalise("wind_dir",   state["wind_dir"]),
            self._normalise("phase",      float(self.phase)),
        ], dtype=np.float32)

    # ── Phase FSM ─────────────────────────────────────────────────────────────

    def _update_phase(self, state: Dict[str, float]) -> None:
        """
        Advance the phase finite state machine based on the current physics state.
        Transitions are one-way and permanent within a single episode.
        """
        speed = state["speed"]
        y_pos = state["y_pos"]
        y_vel = state["y_vel"]

        if self.phase == config.PHASE_WAITING:
            # Any non-trivial speed means the skier has started rolling
            if speed > config.SPEED_ON_RAMP_MIN:
                self.phase = config.PHASE_ON_RAMP
                self._log("  Phase → ON_RAMP")

        elif self.phase == config.PHASE_ON_RAMP:
            # Takeoff detected: y_pos rises for FLIGHT_RISING_STREAK consecutive frames
            y_delta = y_pos - self.prev_y_pos
            if y_delta > config.Y_POS_RISE_THRESHOLD and speed > config.SPEED_FLIGHT_MIN:
                self.rising_streak += 1
            else:
                self.rising_streak = 0

            if self.rising_streak >= config.FLIGHT_RISING_STREAK:
                self.phase = config.PHASE_IN_FLIGHT
                self.rising_streak = 0
                self.flight_frames = 0
                self._log(f"  Phase → IN_FLIGHT  (x={state['x_pos']:.1f} m)")

        elif self.phase == config.PHASE_IN_FLIGHT:
            self.flight_frames += 1
            y_vel_delta = y_vel - self.prev_y_vel

            if self.flight_frames > config.LANDING_GRACE_FRAMES:
                # Primary trigger: sharp y-velocity change on snow impact
                y_vel_triggered = abs(y_vel_delta) > config.LANDING_Y_VEL_DELTA
                # Fallback trigger: physics loop went idle (speed left valid range)
                # Catches smooth landings and cases where the skier stops without
                # a sharp y_vel spike (e.g. fall, very flat hill, or results screen
                # cleared the struct before the next poll).
                phys_went_idle  = not self.telemetry.is_jump_active()

                if y_vel_triggered or phys_went_idle:
                    trigger = "y_vel" if y_vel_triggered else "physics_idle"
                    self.phase = config.PHASE_LANDING
                    self.landing_step_count = 0
                    self.landing_start_time = time.time()
                    # Re-centre mouse: no pitch control after landing
                    self.controller.reset_position()
                    self._log(
                        f"  Phase → LANDING    (frame {self.flight_frames}, "
                        f"trigger={trigger}, Δy_vel={y_vel_delta:.2f})"
                    )

        # PHASE_LANDING is terminal — handled in step()

    # ── Action masking ────────────────────────────────────────────────────────

    def action_masks(self) -> np.ndarray:
        """
        Return a boolean mask of valid actions for the current phase.
        Required by MaskablePPO (sb3-contrib).
        """
        mask = np.zeros(config.N_ACTIONS, dtype=bool)
        for act in config.PHASE_ACTION_MASKS.get(self.phase, [config.ACT_NOTHING]):
            mask[act] = True
        return mask

    # ── Action execution ──────────────────────────────────────────────────────

    def _execute_action(self, action: int) -> None:
        """Dispatch action integer to the appropriate controller method."""
        if action == config.ACT_NOTHING:
            self.controller.do_nothing()
        elif action == config.ACT_LMB:
            self.controller.click_lmb()
        elif action == config.ACT_RMB:
            self.controller.click_rmb()
        elif action == config.ACT_MOUSE_UP:
            self.controller.mouse_up()
        elif action == config.ACT_MOUSE_DOWN:
            self.controller.mouse_down()

    # ── Reward ────────────────────────────────────────────────────────────────

    def _step_reward(self, state: Dict[str, float]) -> float:
        """Small shaped reward for horizontal progress during flight."""
        if self.phase == config.PHASE_IN_FLIGHT:
            delta_x = state["x_pos"] - self.prev_x_pos
            return max(0.0, delta_x) * config.REWARD_SHAPING_SCALE
        return 0.0

    def _terminal_reward(self) -> Tuple[float, Dict[str, Any]]:
        """
        Wait for results to be written to RAM, then compute terminal reward.

        Called once at the end of the LANDING phase.  If the landing was
        detected at time T, this function waits until T + RESULTS_WAIT_S
        before reading memory so the game has had time to write the scores.
        """
        # Honour the minimum results-wait from touchdown
        elapsed_since_landing = time.time() - self.landing_start_time
        remaining = config.RESULTS_WAIT_S - elapsed_since_landing
        if remaining > 0:
            time.sleep(remaining)

        distance, scores = self.telemetry.get_results()

        # Filter out garbage values (unwritten / residual memory noise)
        valid_scores = [s for s in scores if 0.0 < s < 25.0]

        info: Dict[str, Any] = {
            "distance":  distance,
            "scores":    scores,
        }

        if not valid_scores or distance <= 0.0:
            info["reason"] = "no_valid_results"
            return config.REWARD_CRASH, info

        med = median(valid_scores)
        info["median_score"] = med

        if med >= config.JURY_SCORE_THRESHOLD:
            reward = distance * config.REWARD_GOOD_LANDING
            info["reason"] = "good_landing"
        else:
            reward = (distance * config.REWARD_BAD_LANDING_MULT
                      + config.REWARD_BAD_LANDING_BIAS)
            info["reason"] = "bad_landing"

        self._log(
            f"  Result: {distance:.1f} m | "
            f"scores={[f'{s:.1f}' for s in scores]} | "
            f"median={med:.1f} | reward={reward:.1f} [{info['reason']}]"
        )
        return reward, info

    # ── Gymnasium interface ───────────────────────────────────────────────────

    def reset(
        self,
        *,
        seed: Optional[int] = None,
        options: Optional[dict] = None,
    ) -> Tuple[np.ndarray, dict]:
        super().reset(seed=seed)

        # Reset all episode-level bookkeeping
        self.phase = config.PHASE_WAITING
        self.step_count = 0
        self.landing_step_count = 0
        self.landing_start_time = 0.0
        self.flight_frames = 0
        self.rising_streak = 0
        self._prev_phase = config.PHASE_WAITING
        self._obs_clip_count = 0

        # Wait for the user to navigate to the ramp and confirm readiness.
        # This is the main gate between episodes: the agent takes no actions
        # until Enter is pressed, so the game can always be in the right state.
        print(f"\n  [Episode {self.step_count // 1 + 1}] "
              "Navigate to the ramp, then press Enter to start... ",
              end="", flush=True)
        input()

        # Focus the window once for the whole episode
        self.controller.focus_window()
        self.controller.reset_position()

        # Poll until physics go idle (speed → 0) confirming WAITING state
        deadline = time.time() + config.RESET_WAIT_MAX_S
        while time.time() < deadline:
            state = self.telemetry.get_state()
            if state["speed"] < config.WAITING_SPEED_MAX:
                break
            time.sleep(config.RESET_POLL_INTERVAL_S)
        else:
            if self.verbose:
                print("[DSJ2Env] WARNING: timed out waiting for WAITING state in reset()")

        state = self.telemetry.get_state()
        self.prev_x_pos = state["x_pos"]
        self.prev_y_pos = state["y_pos"]
        self.prev_y_vel = state["y_vel"]

        self._log(
            f"  Episode start: "
            f"wind={state['wind_speed']:.2f} m/s @ {state['wind_dir']:.0f}°"
        )

        return self._build_obs(state), {}

    def step(
        self, action: int
    ) -> Tuple[np.ndarray, float, bool, bool, Dict[str, Any]]:
        t_start = time.time()

        # Execute the chosen action
        self._execute_action(action)

        # Pad remaining step time so we run at ~20 Hz
        elapsed = time.time() - t_start
        remainder = config.STEP_DURATION_S - elapsed
        if remainder > 0:
            time.sleep(remainder)

        # Read new physics state
        state = self.telemetry.get_state()

        # Advance the phase FSM
        self._update_phase(state)

        # Shaped step reward (non-zero only during IN_FLIGHT)
        reward = self._step_reward(state)

        terminated = False
        truncated = False
        info: Dict[str, Any] = {
            "phase":          _PHASE_NAMES[self.phase],
            "step":           self.step_count,
            "obs_clip_count": self._obs_clip_count,
        }

        # ── Landing phase: give agent LANDING_PHASE_MAX_STEPS to click ───────
        if self.phase == config.PHASE_LANDING:
            self.landing_step_count += 1

            if self.landing_step_count >= config.LANDING_PHASE_MAX_STEPS:
                # End of episode: wait for results and compute terminal reward
                terminal_r, result_info = self._terminal_reward()
                reward += terminal_r
                info.update(result_info)
                terminated = True

        # ── Hard truncation ──────────────────────────────────────────────────
        elif self.step_count >= config.MAX_EPISODE_STEPS:
            reward += config.REWARD_CRASH
            info["reason"] = "timeout"
            truncated = True

        # Update rolling state for next step's FSM and shaped reward
        self.prev_x_pos = state["x_pos"]
        self.prev_y_pos = state["y_pos"]
        self.prev_y_vel = state["y_vel"]
        self.step_count += 1

        obs = self._build_obs(state)
        return obs, reward, terminated, truncated, info

    # ── Misc ──────────────────────────────────────────────────────────────────

    def _log(self, msg: str) -> None:
        if self.verbose:
            print(msg)

    def close(self) -> None:
        """Terminate the DOSBox child process when training is finished."""
        if self._dosbox_proc is not None and self._dosbox_proc.poll() is None:
            self._dosbox_proc.terminate()
            try:
                self._dosbox_proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self._dosbox_proc.kill()
            if self.verbose:
                print("[DSJ2Env] DOSBox process terminated.")

    def render(self) -> None:
        # The game renders itself in the DOSBox window; nothing to do here.
        pass


# ── Quick sanity test ─────────────────────────────────────────────────────────
if __name__ == "__main__":
    import sys

    print("=== DSJ2Env sanity check ===")
    print("Checking observation and action spaces...")

    env = DSJ2Env(verbose=True)

    print(f"  observation_space : {env.observation_space}")
    print(f"  action_space      : {env.action_space}")
    print(f"  action_masks()    : {env.action_masks()}  (WAITING phase)")

    print("\nReading one state without taking any actions...")
    state = env.telemetry.get_state()
    obs = env._build_obs(state)
    print(f"  raw state  : {state}")
    print(f"  normalised : {obs}")
    print(f"  obs range  : [{obs.min():.3f}, {obs.max():.3f}]")

    print("\nSanity check passed.")
    print("Run train.py to start training.")
