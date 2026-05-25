# config.py
# Central configuration for the DSJ2 RL agent.
# All tunable constants live here — edit this file to adjust behaviour
# without touching the environment or training code.

# ── DOSBox / process ──────────────────────────────────────────────────────────
DOSBOX_PROCESS_NAME = "dosbox"          # substring matched against /proc/*/cmdline
DOSBOX_WINDOW_TITLE = "DSJ.EXE"         # substring matched against wmctrl -l output (case-insensitive)
DOSBOX_DEFAULT_BASE = 0x7025ccbff010    # hint only; always rediscovered after spawn

# Paths used to spawn DOSBox as a child process (bypasses ptrace_scope restrictions)
DOSBOX_BIN       = "/usr/local/bin/dosbox"
DOSBOX_GAME_DIR  = "/home/pdudek/Games/DOS/DSJ21p"
DOSBOX_MAIN_CONF = "/home/pdudek/Games/DOS/DSJ21p/dosbox.conf"
DOSBOX_TRAIN_CONF= "/home/pdudek/Documents/DSJ_AI/training_override.conf"

# How long to wait for DSJ2 to finish loading after DOSBox is spawned (seconds).
# This includes time for the user to navigate the in-game menu to the hill.
# The wind string is only present once a hill screen is loaded, so give plenty of time.
DOSBOX_LOAD_TIMEOUT_S = 15

# ── Action IDs ────────────────────────────────────────────────────────────────
ACT_NOTHING    = 0
ACT_LMB        = 1
ACT_RMB        = 2
ACT_MOUSE_UP   = 3
ACT_MOUSE_DOWN = 4
N_ACTIONS      = 5

# ── Phase IDs ─────────────────────────────────────────────────────────────────
PHASE_WAITING   = 0   # physics idle; waiting for first click to start ramp roll
PHASE_ON_RAMP   = 1   # rolling down ramp; LMB+RMB to jump at the lip
PHASE_IN_FLIGHT = 2   # airborne; mouse up/down for pitch control
PHASE_LANDING   = 3   # touched down; LMB for style/distance bonus

# ── Phase → allowed actions (used by action_masks()) ─────────────────────────
PHASE_ACTION_MASKS = {
    PHASE_WAITING:   [ACT_NOTHING, ACT_LMB, ACT_RMB],
    PHASE_ON_RAMP:   [ACT_NOTHING, ACT_LMB, ACT_RMB],
    PHASE_IN_FLIGHT: [ACT_NOTHING, ACT_MOUSE_UP, ACT_MOUSE_DOWN],
    PHASE_LANDING:   [ACT_NOTHING, ACT_LMB],
}

# ── Physics thresholds ────────────────────────────────────────────────────────
SPEED_ON_RAMP_MIN       = 0.001   # speed above this → ramp phase has started
SPEED_FLIGHT_MIN        = 10.0    # minimum speed to count a y_pos rise as airborne
WAITING_SPEED_MAX       = 0.001   # speed below this → treat as WAITING (physics idle)

Y_POS_RISE_THRESHOLD    = 0.05    # y_pos delta per frame to count as "rising" (airborne)
FLIGHT_RISING_STREAK    = 2       # consecutive rising frames needed to confirm takeoff
LANDING_GRACE_FRAMES    = 20      # frames after takeoff to ignore y_vel changes (avoid false landing)
LANDING_Y_VEL_DELTA     = 2.0     # abs(y_vel change) that signals touchdown

# How many 50 ms steps the agent gets to click after touchdown (the style/distance window).
# At 20 Hz: 10 steps = 0.5 s window.  Increase if you want more landing interaction.
LANDING_PHASE_MAX_STEPS = 10

# ── Timing ────────────────────────────────────────────────────────────────────
STEP_DURATION_S         = 0.05    # 20 Hz; each env.step() call targets this wall-clock length
RESULTS_WAIT_S          = 4.0     # seconds after touchdown before results are written to RAM
CLICK_DURATION_S        = 0.05    # how long the mouse button is held per click
WINDOW_FOCUS_SLEEP_S    = 0.15    # pause after wmctrl focus call to let X11 settle

# ── Mouse control ─────────────────────────────────────────────────────────────
# Relative pixel distance moved per mouse_up / mouse_down action.
# Increase for more aggressive pitch changes; decrease for finer control.
MOUSE_DELTA_PX          = 5

# ── Episode limits ────────────────────────────────────────────────────────────
MAX_EPISODE_STEPS       = 500     # hard truncation at ~25 s (500 × 50 ms)

# ── Reset sequence ────────────────────────────────────────────────────────────
RESET_CLICK_DELAY_S     = 0.4     # gap between the two post-jump LMB clicks
RESET_WAIT_MAX_S        = 8.0     # max seconds to wait for WAITING state after reset
RESET_POLL_INTERVAL_S   = 0.1     # polling interval while waiting for WAITING state

# ── Reward ────────────────────────────────────────────────────────────────────
# Per-step shaped reward during IN_FLIGHT phase:
#   reward += max(0, delta_x_pos) * REWARD_SHAPING_SCALE
REWARD_SHAPING_SCALE    = 0.1

# Terminal reward on episode end:
#   median judge score >= JURY_SCORE_THRESHOLD → reward = distance * REWARD_GOOD_LANDING
#   median judge score <  JURY_SCORE_THRESHOLD → reward = distance * REWARD_BAD_LANDING_MULT
#                                                         + REWARD_BAD_LANDING_BIAS
#   crash / timeout                            → reward = REWARD_CRASH
JURY_SCORE_THRESHOLD    = 16.0
REWARD_GOOD_LANDING     = 1.0
REWARD_BAD_LANDING_MULT = 0.2
REWARD_BAD_LANDING_BIAS = -30.0
REWARD_CRASH            = -50.0

# ── Observation normalisation bounds ─────────────────────────────────────────
# Each raw value is mapped to [-1, 1] using: 2*(v - lo)/(hi - lo) - 1
# Adjust if you observe clipping (check info["obs_clip_count"] during training).
OBS_BOUNDS = {
    "x_vel":      (-50.0,   150.0),
    "y_vel":      (-60.0,    60.0),
    "speed":      (  0.0,   200.0),
    "y_pos":      (-500.0,  500.0),
    "x_pos":      (  0.0,   250.0),
    "tilt":       (-180.0,  180.0),
    "wind_speed": (  0.0,    15.0),
    "wind_dir":   (  0.0,   360.0),
    "phase":      (  0.0,     3.0),
}

# ── PPO hyperparameters ───────────────────────────────────────────────────────
PPO_LEARNING_RATE  = 3e-4
PPO_N_STEPS        = 2048    # steps collected per update (across all envs)
PPO_BATCH_SIZE     = 64
PPO_N_EPOCHS       = 10
PPO_GAMMA          = 0.99
PPO_GAE_LAMBDA     = 0.95
PPO_CLIP_RANGE     = 0.2
PPO_ENT_COEF       = 0.01    # entropy bonus encourages early exploration
PPO_NET_ARCH       = [256, 256]

# ── Paths ─────────────────────────────────────────────────────────────────────
LOG_DIR            = "./logs/"
CHECKPOINT_DIR     = "./checkpoints/"
MODEL_SAVE_PATH    = "./dsj2_ppo"
TENSORBOARD_LOG    = "./tb_logs/"
