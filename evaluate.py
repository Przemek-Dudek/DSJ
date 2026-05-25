# evaluate.py
# Loads a trained DSJ2 agent and runs evaluation episodes.
#
# Usage:
#   python evaluate.py                          # load dsj2_ppo.zip, run 10 episodes
#   python evaluate.py --model path/to/model    # specify model path (without .zip)
#   python evaluate.py --episodes 20            # run more episodes
#   python evaluate.py --deterministic false    # sample stochastically

import argparse
import os
import sys
from statistics import mean, median, stdev

import numpy as np
from sb3_contrib import MaskablePPO
from sb3_contrib.common.maskable.utils import get_action_masks

import config
from dsj2_env import DSJ2Env


# ── Argument parsing ──────────────────────────────────────────────────────────

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Evaluate a trained DSJ2 PPO agent")
    p.add_argument(
        "--model", type=str, default=config.MODEL_SAVE_PATH,
        help=f"Path to the saved model (without .zip, default: {config.MODEL_SAVE_PATH})",
    )
    p.add_argument(
        "--episodes", type=int, default=10,
        help="Number of evaluation episodes (default: 10)",
    )
    p.add_argument(
        "--deterministic", type=lambda v: v.lower() != "false", default=True,
        help="Use deterministic policy (default: true)",
    )
    p.add_argument(
        "--verbose", action="store_true",
        help="Show per-step phase and action info",
    )
    return p.parse_args()


# ── Episode runner ────────────────────────────────────────────────────────────

ACTION_NAMES = {
    config.ACT_NOTHING:    "nothing  ",
    config.ACT_LMB:        "LMB      ",
    config.ACT_RMB:        "RMB      ",
    config.ACT_MOUSE_UP:   "mouse_up ",
    config.ACT_MOUSE_DOWN: "mouse_dwn",
}

PHASE_NAMES = {
    config.PHASE_WAITING:   "WAITING  ",
    config.PHASE_ON_RAMP:   "ON_RAMP  ",
    config.PHASE_IN_FLIGHT: "IN_FLIGHT",
    config.PHASE_LANDING:   "LANDING  ",
}


def run_episode(
    env: DSJ2Env,
    model: MaskablePPO,
    deterministic: bool,
    verbose: bool,
) -> dict:
    """Run a single evaluation episode and return result metrics."""
    obs, _ = env.reset()
    total_reward = 0.0
    steps = 0

    while True:
        # Get valid action mask for the current phase
        masks = get_action_masks(env)

        action, _ = model.predict(obs, action_masks=masks, deterministic=deterministic)
        obs, reward, terminated, truncated, info = env.step(int(action))

        total_reward += reward
        steps += 1

        if verbose:
            phase_str  = PHASE_NAMES.get(env.phase, "?")
            action_str = ACTION_NAMES.get(int(action), str(action))
            print(
                f"    step {steps:4d} | phase={phase_str} | "
                f"action={action_str} | reward={reward:7.3f}"
            )

        if terminated or truncated:
            break

    return {
        "total_reward": total_reward,
        "steps":        steps,
        "distance":     info.get("distance",     0.0),
        "median_score": info.get("median_score", 0.0),
        "scores":       info.get("scores",       []),
        "reason":       info.get("reason",       "unknown"),
    }


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    args = parse_args()

    model_path = args.model
    if not model_path.endswith(".zip"):
        model_path_zip = model_path + ".zip"
    else:
        model_path_zip = model_path

    if not os.path.exists(model_path_zip):
        print(f"[ERROR] Model file not found: {model_path_zip}")
        sys.exit(1)

    # ── Load model and environment ────────────────────────────────────────────
    print(f"Loading model: {model_path_zip}")
    env = DSJ2Env(verbose=False)
    model = MaskablePPO.load(model_path, env=env)
    print(f"Model loaded.  Running {args.episodes} evaluation episode(s)...\n")

    # ── Run episodes ──────────────────────────────────────────────────────────
    results = []

    for ep in range(1, args.episodes + 1):
        print(f"Episode {ep}/{args.episodes}")
        result = run_episode(env, model, args.deterministic, args.verbose)
        results.append(result)

        good = "GOOD" if result["median_score"] >= config.JURY_SCORE_THRESHOLD else "BAD "
        scores_str = "  ".join(f"{s:.1f}" for s in result["scores"])
        print(
            f"  Distance : {result['distance']:6.1f} m\n"
            f"  Judges   : {scores_str}\n"
            f"  Median   : {result['median_score']:.1f}  [{good} landing]\n"
            f"  Reward   : {result['total_reward']:.1f}\n"
            f"  Steps    : {result['steps']}\n"
            f"  End cause: {result['reason']}\n"
        )

    env.close()

    # ── Summary statistics ────────────────────────────────────────────────────
    if len(results) < 2:
        return

    distances = [r["distance"] for r in results]
    medians   = [r["median_score"] for r in results]
    rewards   = [r["total_reward"] for r in results]
    good_cnt  = sum(1 for r in results if r["median_score"] >= config.JURY_SCORE_THRESHOLD)

    print("=" * 55)
    print(f"  Summary over {len(results)} episodes")
    print("=" * 55)
    print(f"  Distance   mean={mean(distances):6.1f}  "
          f"max={max(distances):6.1f}  "
          f"std={stdev(distances) if len(distances)>1 else 0:.1f}")
    print(f"  Jury med   mean={mean(medians):5.2f}  "
          f"max={max(medians):5.2f}")
    print(f"  Reward     mean={mean(rewards):7.1f}  "
          f"max={max(rewards):7.1f}")
    print(f"  Good landings : {good_cnt}/{len(results)} "
          f"({100*good_cnt/len(results):.0f}%)")
    print("=" * 55)


if __name__ == "__main__":
    main()
