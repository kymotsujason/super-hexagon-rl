import argparse
import multiprocessing as mp
import os
import random
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "env"))
from shxgame import KEY_LEFT, KEY_RIGHT, ShxGame

PROJECT_DIR = Path(__file__).resolve().parent.parent


def process_cpu_seconds(pid):
    fields = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
    return (int(fields[11]) + int(fields[12])) / os.sysconf("SC_CLK_TCK")


def machine_busy_jiffies():
    values = [int(v) for v in Path("/proc/stat").read_text().split("\n", 1)[0].split()[1:]]
    idle = values[3] + values[4]  # idle, iowait
    return sum(values) - idle, sum(values)


def sdl_library(pid):
    for line in Path(f"/proc/{pid}/maps").read_text().splitlines():
        if "libSDL2" in line:
            return line.split()[-1]
    return "none"


def worker(index, seconds, frame_skip, headless, barrier, results):
    rng = random.Random(index)
    instance = PROJECT_DIR / "runs" / "parallel" / f"w{index:02d}"
    with ShxGame(instance, seed=index, headless=headless) as game:
        obs = game.reset(level=index % 3, seed=index)
        barrier.wait()
        steps = ticks = resets = 0
        game_cpu_start = process_cpu_seconds(game.process.pid)
        own_cpu_start = time.process_time()
        start = time.perf_counter()
        while time.perf_counter() - start < seconds:
            obs = game.step(rng.choice((0, KEY_LEFT, KEY_RIGHT)), frame_skip=frame_skip)
            steps += 1
            ticks += obs.ticks_run
            if obs.death_timer > 0:
                obs = game.reset(level=index % 3, seed=rng.randrange(1 << 30))
                resets += 1
        elapsed = time.perf_counter() - start
        results.put({
            "index": index,
            "steps": steps,
            "ticks": ticks,
            "resets": resets,
            "elapsed": elapsed,
            "game_cpu": process_cpu_seconds(game.process.pid) - game_cpu_start,
            "own_cpu": time.process_time() - own_cpu_start,
            "sdl": sdl_library(game.process.pid),
        })


def run(count, seconds, frame_skip, headless):
    ctx = mp.get_context("spawn")
    barrier = ctx.Barrier(count + 1)
    results = ctx.Queue()
    procs = [ctx.Process(target=worker, args=(i, seconds, frame_skip, headless, barrier, results))
             for i in range(count)]
    for p in procs:
        p.start()
    barrier.wait(timeout=120)
    busy_start, total_start = machine_busy_jiffies()
    rows = [results.get(timeout=seconds + 120) for _ in range(count)]
    busy_end, total_end = machine_busy_jiffies()
    for p in procs:
        p.join(timeout=30)

    steps = sum(r["steps"] for r in rows)
    ticks = sum(r["ticks"] for r in rows)
    elapsed = max(r["elapsed"] for r in rows)
    cpu = sum(r["game_cpu"] + r["own_cpu"] for r in rows)
    machine = 100 * (busy_end - busy_start) / max(1, total_end - total_start)
    libraries = sorted({r["sdl"] for r in rows})
    print(f"{count:3} instances: {steps / elapsed:10,.0f} steps/s {ticks / elapsed:10,.0f} ticks/s "
          f"({ticks / elapsed / count:8,.0f} per instance), env cpu {cpu / elapsed:5.1f} cores, "
          f"machine busy {machine:4.1f}%, sdl {libraries}", flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--counts", type=int, nargs="+", default=[1, 2, 4, 8, 12])
    parser.add_argument("--seconds", type=float, default=15.0)
    parser.add_argument("--frame-skip", type=int, default=1)
    parser.add_argument("--windowed", action="store_true")
    args = parser.parse_args()
    print(f"frame skip {args.frame_skip}, {'windowed' if args.windowed else 'headless'}, "
          f"{args.seconds:.0f} s per point, {os.cpu_count()} threads on this machine", flush=True)
    for count in args.counts:
        run(count, args.seconds, args.frame_skip, not args.windowed)


if __name__ == "__main__":
    main()
