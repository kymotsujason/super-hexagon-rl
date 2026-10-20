import argparse
import sys
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_DIR / "env"))
sys.path.insert(0, str(PROJECT_DIR / "scripts"))

import numpy as np
import torch

from shxenv import ACTION_KEYS, build_observation
from shxsim import ShxSim
from train_ppo import Agent

# walls, the player's position in its slot, sides, and wall speed, which come from the game alone
GAME_VALUES = 35


def load(capture):
    sim = ShxSim(state=capture["state"].tobytes(), ranges=capture["ranges"], tick=int(capture["tick"]))
    sim.set_random(capture["rand_state"].tobytes(), int(capture["rand_front"]), int(capture["rand_rear"]))
    return sim


# Replays a live episode's actions in the simulator from the captured start, and returns the first
# decision whose observation differs from the live one, or None if they agree to the end.
def first_difference(capture):
    level, run_time, actions, observations = (int(capture["level"]), capture["run_time"], capture["action"],
                                              capture["obs"])
    sim = load(capture)
    try:
        if not np.array_equal(build_observation(sim.shared, level, 0, 0)[:GAME_VALUES], observations[0][:GAME_VALUES]):
            return 0
        for k in range(len(actions) - 1):
            ticks = int(round(run_time[k + 1] - run_time[k]))
            shared = sim.step(ACTION_KEYS[int(actions[k])], ticks)
            if not np.array_equal(build_observation(shared, level, 0, 0)[:GAME_VALUES], observations[k + 1][:GAME_VALUES]):
                return k + 1
        return None
    finally:
        sim.close()


# lets the policy play on its own in the simulator from the captured start
def policy_seconds(capture, agent, max_seconds):
    level = int(capture["level"])
    sim = load(capture)
    try:
        last, hold, start = 0, 0, sim.shared.run_time
        while sim.shared.death_timer == 0 and sim.shared.run_time - start < max_seconds * 60:
            obs = build_observation(sim.shared, level, last, hold)
            with torch.no_grad():
                action = int(agent.logits(torch.from_numpy(obs)).argmax())
            shared = sim.step(ACTION_KEYS[action], 2)
            hold = hold + shared.ticks_run if action == last else shared.ticks_run
            last = action
        return sim.shared.run_time / 60, sim.shared.death_timer > 0
    finally:
        sim.close()


def main():
    parser = argparse.ArgumentParser(description="replays live episodes saved by play_live.py --record in the simulator")
    parser.add_argument("captures", help="folder of .npz files from play_live.py --record")
    parser.add_argument("--model", default=str(PROJECT_DIR / "models" / "smooth-hold-ema.pt"))
    parser.add_argument("--max-seconds", type=float, default=15.0)
    args = parser.parse_args()
    torch.set_num_threads(1)

    checkpoint = torch.load(args.model, map_location="cpu", weights_only=True)
    agent = Agent(checkpoint["obs_size"], 3, checkpoint["args"]["hidden"])
    agent.load_state_dict(checkpoint["model"])
    agent.eval()

    paths = sorted(Path(args.captures).glob("*.npz"))
    if not paths:
        sys.exit(f"no .npz files in {args.captures}")
    for path in paths:
        capture = np.load(path)
        if "state" not in capture:
            print(f"{path.name}: no captured start, record it again with the current play_live.py")
            continue
        difference = first_difference(capture)
        seconds, died = policy_seconds(capture, agent, args.max_seconds)
        print(f"{path.name}: live {float(capture['seconds']):.2f} s, simulator matches the live game "
              f"{'to the end' if difference is None else f'until decision {difference}'}, the policy alone in the "
              f"simulator {'died at' if died else 'reached'} {seconds:.2f} s", flush=True)


if __name__ == "__main__":
    main()
