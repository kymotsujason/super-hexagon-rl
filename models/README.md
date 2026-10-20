# Models

PPO checkpoints for `Agent` in `scripts/train_ppo.py`, in the order they were trained. The
simulator column is runs that lasted the full 600 s on the seed sets starting at 12345, 50000, and
90000 (20 per level, 360 in all), measured together with `scripts/plot_figures.py --evaluate` and
saved in `docs/figures/models.json`. The real game column is the first seed set played in the
actual game, which gave the same count as the simulator each time.

| File | What changed | Real game | Simulator | Presses a second | Mean hold |
|---|---|---|---|---|---|
| `game-trained.pt` | 40 minutes in the real game | 109 of 120 | 325 of 360 | 8.46 | 76 ms |
| `sim-tuned.pt` | trained in the simulator | 120 of 120 | 357 of 360 | 6.11 | 81 ms |
| `no-flips.pt` | reversal cost | | 350 of 360 | 4.25 | 90 ms |
| `smooth-b2.pt` | press cost | | 343 of 360 | 1.48 | 255 ms |
| `smooth-avg-b5b6.pt` | snapshot average | 113 of 120 | 342 of 360 | 1.39 | 271 ms |
| `smooth-avg-b7.pt` | snapshot average | 116 of 120 | 348 of 360 | 1.21 | 332 ms |
| `smooth-hold-ema.pt` | running average, hold input | 116 of 120 | 349 of 360 | 1.19 | 340 ms |

`smooth-hold-ema.pt` is the one to watch, and `sim-tuned.pt` survives most. On three fresh seed
sets (130000, 170000, and 210000) `smooth-hold-ema.pt` reached 600 s in 351 of 360 against 337 for
`smooth-avg-b7.pt`. It reads a 45th observation value, how long the last key has been held, and the
older checkpoints read only the first 44. `docs/training.md` has the full history.
