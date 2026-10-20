import argparse
from pathlib import Path

import torch


# Averages the weights of checkpoints from one continuous run, which tends to give a steadier
# policy than any single snapshot when they swing from one to the next.
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("output")
    parser.add_argument("checkpoints", nargs="+")
    args = parser.parse_args()

    loaded = [torch.load(path, map_location="cpu", weights_only=True) for path in args.checkpoints]
    averaged = {}
    for key in loaded[0]["model"]:
        averaged[key] = sum(c["model"][key].float() for c in loaded) / len(loaded)
    result = dict(loaded[-1])
    result["model"] = averaged
    result["averaged_from"] = [str(Path(p)) for p in args.checkpoints]
    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    torch.save(result, args.output)
    print(f"averaged {len(loaded)} checkpoints into {args.output}")


if __name__ == "__main__":
    main()
