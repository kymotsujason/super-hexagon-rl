import argparse
import random
import sys
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_DIR / "env"))
sys.path.insert(0, str(PROJECT_DIR / "scripts"))

import numpy as np
import torch

from check_determinism import STATE_FIELDS, greedy_keys, snapshot
from shxenv import ACTION_KEYS
from shxgame import GameError, ShxGame
from shxsim import ShxSim, save_initial_state

LEVEL_NAMES = ["Hexagon", "Hexagoner", "Hexagonest", "Hyper Hexagon", "Hyper Hexagoner", "Hyper Hexagonest"]
FRAME_SKIP = 2

# superhex sub objects, for naming where two dumps differ
REGIONS = [
    (0x0, "superhex"), (0x18, "gameclass"), (0x57f0, "scoreclass"), (0xbfc8, "graphicsclass"),
    (0x1e4a8, "musicclass"), (0x3bf30, "helpclass"), (0x3bfa8, "deviceclass"), (0x3c07c, "keys"),
    (0x3c27c, "superhex tail"),
]


def region_name(offset):
    name = REGIONS[0][1]
    for start, label in REGIONS:
        if offset >= start:
            name = label
    return name


def diff_ranges(a, b, ignore):
    x = np.frombuffer(a, dtype=np.uint8)
    y = np.frombuffer(b, dtype=np.uint8)
    differ = x != y
    differ[ignore] = False
    idx = np.nonzero(differ)[0]
    ranges = []
    for i in idx:
        if ranges and i <= ranges[-1][1] + 8:
            ranges[-1][1] = i
        else:
            ranges.append([i, i])
    return ranges


def describe(ranges, limit=12):
    parts = [f"0x{lo:x}-0x{hi:x} ({region_name(lo)})" for lo, hi in ranges[:limit]]
    more = f" and {len(ranges) - limit} more" if len(ranges) > limit else ""
    return ", ".join(parts) + more


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--seeds", type=int, nargs="+", default=[7, 1234])
    parser.add_argument("--max-seconds", type=float, default=120.0)
    parser.add_argument("--dump-every", type=int, default=50)
    parser.add_argument("--policy", default=None, help="checkpoint to choose actions with, for long episodes")
    parser.add_argument("--levels", type=int, nargs="+", default=list(range(6)))
    parser.add_argument("--ignore", default=str(PROJECT_DIR / "sim" / "dump_ignore.txt"),
                        help="byte ranges (hex start-end per line) known to hold pointers or drawing state")
    args = parser.parse_args()
    max_decisions = int(args.max_seconds * 60 / FRAME_SKIP)

    ignore = np.zeros(0x3C758, dtype=bool)
    ignore_path = Path(args.ignore)
    if ignore_path.is_file():
        for line in ignore_path.read_text().splitlines():
            line = line.split("#")[0].strip()
            if line:
                lo, hi = (int(v, 16) for v in line.split("-"))
                ignore[lo:hi + 1] = True

    agent = None
    if args.policy:
        from train_ppo import Agent
        torch.set_num_threads(1)
        checkpoint = torch.load(args.policy, map_location="cpu", weights_only=True)
        agent = Agent(checkpoint["obs_size"], 3, checkpoint["args"]["hidden"])
        agent.load_state_dict(checkpoint["model"])
        agent.eval()

    def choose(real, obs, rng):
        if agent is None:
            return greedy_keys(real, rng)
        with torch.no_grad():
            return ACTION_KEYS[int(agent.logits(torch.tensor(obs).unsqueeze(0)).argmax())]

    # a fresh game and a simulator built from its dump, with the replaced pointers ignored
    def start_pair():
        game = ShxGame(PROJECT_DIR / "runs" / "check-sim", headless=True)
        save_initial_state(game)
        sim = ShxSim()
        mask = ignore.copy()
        for offset in sim.pointer_offsets():
            mask[offset:offset + 8] = True
        ranges = diff_ranges(game.dump(), sim.dump(), mask)
        print(f"new pair: {len(sim.pointer_offsets())} pointers replaced, {len(ranges)} differing ranges", flush=True)
        return game, sim, mask

    failures = 0
    endings = 0
    game, sim, mask = start_pair()
    try:
        for level in args.levels:
            for seed in args.seeds:
                rng = random.Random(seed * 13 + level)
                try:
                    real = game.reset(level=level, seed=seed)
                except GameError:
                    # the Hyper Hexagonest ending blocks resets in the game, and has to in the simulator too
                    if game.shared.error != 6:
                        raise
                    try:
                        sim.reset(level=level, seed=seed)
                        print("the game is in its ending but the simulator reset anyway  <-- FAIL", flush=True)
                        failures += 1
                    except GameError:
                        endings += 1
                    game.close()
                    sim.close()
                    game, sim, mask = start_pair()
                    real = game.reset(level=level, seed=seed)
                simulated = sim.reset(level=level, seed=seed)
                decision = 0
                problem = None
                while True:
                    a, b = snapshot(real), snapshot(simulated)
                    if a != b:
                        names = STATE_FIELDS + ["walls"]
                        fields = [names[k] for k in range(len(names)) if a[k] != b[k]]
                        problem = f"state differs at decision {decision}: {fields}"
                    elif not np.array_equal(game.obs, sim.obs):
                        problem = f"observation differs at decision {decision}"
                    elif decision % args.dump_every == 0:
                        ranges = diff_ranges(game.dump(), sim.dump(), mask)
                        if ranges:
                            problem = f"memory differs at decision {decision}: {describe(ranges)}"
                    if problem or real.death_timer > 0 or decision >= max_decisions:
                        break
                    keys = choose(real, game.obs, rng)
                    real = game.step(keys, frame_skip=FRAME_SKIP)
                    simulated = sim.step(keys, frame_skip=FRAME_SKIP)
                    decision += 1
                failures += problem is not None
                print(f"{LEVEL_NAMES[level]:17} seed {seed:5}: {decision:5} decisions "
                      f"({real.run_time / 60:6.2f} s), {problem or 'matches'}", flush=True)
    finally:
        game.close()
        sim.close()
    print(f"{failures} cases differ, {endings} endings refused by both")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
