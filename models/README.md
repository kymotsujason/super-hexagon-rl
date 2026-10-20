# Models

PPO checkpoints for `scripts/train_ppo.py`'s `Agent`. Survival counts come from the real game
evaluation's protocol (20 seeded episodes per level, 600 s cap), played in the simulator with
`scripts/eval_batch.py`, which matched the real game episode for episode when checked. Smoothness
comes from the same runs.

| File | What it is | Reached 600 s | Presses/s | Mean hold |
|---|---|---|---|---|
| `game-trained.pt` | 40 minutes in the real game, all levels | real game: 109 of 120 | not measured | not measured |
| `sim-tuned.pt` | simulator, tuned for the square phases | 240 of 240 on two sets | 5.7 to 6.1 | 81 to 85 ms |
| `no-flips.pt` | reversal cost 0.003 | 350 of 360 on three sets | 4.24 | 90 ms |
| `smooth-b2.pt` | reversal and press costs | 343 of 360 on three sets | 1.48 | 254 ms |
| `smooth-avg-b5b6.pt` | weight average of later smooth snapshots | 342 of 360 on three sets | 1.39 | 271 ms |
| `smooth-avg-b7.pt` | weight average of burst 7's last four snapshots | 348 of 360 on three sets | 1.21 | 332 ms |
| `smooth-hold-ema.pt` | running weight average, press cost 0.0008, hold input | 349 of 360 on three sets | 1.19 | 340 ms |

The seed sets start at 12345, 50000, and 90000. In the real game, on the first set,
`sim-tuned.pt` reached the cap in 120 of 120 episodes, `smooth-avg-b5b6.pt` in 113, and
`smooth-avg-b7.pt` and `smooth-hold-ema.pt` in 116 each, every count the same as the simulator's
for those seeds. On three fresh seed sets (130000, 170000, and 210000) `smooth-hold-ema.pt`
reached the cap in 351 of 360 against 337 for `smooth-avg-b7.pt`.

`smooth-hold-ema.pt` is the smooth policy to use, and `sim-tuned.pt` the one that survives most
often. It reads a 45th observation value (how long the last action has been held), and the older
checkpoints read only the first 44. `docs/overnight-log.md` and `docs/hold-anneal-log.md` have the
full history.
