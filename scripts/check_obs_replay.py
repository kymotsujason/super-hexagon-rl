import argparse
import random
import sys
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_DIR / "env"))
sys.path.insert(0, str(PROJECT_DIR / "scripts"))

import numpy as np

from check_determinism import greedy_keys
from shxenv import ACTION_KEYS, build_observation
from shxgame import ShxGame

FRAME_SKIP = 2


def key_to_action(keys):
    return ACTION_KEYS.index(keys)


# plays one episode, checking the C observation against the Python one at every decision
def play(game, level, seed, rng, max_decisions):
    obs = game.reset(level=level, seed=seed)
    actions = []
    observations = [game.obs.copy()]
    mismatches = 0
    last_action = 0
    hold_ticks = 0
    while obs.death_timer == 0 and len(actions) < max_decisions:
        expected = build_observation(obs, level, last_action, hold_ticks)
        if not np.array_equal(expected, game.obs):
            mismatches += 1
        action = key_to_action(greedy_keys(obs, rng, epsilon=0.2))
        obs = game.step(ACTION_KEYS[action], frame_skip=FRAME_SKIP)
        actions.append(action)
        hold_ticks = hold_ticks + obs.ticks_run if action == last_action else obs.ticks_run
        last_action = action
        observations.append(game.obs.copy())
    return actions, observations, mismatches


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--episodes-per-level", type=int, default=3)
    parser.add_argument("--max-seconds", type=float, default=90.0)
    args = parser.parse_args()
    max_decisions = int(args.max_seconds * 60 / FRAME_SKIP)

    failures = 0
    decisions = 0
    with ShxGame(PROJECT_DIR / "runs" / "check-obs", headless=True) as game:
        for level in range(6):
            for episode in range(args.episodes_per_level):
                seed = 1000 * level + episode
                rng = random.Random(seed)
                actions, observations, mismatches = play(game, level, seed, rng, max_decisions)
                decisions += len(actions)

                # replay to a point a bit before the end, then continue with the recorded actions
                cut = max(0, len(actions) - 1 - random.Random(seed).randrange(30, 120))
                game.replay(level, seed, actions[:cut], FRAME_SKIP)
                replay_ok = np.array_equal(game.obs, observations[cut])
                for i in range(cut, len(actions)):
                    game.step(ACTION_KEYS[actions[i]], frame_skip=FRAME_SKIP)
                    if not np.array_equal(game.obs, observations[i + 1]):
                        replay_ok = False
                        break

                ok = mismatches == 0 and replay_ok
                failures += not ok
                print(f"level {level} seed {seed:5}: {len(actions):5} decisions, observation mismatches "
                      f"{mismatches}, replay from {cut} {'matches' if replay_ok else 'DIFFERS'}"
                      f"{'' if ok else '  <-- FAIL'}", flush=True)

    print(f"{decisions} decisions checked, {failures} failing episodes")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
