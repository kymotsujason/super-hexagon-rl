import argparse
import json
import sys
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_DIR / "env"))
sys.path.insert(0, str(PROJECT_DIR / "scripts"))

import numpy as np
import torch

FIGURES = PROJECT_DIR / "docs" / "figures"
DATA = FIGURES / "models.json"
SEEDS = (12345, 50000, 90000)
FRAME_SKIP = 2

# in training order, which the trade off chart traces as a path
MODELS = ["game-trained", "sim-tuned", "no-flips", "smooth-b2", "smooth-avg-b5b6", "smooth-avg-b7", "smooth-hold-ema"]
FOCUS = "smooth-hold-ema"

FONT = "-apple-system, BlinkMacSystemFont, 'SF Pro Text', system-ui, 'Segoe UI', Helvetica, Arial, sans-serif"

# one accent hue against gray, with the dark theme stepped for its own surface
THEMES = {
    "light": {"surface": "#fcfcfb", "border": "rgba(11,11,11,0.10)", "primary": "#0b0b0b", "secondary": "#52514e",
              "muted": "#898781", "grid": "#e1e0d9", "baseline": "#c3c2b7", "accent": "#2a78d6",
              "context": "#898781", "path": "#d9d8d1"},
    "dark": {"surface": "#1a1a19", "border": "rgba(255,255,255,0.10)", "primary": "#f0efec", "secondary": "#c3c2b7",
             "muted": "#898781", "grid": "#2c2c2a", "baseline": "#383835", "accent": "#3987e5",
             "context": "#6e6d68", "path": "#3a3a37"},
}


# survival and smoothness for every model on the three usual seed sets, played in the simulator
def evaluate(threads):
    from eval_batch import evaluate as evaluate_batch
    rows = []
    for name in MODELS:
        reached, presses, holds = 0, [], []
        for seed in SEEDS:
            _, capped, stats = evaluate_batch(str(PROJECT_DIR / "models" / f"{name}.pt"), 20, 600.0, seed, 0.0,
                                              threads, "cuda" if torch.cuda.is_available() else "cpu")
            reached += int(capped.sum())
            presses.append(stats["presses"])
            holds.append(stats["hold_ms"])
        rows.append({"name": name, "reached": reached, "of": 120 * len(SEEDS),
                     "presses": round(float(np.mean(presses)), 2), "hold_ms": round(float(np.mean(holds)))})
        print(f"{name}: {reached} of {120 * len(SEEDS)}, {rows[-1]['presses']} presses/s", flush=True)
    FIGURES.mkdir(parents=True, exist_ok=True)
    DATA.write_text(json.dumps(rows, indent=2) + "\n")


def play(name, level, seed, seconds):
    from shxenv import ACTION_KEYS
    from shxsim import ShxSim
    from train_ppo import Agent
    checkpoint = torch.load(PROJECT_DIR / "models" / f"{name}.pt", map_location="cpu", weights_only=True)
    agent = Agent(checkpoint["obs_size"], 3, checkpoint["args"]["hidden"])
    agent.load_state_dict(checkpoint["model"])
    agent.eval()
    sim = ShxSim()
    sim.reset(level=level, seed=seed)
    actions, angles = [], []
    for _ in range(int(seconds * 60 / FRAME_SKIP)):
        with torch.no_grad():
            action = int(agent.logits(torch.from_numpy(sim.obs.copy())).argmax())
        shared = sim.step(ACTION_KEYS[action], frame_skip=FRAME_SKIP)
        actions.append(action)
        angles.append(shared.player_angle)
        if shared.death_timer > 0:
            break
    sim.close()
    return actions, np.unwrap(np.radians(angles)) * 180 / np.pi


def text(x, y, content, size, fill, weight=400, anchor="start", extra=""):
    return (f'<text x="{x:.1f}" y="{y:.1f}" font-size="{size}" font-weight="{weight}" fill="{fill}" '
            f'text-anchor="{anchor}"{extra}>{content}</text>')


def card(t, width, height, title, subtitle):
    return [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" width="{width}" height="{height}" '
            f'font-family="{FONT}">',
            f'<rect x="0.5" y="0.5" width="{width - 1}" height="{height - 1}" rx="18" fill="{t["surface"]}" '
            f'stroke="{t["border"]}"/>',
            text(40, 54, title, 22, t["primary"], 600),
            text(40, 80, subtitle, 13.5, t["secondary"])]


def tradeoff_svg(t, rows):
    width, height = 960, 560
    left, right, top, bottom = 96, 920, 132, 470
    x_max, y_min, y_max = 9.0, 88.0, 100.0

    def px(presses):
        return left + presses / x_max * (right - left)

    def py(percent):
        return bottom - (percent - y_min) / (y_max - y_min) * (bottom - top)

    parts = card(t, width, height, "Smooth play without giving up survival",
                 "Seven models, each on 360 seeded runs in the simulator. The line follows the order they were trained in.")
    parts.append(text(left, top - 18, "Runs reaching the 600 s cap", 12.5, t["muted"]))
    for percent in (90, 95, 100):
        y = py(percent)
        parts.append(f'<line x1="{left}" y1="{y:.1f}" x2="{right}" y2="{y:.1f}" stroke="{t["grid"]}" stroke-width="1"/>')
        parts.append(text(left - 12, y + 4, f"{percent}%", 12, t["muted"], anchor="end",
                          extra=' style="font-variant-numeric: tabular-nums"'))
    parts.append(f'<line x1="{left}" y1="{bottom}" x2="{right}" y2="{bottom}" stroke="{t["baseline"]}" stroke-width="1"/>')
    for presses in (0, 2, 4, 6, 8):
        parts.append(text(px(presses), bottom + 22, str(presses), 12, t["muted"], anchor="middle",
                          extra=' style="font-variant-numeric: tabular-nums"'))
    parts.append(text((left + right) / 2, bottom + 50, "Key presses per second, fewer is smoother", 12.5, t["muted"],
                      anchor="middle"))

    by_name = {row["name"]: row for row in rows}
    points = {row["name"]: (px(row["presses"]), py(100 * row["reached"] / row["of"])) for row in rows}
    path = " ".join(f"{points[name][0]:.1f},{points[name][1]:.1f}" for name in MODELS if name in points)
    parts.append(f'<polyline points="{path}" fill="none" stroke="{t["path"]}" stroke-width="1.5" '
                 f'stroke-linejoin="round" stroke-linecap="round"/>')
    for name in MODELS:
        if name in points and name != FOCUS:
            x, y = points[name]
            parts.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="5" fill="{t["context"]}" stroke="{t["surface"]}" stroke-width="2"/>')
    x, y = points[FOCUS]
    parts.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="7.5" fill="{t["accent"]}" stroke="{t["surface"]}" stroke-width="2.5"/>')

    def context_label(name, dx, dy, anchor, label=None):
        x, y = points[name]
        parts.append(text(x + dx, y + dy, label or name, 12.5, t["secondary"], anchor=anchor))

    context_label("game-trained", -12, 4, "end")
    context_label("sim-tuned", 0, -14, "middle")
    context_label("no-flips", 0, -14, "middle")
    cluster = [points[name] for name in ("smooth-b2", "smooth-avg-b5b6")]
    cx, cy = max(p[0] for p in cluster), np.mean([p[1] for p in cluster])
    parts.append(text(cx + 14, cy + 4, "earlier smooth models", 12.5, t["secondary"]))
    focus = by_name[FOCUS]
    parts.append(text(x + 16, y - 26, FOCUS, 14, t["primary"], 600))
    parts.append(text(x + 16, y - 9, f"{focus['presses']:.2f} presses a second, {focus['reached']} of {focus['of']} runs",
                      12.5, t["secondary"]))
    # the model before it lands almost on the same spot, so say so instead of leaving a stray sliver
    parts.append(text(x + 16, y + 8, "smooth-avg-b7 sits just under it", 11.5, t["muted"]))
    parts.append("</svg>")
    return "\n".join(parts)


def segments(actions):
    runs, start = [], None
    for i, action in enumerate(list(actions) + [0]):
        if start is not None and action != actions[start]:
            runs.append((start, i, actions[start]))
            start = None
        if start is None and i < len(actions) and action > 0:
            start = i
    return runs


def movement_svg(t, plays, seconds):
    width, height = 960, 560
    left, right = 96, 920
    panel_top, panel_height = 118, 186
    decisions = int(seconds * 60 / FRAME_SKIP)
    scale = (right - left) / decisions
    spans = [float(np.ptp(angles)) for _, angles, _ in plays]
    degrees = max(spans + [1.0])

    parts = card(t, width, height, "Holding keys instead of tapping",
                 f"The same {seconds:.0f} s of Hexagoner, played by two models. The line is the player's angle, "
                 f"and the bars below it are held keys.")
    axis_y = panel_top + 2 * panel_height + 8
    for s in range(0, int(seconds) + 1, 5):
        x = left + s * 30 * scale
        parts.append(f'<line x1="{x:.1f}" y1="{panel_top + 34}" x2="{x:.1f}" y2="{axis_y - 10}" stroke="{t["grid"]}" stroke-width="1"/>')
        parts.append(text(x, axis_y + 8, f"{s} s", 12, t["muted"], anchor="middle",
                          extra=' style="font-variant-numeric: tabular-nums"'))

    for k, (name, angles, actions) in enumerate(plays):
        top = panel_top + k * panel_height
        color = t["accent"] if name == FOCUS else t["context"]
        presses = sum(1 for i, a in enumerate(actions) if a > 0 and (i == 0 or actions[i - 1] != a))
        parts.append(text(left, top + 20, name, 15, t["primary"], 600))
        parts.append(f'<text x="{right}" y="{top + 22}" text-anchor="end" fill="{t["primary"]}">'
                     f'<tspan font-size="28" font-weight="600">{presses}</tspan>'
                     f'<tspan font-size="13" fill="{t["secondary"]}" dx="6">presses</tspan></text>')
        trace_top, trace_height = top + 44, 78
        high = float(np.max(angles))
        middle = (high + float(np.min(angles))) / 2
        points = " ".join(
            f"{left + i * scale:.1f},{trace_top + trace_height / 2 + (middle - a) / degrees * trace_height:.1f}"
            for i, a in enumerate(angles))
        parts.append(f'<polyline points="{points}" fill="none" stroke="{color}" stroke-width="2" '
                     f'stroke-linejoin="round" stroke-linecap="round"/>')
        for lane, (label, action) in enumerate((("Left", 1), ("Right", 2))):
            y = top + 136 + lane * 18
            parts.append(text(left - 12, y + 7, label, 11.5, t["muted"], anchor="end"))
            for start, end, held in segments(actions):
                if held != action:
                    continue
                x = left + start * scale
                w = max((end - start) * scale - 2, 4)
                parts.append(f'<rect x="{x:.1f}" y="{y}" width="{w:.1f}" height="8" rx="{min(4.0, w / 2):.1f}" fill="{color}"/>')
    parts.append("</svg>")
    return "\n".join(parts)


def main():
    parser = argparse.ArgumentParser(description="draws the README figures in light and dark")
    parser.add_argument("--evaluate", action="store_true", help=f"measure every model again into {DATA.name} first")
    parser.add_argument("--threads", type=int, default=4)
    parser.add_argument("--level", type=int, default=1)
    parser.add_argument("--seed", type=int, default=12345)
    parser.add_argument("--seconds", type=float, default=15.0)
    args = parser.parse_args()
    torch.set_num_threads(1)

    if args.evaluate or not DATA.is_file():
        evaluate(args.threads)
    rows = json.loads(DATA.read_text())
    plays = []
    for name in ("sim-tuned", FOCUS):
        actions, angles = play(name, args.level, args.seed, args.seconds)
        plays.append((name, angles, actions))
    for theme, colors in THEMES.items():
        (FIGURES / f"tradeoff-{theme}.svg").write_text(tradeoff_svg(colors, rows))
        (FIGURES / f"movement-{theme}.svg").write_text(movement_svg(colors, plays, args.seconds))
    print(f"wrote the figures to {FIGURES}")


if __name__ == "__main__":
    main()
