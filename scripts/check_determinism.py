import argparse
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "env"))
from shxgame import KEY_LEFT, KEY_RIGHT, ShxGame

LEVEL_NAMES = ["Hexagon", "Hexagoner", "Hexagonest", "Hyper Hexagon", "Hyper Hexagoner", "Hyper Hexagonest"]
STATE_FIELDS = [
    "stage", "player_angle", "sides", "death_timer", "run_time", "wall_speed", "hit_flag",
    "camera_distance", "rotation_mode", "hyper", "tutorial_active",
]
FAR = 10 ** 9


def snapshot(obs):
    walls = tuple((w.slot, w.distance, w.width, w.type) for w in obs.walls[:obs.wall_count])
    return tuple(getattr(obs, name) for name in STATE_FIELDS) + (walls,)


# steer towards the slot whose nearest wall is farthest away, with a seeded random move now and then
def greedy_keys(obs, rng, epsilon=0.1):
    if rng.random() < epsilon:
        return rng.choice((0, KEY_LEFT, KEY_RIGHT))
    sides = obs.sides
    if sides <= 0:
        return 0
    nearest = [FAR] * sides
    for w in obs.walls[:obs.wall_count]:
        if 0 <= w.slot < sides and w.width > 0:
            nearest[w.slot] = min(nearest[w.slot], w.distance)
    current = (obs.player_angle // (360 // sides)) % sides
    best = max(range(sides), key=lambda s: (nearest[s], -min((s - current) % sides, (current - s) % sides)))
    delta = (best - current) % sides
    if delta == 0:
        return 0
    # the angle grows with the left key, and slots follow the angle
    return KEY_LEFT if delta <= sides // 2 else KEY_RIGHT


def play_episode(game, level, seed, action_seed, max_ticks):
    rng = random.Random(action_seed)
    obs = game.reset(level=level, seed=seed)
    trace = [snapshot(obs)]
    while obs.death_timer == 0 and len(trace) <= max_ticks:
        obs = game.step(greedy_keys(obs, rng))
        trace.append(snapshot(obs))
    return trace


def first_difference(a, b):
    for i, (x, y) in enumerate(zip(a, b)):
        if x != y:
            names = STATE_FIELDS + ["walls"]
            fields = [names[k] for k in range(len(names)) if x[k] != y[k]]
            return f"tick {i}, fields {fields}"
    if len(a) != len(b):
        return f"lengths differ ({len(a)} vs {len(b)})"
    return None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--runs-dir", default=str(Path(__file__).resolve().parent.parent / "runs"))
    parser.add_argument("--seeds", type=int, nargs="+", default=[7, 1234])
    parser.add_argument("--max-seconds", type=float, default=120.0)
    parser.add_argument("--headless-other", action="store_true",
                        help="run the second process headless, so it also compares the two SDL builds")
    args = parser.parse_args()

    runs_dir = Path(args.runs_dir)
    max_ticks = int(args.max_seconds * 60)
    cases = [(level, seed) for level in range(len(LEVEL_NAMES)) for seed in args.seeds]
    failures = 0

    with ShxGame(runs_dir / "determinism-a", seed=1) as game_a, ShxGame(runs_dir / "determinism-c", seed=99, headless=args.headless_other) as game_c:
        # put the second process out of phase, so its virtual clock differs at every reset
        for _ in range(137):
            game_c.step(0)

        for level, seed in cases:
            action_seed = seed * 31 + level
            trace_a = play_episode(game_a, level, seed, action_seed, max_ticks)
            play_episode(game_a, (level + 1) % len(LEVEL_NAMES), seed + 1, 5, max_ticks)
            trace_b = play_episode(game_a, level, seed, action_seed, max_ticks)
            trace_c = play_episode(game_c, level, seed, action_seed, max_ticks)
            trace_other = play_episode(game_c, level, seed + 1000, action_seed, max_ticks)

            same_process = first_difference(trace_a, trace_b)
            other_process = first_difference(trace_a, trace_c)
            other_seed = first_difference(trace_a, trace_other)
            ok = same_process is None and other_process is None and other_seed is not None
            failures += not ok
            final = trace_a[-1]
            print(f"{LEVEL_NAMES[level]:17} seed {seed:5}: {len(trace_a) - 1:5} ticks "
                  f"({final[4] / 60:6.2f} s run time, died {final[3] > 0}), "
                  f"same process {same_process or 'identical'}, "
                  f"other process {other_process or 'identical'}, "
                  f"other seed {'differs at ' + other_seed if other_seed else 'IDENTICAL (bad)'}"
                  f"{'' if ok else '  <-- FAIL'}")

    print(f"{len(cases) - failures} of {len(cases)} cases passed")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
