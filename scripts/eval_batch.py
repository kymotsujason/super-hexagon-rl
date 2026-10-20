import argparse
import collections
import ctypes
import sys
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_DIR / "env"))
sys.path.insert(0, str(PROJECT_DIR / "scripts"))

import numpy as np
import torch

from eval_style import jitter_stats
from shxenv import ACTION_KEYS
from shxgame import OBS_SIZE, GameError
from shxsim import ShxSim, sim_lib
from train_ppo import Agent

LEVEL_NAMES = ["Hexagon", "Hexagoner", "Hexagonest", "Hyper Hexagon", "Hyper Hexagoner", "Hyper Hexagonest"]
FRAME_SKIP = 2
KEY_TABLE = np.array(ACTION_KEYS, dtype=np.int32)


def new_episode(level, seed):
    sim = ShxSim()
    try:
        sim.reset(level=level, seed=seed)
    except GameError:
        # a fresh simulator can't be in the Hyper Hexagonest ending, so this only guards odd states
        sim.close()
        sim = ShxSim()
        sim.reset(level=level, seed=seed)
    return sim


# Plays the same seeded episodes as eval_policy.py (seed + episode on each level), all at once in
# the simulator, so its numbers line up with the real game evaluation episode for episode.
def evaluate(model_path, episodes, max_seconds, seed, sticky, threads, device, delay=0, own_features=False,
             late_prob=0.0):
    checkpoint = torch.load(model_path, map_location=device, weights_only=True)
    agent = Agent(checkpoint["obs_size"], 3, checkpoint["args"]["hidden"]).to(device)
    agent.load_state_dict(checkpoint["model"])
    agent.eval()

    cases = [(level, seed + episode) for level in range(6) for episode in range(episodes)]
    sims = [new_episode(level, game_seed) for level, game_seed in cases]
    count = len(sims)
    handles = (ctypes.c_void_p * count)(*[sim.handle for sim in sims])
    obs = np.stack([sim.obs.copy() for sim in sims])
    keys = np.zeros(count, dtype=np.int32)
    death = np.zeros(count, dtype=np.float32)
    run_time = np.zeros(count, dtype=np.float32)
    stage = np.zeros(count, dtype=np.int32)
    previous = np.zeros(count, dtype=np.int64)
    active = np.ones(count, dtype=bool)
    survival = np.zeros(count)
    actions = [[] for _ in range(count)]
    max_decisions = int(max_seconds * 60 / FRAME_SKIP)
    # actions reach the game this many decisions after they're chosen, like a live player's lag
    pending = collections.deque(np.zeros(count, dtype=np.int64) for _ in range(delay))
    # a live player knows what it sent, so these features can follow its choices instead of the
    # keys the game has applied so far
    last_index = OBS_SIZE - 4
    own_hold = np.zeros(count, dtype=np.int64)

    # a live key that misses the game's next input read lands one tick into the decision
    late_rng = np.random.default_rng(seed)
    previous_applied = np.zeros(count, dtype=np.int64)

    def step(ticks):
        failed = sim_lib.shxSimBatchStep(count, handles, keys.ctypes.data, ticks, obs.ctypes.data,
                                         death.ctypes.data, run_time.ctypes.data, stage.ctypes.data, threads)
        if failed >= 0:
            raise GameError(f"simulator {failed} failed a step")

    def record_deaths():
        for i in np.nonzero(active & (death > 0))[0]:
            active[i] = False
            survival[i] = run_time[i] / 60

    previous_chosen = np.zeros(count, dtype=np.int64)
    for _ in range(max_decisions):
        with torch.no_grad():
            probs = torch.softmax(agent.logits(torch.from_numpy(obs).to(device)), dim=1).cpu().numpy()
        best = probs.argmax(axis=1)
        keep = probs[np.arange(count), previous] >= probs[np.arange(count), best] - sticky
        chosen = np.where(keep, previous, best)
        previous = chosen
        pending.append(chosen)
        applied = pending.popleft()
        if late_prob > 0:
            late = late_rng.random(count) < late_prob
            keys[:] = KEY_TABLE[np.where(late, previous_applied, applied)]
            step(1)
            record_deaths()
            keys[:] = KEY_TABLE[applied]
            step(FRAME_SKIP - 1)
        else:
            keys[:] = KEY_TABLE[applied]
            step(FRAME_SKIP)
        previous_applied = applied
        if own_features:
            own_hold = np.where(chosen == previous_chosen, own_hold + FRAME_SKIP, FRAME_SKIP)
            obs[:, last_index:last_index + 3] = 0.0
            obs[np.arange(count), last_index + chosen] = 1.0
            obs[:, last_index + 3] = np.minimum(own_hold / 60.0, 1.0)
        previous_chosen = chosen
        for i in np.nonzero(active)[0]:
            actions[i].append(int(applied[i]))
        record_deaths()
        if not active.any():
            break
    survival[active] = run_time[active] / 60
    for sim in sims:
        sim.close()

    flat = []
    for sequence in actions:
        flat.extend(sequence)
        flat.append(-1)
    decisions = sum(len(sequence) for sequence in actions)
    stats = jitter_stats(flat, decisions * FRAME_SKIP / 60)
    by_level = survival.reshape(6, episodes)
    capped = by_level >= max_seconds - 0.1
    return by_level, capped, stats


def main():
    parser = argparse.ArgumentParser(description="survival and jitter on fixed seeds, all episodes at once")
    parser.add_argument("models", nargs="+")
    parser.add_argument("--episodes", type=int, default=20)
    parser.add_argument("--max-seconds", type=float, default=600.0)
    parser.add_argument("--seed", type=int, default=12345)
    parser.add_argument("--sticky", type=float, default=0.0)
    parser.add_argument("--threads", type=int, default=4)
    parser.add_argument("--delay-decisions", type=int, default=0,
                        help="apply each action this many decisions (33 ms each) after it's chosen")
    parser.add_argument("--late-prob", type=float, default=0.0,
                        help="chance each decision's key lands one tick late, as when a live key misses a frame")
    parser.add_argument("--own-features", action="store_true",
                        help="fill the last action and hold values from the policy's choices, as a live player would")
    parser.add_argument("--device", default="cuda" if torch.cuda.is_available() else "cpu")
    parser.add_argument("--per-episode", action="store_true", help="print every episode's survival")
    args = parser.parse_args()
    torch.set_num_threads(1)

    for model in args.models:
        by_level, capped, stats = evaluate(model, args.episodes, args.max_seconds, args.seed, args.sticky,
                                           args.threads, args.device, args.delay_decisions, args.own_features,
                                           args.late_prob)
        counts = " ".join(f"{int(c.sum()):2}" for c in capped)
        print(f"{model} sticky {args.sticky:.2f} delay {args.delay_decisions} late {args.late_prob:g}: reached {args.max_seconds:.0f} s by level [{counts}] "
              f"total {int(capped.sum())}/{capped.size}, mean {by_level.mean():.0f} s | "
              f"presses {stats['presses']:.2f}/s, changes {stats['changes']:.1f}/s, flips {stats['flips']:.2f}/s, "
              f"wiggles {stats['wiggles']:.2f}/s, mean hold {stats['hold_ms']:.0f} ms, "
              f"moving in long holds {stats['long_share']:.0%}", flush=True)
        if args.per_episode:
            for level in range(6):
                values = " ".join(f"{v:.2f}" for v in by_level[level])
                print(f"  {LEVEL_NAMES[level]:17} {values}")


if __name__ == "__main__":
    main()
