import argparse
import os
import random
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "env"))
from shxgame import KEY_LEFT, KEY_RIGHT, KEY_SELECT, KEY_UP, ShxGame

STAGE_TITLE = -1
STAGE_LEVEL_SELECT = -2


def process_cpu_seconds(pid):
    fields = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
    ticks = int(fields[11]) + int(fields[12])  # utime, stime
    return ticks / os.sysconf("SC_CLK_TCK")


# menus need a press then a release, since a held key latches; play picks a random action
def choose_keys(obs, rng, press_toggle):
    if obs.stage in (STAGE_TITLE, STAGE_LEVEL_SELECT):
        return KEY_SELECT if press_toggle else 0
    if obs.stage >= 0 and obs.death_timer > 0:
        return KEY_UP
    if obs.stage >= 0:
        return rng.choice((0, KEY_LEFT, KEY_RIGHT))
    return 0


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--instance", default=str(Path(__file__).resolve().parent.parent / "runs" / "bench"))
    parser.add_argument("--seconds", type=float, default=20.0)
    parser.add_argument("--frame-skip", type=int, default=1)
    parser.add_argument("--seed", type=int, default=1)
    args = parser.parse_args()

    rng = random.Random(args.seed)
    with ShxGame(args.instance, seed=args.seed) as game:
        obs = game.shared
        print(f"lockstep ready at tick {obs.tick}, stage {obs.stage}")

        steps = 0
        ticks = 0
        play_ticks = 0
        deaths = 0
        run_times = []
        press_toggle = True
        seen_stages = set()

        game_cpu_start = process_cpu_seconds(game.process.pid)
        own_cpu_start = time.process_time()
        start = time.perf_counter()
        while time.perf_counter() - start < args.seconds:
            keys = choose_keys(obs, rng, press_toggle)
            press_toggle = not press_toggle
            alive_before = obs.stage >= 0 and obs.death_timer == 0
            run_time_before = obs.run_time
            obs = game.step(keys, frame_skip=args.frame_skip)
            steps += 1
            ticks += obs.ticks_run
            seen_stages.add(obs.stage)
            if alive_before:
                play_ticks += obs.ticks_run
                if obs.death_timer > 0:
                    deaths += 1
                    run_times.append(run_time_before / 60.0)
        elapsed = time.perf_counter() - start
        game_cpu = process_cpu_seconds(game.process.pid) - game_cpu_start
        own_cpu = time.process_time() - own_cpu_start

        print(f"frame skip {args.frame_skip}, {elapsed:.1f} s wall")
        print(f"steps {steps} ({steps / elapsed:,.0f}/s), ticks {ticks} ({ticks / elapsed:,.0f}/s)")
        print(f"ticks while alive {play_ticks}, deaths {deaths}, stages seen {sorted(seen_stages)}")
        if run_times:
            print(f"mean survival {sum(run_times) / len(run_times):.2f} s of game time over {len(run_times)} runs")
        print(f"game cpu {game_cpu:.1f} s ({100 * game_cpu / elapsed:.0f}% of one core), "
              f"client cpu {own_cpu:.1f} s ({100 * own_cpu / elapsed:.0f}% of one core)")


if __name__ == "__main__":
    main()
