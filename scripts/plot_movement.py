import argparse
import sys
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_DIR / "env"))
sys.path.insert(0, str(PROJECT_DIR / "scripts"))

import numpy as np
import torch

from shxenv import ACTION_KEYS
from shxsim import ShxSim
from train_ppo import Agent

FRAME_SKIP = 2
COLORS = {1: "#2f6fd6", 2: "#e07b1f"}
WIDTH, ROW, PAD = 960, 150, 70


def play(model_path, level, seed, seconds):
    checkpoint = torch.load(model_path, map_location="cpu", weights_only=True)
    agent = Agent(checkpoint["obs_size"], 3, checkpoint["args"]["hidden"])
    agent.load_state_dict(checkpoint["model"])
    agent.eval()
    sim = ShxSim()
    sim.reset(level=level, seed=seed)
    actions, angles = [], []
    for _ in range(int(seconds * 60 / FRAME_SKIP)):
        with torch.no_grad():
            action = int(agent.logits(torch.from_numpy(sim.obs.copy()).unsqueeze(0)).argmax())
        obs = sim.step(ACTION_KEYS[action], frame_skip=FRAME_SKIP)
        actions.append(action)
        angles.append(obs.player_angle)
        if obs.death_timer > 0:
            break
    sim.close()
    return actions, np.unwrap(np.radians(angles)) * 180 / np.pi


# one row per policy: key bars along the bottom, the player's angle as a line above them
def row_svg(label, actions, angles, top, seconds):
    scale = (WIDTH - 2 * PAD) / (seconds * 30)
    parts = [f'<text x="10" y="{top + 20}" font-size="14" fill="#222">{label}</text>']
    bar_y = top + ROW - 28
    for i, action in enumerate(actions):
        if action in COLORS:
            x = PAD + i * scale
            parts.append(f'<rect x="{x:.1f}" y="{bar_y}" width="{scale:.2f}" height="16" fill="{COLORS[action]}"/>')
    low, high = float(np.min(angles)), float(np.max(angles))
    span = max(high - low, 1.0)
    points = " ".join(f"{PAD + i * scale:.1f},{top + 30 + (high - a) / span * (ROW - 70):.1f}" for i, a in enumerate(angles))
    parts.append(f'<polyline points="{points}" fill="none" stroke="#333" stroke-width="1.5"/>')
    return parts


def main():
    parser = argparse.ArgumentParser(description="draw two policies' key presses and angle over the same stretch")
    parser.add_argument("first")
    parser.add_argument("second")
    parser.add_argument("output")
    parser.add_argument("--labels", nargs=2, default=["first", "second"])
    parser.add_argument("--level", type=int, default=1)
    parser.add_argument("--seed", type=int, default=12345)
    parser.add_argument("--seconds", type=float, default=15.0)
    args = parser.parse_args()
    torch.set_num_threads(1)

    rows = [play(path, args.level, args.seed, args.seconds) for path in (args.first, args.second)]
    height = 2 * ROW + 60
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{WIDTH}" height="{height}" font-family="sans-serif">',
             f'<rect width="{WIDTH}" height="{height}" fill="white"/>']
    for k, ((actions, angles), label) in enumerate(zip(rows, args.labels)):
        presses = sum(1 for i, a in enumerate(actions) if a > 0 and (i == 0 or actions[i - 1] != a))
        text = f"{label}: {presses} presses in {len(actions) * FRAME_SKIP / 60:.0f} s, line is the player's angle"
        parts += row_svg(text, actions, angles, 10 + k * ROW, args.seconds)
    legend_y = height - 22
    parts.append(f'<rect x="{PAD}" y="{legend_y}" width="14" height="14" fill="{COLORS[1]}"/>')
    parts.append(f'<text x="{PAD + 20}" y="{legend_y + 12}" font-size="13">left held</text>')
    parts.append(f'<rect x="{PAD + 110}" y="{legend_y}" width="14" height="14" fill="{COLORS[2]}"/>')
    parts.append(f'<text x="{PAD + 130}" y="{legend_y + 12}" font-size="13">right held</text>')
    parts.append(f'<text x="{PAD + 240}" y="{legend_y + 12}" font-size="13">{args.seconds:.0f} s across</text>')
    parts.append("</svg>")
    Path(args.output).write_text("\n".join(parts))
    print(f"wrote {args.output}")


if __name__ == "__main__":
    main()
