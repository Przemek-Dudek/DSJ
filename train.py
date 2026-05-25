# train.py
# Trains the DSJ2 RL agent using MaskablePPO (sb3-contrib).
#
# Usage:
#   python train.py                     # fresh training run
#   python train.py --resume            # continue from latest checkpoint
#   python train.py --steps 1000000     # override total timesteps
#   python train.py --no-tensorboard    # skip TensorBoard logging
#
# Prerequisites:
#   pip install stable-baselines3 sb3-contrib gymnasium pynput
#   apt install wmctrl
#
# Keep the DOSBox window visible and focused when training starts.
# The agent will take control of the mouse for the duration of training.

import argparse
import os
import sys
import glob

import numpy as np
from stable_baselines3.common.callbacks import (
    BaseCallback,
    CheckpointCallback,
    EvalCallback,
)
from stable_baselines3.common.monitor import Monitor
from sb3_contrib import MaskablePPO
from sb3_contrib.common.maskable.evaluation import evaluate_policy

import config
from dsj2_env import DSJ2Env


# ── Callbacks ─────────────────────────────────────────────────────────────────

class EpisodeStatsCallback(BaseCallback):
    """
    Logs per-episode distance, median judge score and reward to TensorBoard
    and stdout after every completed episode.
    """

    def __init__(self, verbose: int = 0):
        super().__init__(verbose)
        self._ep_rewards: list = []
        self._ep_distances: list = []
        self._ep_scores: list = []
        self._ep_count: int = 0

    def _on_step(self) -> bool:
        # SB3 stores episode info in self.locals["infos"] for each env
        for info in self.locals.get("infos", []):
            if "episode" in info:
                ep_r = info["episode"]["r"]
                self._ep_rewards.append(ep_r)
                self._ep_count += 1

            # Harvest jump-specific metrics written by DSJ2Env.step()
            if "distance" in info:
                self._ep_distances.append(info["distance"])
            if "median_score" in info:
                self._ep_scores.append(info["median_score"])

        # Log a summary every 10 completed episodes
        if self._ep_count > 0 and self._ep_count % 10 == 0:
            mean_r = np.mean(self._ep_rewards[-10:])
            mean_d = np.mean(self._ep_distances[-10:]) if self._ep_distances else 0.0
            mean_s = np.mean(self._ep_scores[-10:]) if self._ep_scores else 0.0
            good = sum(1 for s in self._ep_scores[-10:] if s >= config.JURY_SCORE_THRESHOLD)

            self.logger.record("dsj2/mean_reward_10ep",   mean_r)
            self.logger.record("dsj2/mean_distance_10ep", mean_d)
            self.logger.record("dsj2/mean_jury_10ep",     mean_s)
            self.logger.record("dsj2/good_landings_10ep", good)

            print(
                f"  [ep {self._ep_count:5d}] "
                f"reward={mean_r:7.1f}  dist={mean_d:6.1f} m  "
                f"jury={mean_s:5.2f}  good_landings={good}/10"
            )

        return True


# ── Helper: find latest checkpoint ────────────────────────────────────────────

def _latest_checkpoint(checkpoint_dir: str) -> str | None:
    """Return the path to the most recently saved checkpoint, or None."""
    pattern = os.path.join(checkpoint_dir, "dsj2_ppo_*_steps.zip")
    files = glob.glob(pattern)
    if not files:
        return None
    # File names end in _<N>_steps.zip; pick the largest N
    files.sort(key=lambda p: int(p.split("_")[-2]))
    return files[-1]


# ── Main ──────────────────────────────────────────────────────────────────────

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Train DSJ2 RL agent with MaskablePPO")
    p.add_argument("--steps",          type=int,  default=500_000,
                   help="Total training timesteps (default: 500000)")
    p.add_argument("--resume",         action="store_true",
                   help="Resume from the latest checkpoint in CHECKPOINT_DIR")
    p.add_argument("--no-tensorboard", action="store_true",
                   help="Disable TensorBoard logging")
    p.add_argument("--verbose",        type=int,  default=1,
                   help="SB3 verbosity level (0=silent, 1=info, 2=debug)")
    return p.parse_args()


def main() -> None:
    args = parse_args()

    # ── Environment (spawns DOSBox as child process) ──────────────────────────
    print("Initialising DSJ2 environment...")
    print("  DOSBox will be launched automatically.")
    print("  Any existing DOSBox process will be terminated first.\n")
    env = Monitor(DSJ2Env(verbose=(args.verbose > 0)))

    # Give the user time to navigate the DSJ2 menu to the ramp
    print()
    print("=" * 60)
    print("  DOSBox is running.  In the game window:")
    print("    1. Select your country / jumper")
    print("    2. Select a hill")
    print("    3. Wait until the jumper is standing at the top of the ramp")
    print("  Then come back here and press Enter to start training.")
    print("=" * 60)
    input("\n  Press Enter when the jumper is ready at the ramp... ")
    print()

    # ── Directories ───────────────────────────────────────────────────────────
    os.makedirs(config.CHECKPOINT_DIR, exist_ok=True)
    os.makedirs(config.LOG_DIR,        exist_ok=True)
    if not args.no_tensorboard:
        os.makedirs(config.TENSORBOARD_LOG, exist_ok=True)

    tb_log = None if args.no_tensorboard else config.TENSORBOARD_LOG

    # ── Model ─────────────────────────────────────────────────────────────────
    policy_kwargs = dict(net_arch=config.PPO_NET_ARCH)

    if args.resume:
        ckpt = _latest_checkpoint(config.CHECKPOINT_DIR)
        if ckpt is None:
            print("[WARNING] --resume specified but no checkpoint found. Starting fresh.")
            args.resume = False
        else:
            print(f"Resuming from checkpoint: {ckpt}")

    if args.resume and ckpt:
        model = MaskablePPO.load(
            ckpt,
            env=env,
            tensorboard_log=tb_log,
            verbose=args.verbose,
        )
        # Restore learning rate (overwritten by load if it was an lr schedule)
        model.learning_rate = config.PPO_LEARNING_RATE
    else:
        model = MaskablePPO(
            policy="MlpPolicy",
            env=env,
            learning_rate=config.PPO_LEARNING_RATE,
            n_steps=config.PPO_N_STEPS,
            batch_size=config.PPO_BATCH_SIZE,
            n_epochs=config.PPO_N_EPOCHS,
            gamma=config.PPO_GAMMA,
            gae_lambda=config.PPO_GAE_LAMBDA,
            clip_range=config.PPO_CLIP_RANGE,
            ent_coef=config.PPO_ENT_COEF,
            policy_kwargs=policy_kwargs,
            tensorboard_log=tb_log,
            verbose=args.verbose,
        )
        print("New model created.")

    print(f"\nPolicy architecture : {config.PPO_NET_ARCH}")
    print(f"Learning rate       : {config.PPO_LEARNING_RATE}")
    print(f"Total timesteps     : {args.steps:,}")
    print(f"Checkpoint dir      : {config.CHECKPOINT_DIR}")
    print(f"TensorBoard log     : {tb_log or '(disabled)'}")
    print()

    # ── Callbacks ─────────────────────────────────────────────────────────────
    checkpoint_cb = CheckpointCallback(
        save_freq=max(1, config.PPO_N_STEPS),  # save after every PPO update
        save_path=config.CHECKPOINT_DIR,
        name_prefix="dsj2_ppo",
        verbose=1,
    )
    stats_cb = EpisodeStatsCallback(verbose=args.verbose)

    callbacks = [checkpoint_cb, stats_cb]

    # ── Training ──────────────────────────────────────────────────────────────
    print("=" * 60)
    print("Training started.  Keep the DOSBox window focused.")
    print("Press Ctrl+C to stop and save the model.")
    print("=" * 60)

    try:
        model.learn(
            total_timesteps=args.steps,
            callback=callbacks,
            reset_num_timesteps=not args.resume,
            tb_log_name="dsj2_ppo",
            progress_bar=True,
        )
    except KeyboardInterrupt:
        print("\n\nInterrupted by user.")

    # ── Save final model ──────────────────────────────────────────────────────
    model.save(config.MODEL_SAVE_PATH)
    print(f"\nFinal model saved to: {config.MODEL_SAVE_PATH}.zip")

    env.close()


if __name__ == "__main__":
    main()
