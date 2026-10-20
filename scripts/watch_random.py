import argparse
import random
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "env"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from bench_step import choose_keys
from shxgame import ShxGame

TICK_SECONDS = 1.0 / 60.0


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--instance", default=str(Path(__file__).resolve().parent.parent / "runs" / "watch"))
    parser.add_argument("--seconds", type=float, default=15.0)
    parser.add_argument("--seed", type=int, default=1)
    args = parser.parse_args()

    rng = random.Random(args.seed)
    with ShxGame(args.instance, seed=args.seed) as game:
        obs = game.shared
        press_toggle = True
        start = time.perf_counter()
        next_frame = start
        while time.perf_counter() - start < args.seconds:
            keys = choose_keys(obs, rng, press_toggle)
            press_toggle = not press_toggle
            obs = game.step(keys, render=True)
            # pace to 60 steps per second so the run is watchable whether or not vsync blocks
            next_frame += TICK_SECONDS
            delay = next_frame - time.perf_counter()
            if delay > 0:
                time.sleep(delay)
        print(f"ended at tick {obs.tick}, stage {obs.stage}, walls on screen {obs.wall_count}")


if __name__ == "__main__":
    main()
