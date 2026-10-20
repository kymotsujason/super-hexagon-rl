import argparse
import sys
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_DIR / "env"))
sys.path.insert(0, str(PROJECT_DIR / "scripts"))

import torch

from train_ppo import Agent


# Copies a checkpoint into a network with wider hidden layers. The new units get small random
# incoming weights and zero outgoing weights, so the wider network plays exactly like the old one
# until training gives the new units something to do.
def widen(old_state, obs_size, new_hidden):
    wide = Agent(obs_size, 3, new_hidden)
    new_state = wide.state_dict()
    for key, old in old_state.items():
        new = new_state[key].clone()
        if old.dim() == 1:
            new.zero_()
            new[:old.shape[0]] = old
        else:
            rows, cols = old.shape
            new[:rows, :cols] = old
            # past the first layer, inputs from the new hidden units start at zero
            if cols != obs_size:
                new[:, cols:] = 0.0
        new_state[key] = new
    wide.load_state_dict(new_state)
    return wide


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("checkpoint")
    parser.add_argument("output")
    parser.add_argument("--hidden", type=int, default=512)
    args = parser.parse_args()

    checkpoint = torch.load(args.checkpoint, map_location="cpu", weights_only=True)
    old_hidden = checkpoint["args"]["hidden"]
    obs_size = checkpoint["obs_size"]
    wide = widen(checkpoint["model"], obs_size, args.hidden)

    # the wider network has to give the same outputs as the original on any input
    narrow = Agent(obs_size, 3, old_hidden)
    narrow.load_state_dict(checkpoint["model"])
    x = torch.randn(4096, obs_size)
    with torch.no_grad():
        diff_actor = (narrow.actor(x) - wide.actor(x)).abs().max().item()
        diff_critic = (narrow.value(x) - wide.value(x)).abs().max().item()
    print(f"largest output difference after widening: actor {diff_actor:.2e}, critic {diff_critic:.2e}")
    if diff_actor > 1e-4 or diff_critic > 1e-4:
        raise SystemExit("widening changed the network's outputs")

    result = dict(checkpoint)
    result["model"] = wide.state_dict()
    result["args"] = dict(checkpoint["args"], hidden=args.hidden)
    result["widened_from"] = args.checkpoint
    torch.save(result, args.output)
    print(f"saved {args.output} with {args.hidden} hidden units")


if __name__ == "__main__":
    main()
