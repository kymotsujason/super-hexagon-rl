import argparse
import sys
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_DIR / "env"))
sys.path.insert(0, str(PROJECT_DIR / "scripts"))

import numpy as np
import torch

from shxenv import ACTION_KEYS
from shxgame import GameError
from shxsim import ShxSim
from train_ppo import Agent

LEVEL_NAMES = ["Hexagon", "Hexagoner", "Hexagonest", "Hyper Hexagon", "Hyper Hexagoner", "Hyper Hexagonest"]
FRAME_SKIP = 2


# Greedy, except the previous action is kept unless the best one beats it by more than margin
# in probability. A margin of 0 is plain greedy.
def choose(probs, previous, margin):
    best = int(np.argmax(probs))
    if probs[previous] >= probs[best] - margin:
        return previous
    return best


def jitter_stats(actions, seconds):
    a = np.array(actions)
    valid = (a[1:] >= 0) & (a[:-1] >= 0)
    cur, prv = a[1:][valid], a[:-1][valid]
    flips = np.sum(((cur == 1) & (prv == 2)) | ((cur == 2) & (prv == 1)))
    presses = [x for x in a if x != 0]
    wiggles = sum(1 for i in range(2, len(presses))
                  if presses[i] > 0 and presses[i - 1] > 0 and presses[i - 2] > 0
                  and presses[i] == presses[i - 2] and presses[i] != presses[i - 1])
    presses = int(np.sum((cur > 0) & (cur != prv)))
    holds = []
    run = 1
    for i in range(1, len(a)):
        if a[i] == a[i - 1] and a[i] > 0:
            run += 1
        else:
            if a[i - 1] > 0:
                holds.append(run)
            run = 1
    # share of the time spent moving that comes from holds of 200 ms (6 decisions) or longer
    moving = sum(holds)
    long_share = sum(h for h in holds if h * FRAME_SKIP / 60 >= 0.2) / moving if moving else 0.0
    return {
        "presses": presses / seconds,
        "long_share": long_share,
        "changes": np.sum(cur != prv) / seconds,
        "flips": flips / seconds,
        "wiggles": wiggles / seconds,
        "hold_ms": np.mean(holds) * FRAME_SKIP / 60 * 1000 if holds else 0.0,
        "median_hold": np.median(holds) if holds else 0.0,
    }


def main():
    parser = argparse.ArgumentParser(description="survival and jitter of a policy in the simulator")
    parser.add_argument("model")
    parser.add_argument("--sticky", type=float, default=0.0, help="probability margin to keep the previous action")
    parser.add_argument("--episodes", type=int, default=5)
    parser.add_argument("--max-seconds", type=float, default=300.0)
    parser.add_argument("--seed", type=int, default=31000)
    args = parser.parse_args()

    torch.set_num_threads(1)
    checkpoint = torch.load(args.model, map_location="cpu", weights_only=True)
    agent = Agent(checkpoint["obs_size"], 3, checkpoint["args"]["hidden"])
    agent.load_state_dict(checkpoint["model"])
    agent.eval()
    max_decisions = int(args.max_seconds * 60 / FRAME_SKIP)

    sim = ShxSim()
    actions = []
    survival = {level: [] for level in range(6)}
    for level in range(6):
        for episode in range(args.episodes):
            seed = args.seed + 100 * level + episode
            try:
                sim.reset(level=level, seed=seed)
            except GameError:
                # Hyper Hexagonest's ending blocks resets, and a fresh simulator is the way back
                sim.close()
                sim = ShxSim()
                sim.reset(level=level, seed=seed)
            previous = 0
            for _ in range(max_decisions):
                with torch.no_grad():
                    probs = torch.softmax(agent.logits(torch.from_numpy(sim.obs.copy()).unsqueeze(0))[0], 0).numpy()
                previous = choose(probs, previous, args.sticky)
                obs = sim.step(ACTION_KEYS[previous], frame_skip=FRAME_SKIP)
                actions.append(previous)
                if obs.death_timer > 0:
                    break
            survival[level].append(obs.run_time / 60)
            actions.append(-1)
    sim.close()

    decisions = sum(1 for a in actions if a >= 0)
    stats = jitter_stats(actions, decisions * FRAME_SKIP / 60)
    capped = sum(sum(s >= args.max_seconds - 0.1 for s in values) for values in survival.values())
    total = sum(len(values) for values in survival.values())
    means = " ".join(f"{np.mean(survival[level]):5.0f}" for level in range(6))
    print(f"sticky {args.sticky:.2f}: survival by level [{means}] s, {capped}/{total} reached {args.max_seconds:.0f} s | "
          f"presses {stats['presses']:.2f}/s, changes {stats['changes']:.1f}/s, flips {stats['flips']:.2f}/s, "
          f"wiggles {stats['wiggles']:.2f}/s, mean hold {stats['hold_ms']:.0f} ms, median hold {stats['median_hold']:.0f}, "
          f"moving in long holds {stats['long_share']:.0%}", flush=True)


if __name__ == "__main__":
    main()
