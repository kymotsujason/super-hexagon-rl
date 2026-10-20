import argparse
import sys
import time
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_DIR / "env"))
sys.path.insert(0, str(PROJECT_DIR / "scripts"))

import numpy as np
import torch

from shxenv import ShxEnv
from train_ppo import Agent

LEVEL_NAMES = ["Hexagon", "Hexagoner", "Hexagonest", "Hyper Hexagon", "Hyper Hexagoner", "Hyper Hexagonest"]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("model")
    parser.add_argument("--levels", type=int, nargs="+", default=[0])
    parser.add_argument("--episodes", type=int, default=50)
    parser.add_argument("--max-seconds", type=float, default=300.0)
    parser.add_argument("--sample", action="store_true", help="sample actions instead of taking the most likely one")
    parser.add_argument("--watch", action="store_true", help="render in a window at normal game speed")
    parser.add_argument("--seed", type=int, default=12345)
    parser.add_argument("--sticky", type=float, default=0.0,
                        help="keep the previous action unless the best one beats it by this much probability")
    parser.add_argument("--instance", default=None, help="instance folder, defaults to runs/eval-policy")
    args = parser.parse_args()

    # one sample at a time doesn't benefit from threads, and several evaluations may share the CPU
    torch.set_num_threads(1)
    checkpoint = torch.load(args.model, map_location="cpu", weights_only=True)
    train_args = checkpoint["args"]
    agent = Agent(checkpoint["obs_size"], 3, train_args["hidden"])
    agent.load_state_dict(checkpoint["model"])
    agent.eval()
    frame_skip = train_args["frame_skip"]

    instance = args.instance or PROJECT_DIR / "runs" / ("watch-policy" if args.watch else "eval-policy")
    env = ShxEnv(instance,
                 levels=tuple(args.levels), frame_skip=frame_skip, headless=not args.watch,
                 seed=args.seed, max_episode_seconds=args.max_seconds, render=args.watch)
    print(f"model {args.model} (update {checkpoint['update']}, {checkpoint['global_step']:,} steps), "
          f"frame skip {frame_skip}, {'sampled' if args.sample else 'greedy'} actions", flush=True)
    try:
        for level in args.levels:
            results = []
            for episode in range(args.episodes):
                obs, _ = env.reset(options={"level": level, "game_seed": args.seed + episode})
                previous = 0
                step_time = frame_skip / 60.0
                next_frame = time.perf_counter()
                while True:
                    with torch.no_grad():
                        logits = agent.logits(torch.tensor(obs).unsqueeze(0))
                    if args.sample:
                        action = torch.distributions.Categorical(logits=logits).sample().item()
                    else:
                        action = int(logits.argmax().item())
                        probs = torch.softmax(logits[0], 0)
                        if probs[previous] >= probs[action] - args.sticky:
                            action = previous
                    previous = action
                    obs, _, dead, truncated, info = env.step(action)
                    if args.watch:
                        next_frame += step_time
                        delay = next_frame - time.perf_counter()
                        if delay > 0:
                            time.sleep(delay)
                    if dead or truncated:
                        results.append((info["run_seconds"], truncated))
                        print(f"  {LEVEL_NAMES[level]} episode {episode + 1}: {info['run_seconds']:.2f}s"
                              f"{' (hit the cap)' if truncated else ''}", flush=True)
                        break
            seconds = np.array([r[0] for r in results])
            cleared = np.mean(seconds >= 60.0) * 100
            capped = sum(r[1] for r in results)
            print(f"{LEVEL_NAMES[level]:17} {len(results)} episodes: mean {seconds.mean():6.2f}s "
                  f"median {np.median(seconds):6.2f}s min {seconds.min():6.2f}s max {seconds.max():6.2f}s, "
                  f"reached 60 s in {cleared:.0f}%, hit the {args.max_seconds:.0f} s cap {capped} times", flush=True)
    finally:
        env.close()


if __name__ == "__main__":
    main()
