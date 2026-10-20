# Overnight training log, 2026-10-19

Started at 04:40 PDT with a hard stop at 11:40 PDT. Bursts ran until about 10:00, then the
final real game evaluation, this write-up, and a git repo for the project.

The goal is smooth, held movement without giving up survival. The policy taps (median hold of
one 33 ms decision) where a person would hold the key down. Training runs in bursts of about 35
minutes, each starting from the best checkpoint so far, with the settings for the next burst
chosen from the last one's results.

Each burst is judged with `scripts/eval_style.py` in the simulator (greedy play, survival and
jitter), and the best result also gets the real game evaluation (20 seeded episodes per level,
600 s cap). The smoothness measures are presses per second (a press starts whenever a direction
key goes down), action changes per second, mean hold, and the share of movement time that comes
from holds of 200 ms or longer.

Two reward costs shape the movement. The reversal cost charges a straight flip between left and
right. The press cost charges every new press of a direction, so holding a key is free and
tapping isn't.

The starting points were `runs/train/sim-tuned` (reached 600 s in 120 of 120 real game episodes,
twitchy) and `runs/train/reversal-0.003` (113 of 120, no direct flips, still tapping).

## Batched evaluation matches the real game

`scripts/eval_batch.py` plays the real game evaluation's 120 seeded episodes (20 per level, seeds
12345 and up, 600 s cap) all at once in the simulator, with one network call per step. For
`runs/train/reversal-0.003` it gave 113 of 120 reaching the cap with per level counts [17 18 20
18 20 20], and its 7 deaths matched the real game evaluation's 7 deaths exactly, the same episodes
at the same times to the hundredth of a second (for example Hexagoner episode 9 at 28.87 s and
Hyper Hexagon episode 2 at 6.67 s). It took about a minute against 10 to 20 for the real game. So
the bursts below are judged on this, and the real game evaluation is kept for the final policy.

## Baselines

Measured in the simulator with `scripts/eval_style.py` (5 episodes per level, 300 s cap).

| Policy | Reached 300 s | Presses/s | Changes/s | Mean hold | Moving in long holds |
|---|---|---|---|---|---|
| sim-tuned | 30 of 30 | 5.74 | 9.2 | 85 ms | 40% |
| reversal-0.003 | 28 of 30 | 3.49 | 7.0 | 103 ms | 52% |

## Burst 1

All three arms start from `runs/train/reversal-0.003/model.pt` and train 35 minutes with 512
simulators on 4 threads, 128 step rollouts, 16 minibatches, learning rate 1e-4, and a 600 s level
target.

| Arm | Reversal cost | Press cost |
|---|---|---|
| A | 0.003 | 0 |
| B | 0.003 | 0.002 |
| C | 0.003 | 0.006 |

### Burst 1 results (finished 05:10)

Each arm took about 119 million steps in 35 minutes (7.2 cores together). The batched evaluation
gave these numbers.

| Arm | Reached 600 s | Presses/s | Changes/s | Mean hold | Moving in long holds |
|---|---|---|---|---|---|
| A | 118 of 120 | 4.24 | 8.5 | 90 ms | 41% |
| B | 68 of 120 | 1.59 | 3.2 | 229 ms | 82% |
| C | 95 of 120 | 1.41 | 2.8 | 262 ms | 88% |

The press cost works as intended. Holds got about 2.5 to 3 times longer, and most movement now
comes from long holds. However, survival dropped while the policy learns to play with holds. Arm
A shows survival recovering with more training (113 to 118 of 120 on the reversal cost alone).
B doing worse than C despite the smaller cost suggests the policies are mid adjustment instead of
settled.

## Burst 2

Both smooth arms continue for 40 minutes, plus a copy of C at three times the learning rate to see
whether faster adaptation brings survival back sooner.

| Arm | Starts from | Reversal cost | Press cost | Learning rate |
|---|---|---|---|---|
| B2 | b1-B | 0.003 | 0.002 | 1e-4 |
| C2 | b1-C | 0.003 | 0.006 | 1e-4 |
| C2f | b1-C | 0.003 | 0.006 | 3e-4 |

### Burst 2 results (finished 05:52)

| Arm | Reached 600 s | Presses/s | Changes/s | Mean hold | Moving in long holds |
|---|---|---|---|---|---|
| B2 | 112 of 120 | 1.48 | 3.0 | 255 ms | 88% |
| C2 | 94 of 120 | 1.25 | 2.5 | 308 ms | 92% |
| C2f | 57 of 120 | 1.19 | 2.4 | 333 ms | 93% |

B2 recovered from 68 to 112 of 120 and stayed smooth, so continued training does bring survival
back at a press cost of 0.002. C2 didn't move at 0.006, and the faster learning rate made it much
worse. The B line goes forward and C stops here.

## Burst 3

All three arms start from b2-B2 and train 40 minutes with a reversal cost of 0.003.

| Arm | Press cost | Learning rate |
|---|---|---|
| B3 | 0.002 | 1e-4 |
| B3s | 0.003 | 1e-4 |
| B3l | 0.002 | 5e-5 |

Sticky play on b2-B2 no longer pays off. Margins of 0.1 and 0.2 left holds at 259 and 263 ms
(against 255 ms without) and lowered survival to 110 and 105 of 120 (against 112), since the
press cost already does that job. Sticky play is out from here on.

### Burst 3 results (finished 06:35)

| Arm | Reached 600 s | Presses/s | Changes/s | Mean hold | Moving in long holds |
|---|---|---|---|---|---|
| B3 | 104 of 120 | 1.39 | 2.8 | 269 ms | 90% |
| B3s | 90 of 120 | 1.23 | 2.4 | 313 ms | 92% |
| B3l | 110 of 120 | 1.42 | 2.8 | 265 ms | 89% |

At a press cost of 0.002, survival levels off around 104 to 112 of 120, against 118 for the
reversal cost alone. One likely reason is the discount. At gamma 0.995 and 30 decisions per
second, a death 10 s away weighs about 0.22 of its full penalty, while each press is paid right
away, so the policy can trade some risk for fewer presses.

## Burst 4

| Arm | Starts from | Press cost | Gamma | Learning rate |
|---|---|---|---|---|
| B4g | b2-B2 | 0.002 | 0.998 | 5e-5 |
| B4r | b2-B2 | 0.001 | 0.995 | 5e-5 |
| B4l | b3-B3l | 0.002 | 0.995 | 5e-5 |

All three use a reversal cost of 0.003 and train 40 minutes. At gamma 0.998 a death 10 s away
weighs about 0.55 instead of 0.22.

### How noisy one seed set is

The same checkpoints on a second set of 120 seeds (50000 and up) gave b2-B2 117 (112 on the
first set), b3-B3l 108 (110), and b1-A 116 (118). One set of 120 episodes swings by about 5, which
is as large as most gaps between arms. From here on each candidate is judged on both sets
together, 240 episodes. On that measure b2-B2 reaches the cap in 229 of 240 against 234 for the
reversal cost alone (b1-A), while holding keys about three times as long (255 ms against 90 ms).

### Burst 4 results (finished 07:17)

Counts are over both seed sets, 240 episodes.

| Arm | Reached 600 s | Presses/s | Mean hold | Moving in long holds |
|---|---|---|---|---|
| b2-B2, for reference | 229 of 240 | 1.48 | 255 ms | 88% |
| B4g | 219 of 240 | 1.43 | 263 ms | 89% |
| B4r | 219 of 240 | 1.49 | 247 ms | 88% |
| B4l | 206 of 240 | 1.30 | 291 ms | 92% |

None beat b2-B2. The training logs show why the line drifts. Survival in training stays near 500
to 510 s (sampled play), but presses keep falling (1.53, then 1.44, then 1.33 per second across
B2, B3l, and B4l), and greedy survival slips with them. The press cost keeps pulling towards fewer
presses, so a run's last checkpoint is wherever that slide happened to stop.

## Burst 5

Two arms, since another job was using about 8 cores, with a checkpoint every 5 minutes so the
best one can be chosen afterwards instead of the last.

| Arm | Starts from | Press cost | Learning rate |
|---|---|---|---|
| B5 | b2-B2 | 0.0015 | 3e-5 |
| A5 | b1-A | 0.001 | 5e-5 |

### Burst 5 results (finished 08:00)

Every 5 minute snapshot was evaluated on the first seed set, and the best four on the second.

| Checkpoint | Set 1 | Set 2 | Both | Presses/s | Mean hold |
|---|---|---|---|---|---|
| b5-B5 35 min | 114 | 116 | 230 of 240 | 1.41 | 268 ms |
| b5-B5 40 min | 115 | 112 | 227 of 240 | 1.41 | 268 ms |
| b5-A5 30 min | 116 | 113 | 229 of 240 | 1.68 | 217 ms |
| b5-A5 40 min | 112 | 113 | 225 of 240 | 1.62 | 225 ms |

B5's snapshots on the first set went 108, 69, 112, 109, 108, 112, 114, 115 from 5 to 40 minutes,
so single checkpoints vary a lot, and the late ones were still climbing at the lower learning
rate. b5-B5 at 35 minutes matches b2-B2 on survival (230 against 229) while holding a little
longer. It was picked from 16 snapshots on these same seeds, so it gets a third seed set
before being trusted.

## Burst 6

| Arm | Starts from | Press cost | Learning rate |
|---|---|---|---|
| B6 | b5-B5 final | 0.0015 | 3e-5 |
| A6 | b5-A5 final | 0.0015 | 3e-5 |

Both keep the reversal cost of 0.003, train 40 minutes, and save snapshots every 5 minutes.

### Third seed set (90000 and up)

| Checkpoint | Set 3 | All three sets | Presses/s | Mean hold |
|---|---|---|---|---|
| b5-B5 35 min | 111 | 341 of 360 (94.7%) | 1.41 | 267 ms |
| b2-B2 | 114 | 343 of 360 (95.3%) | 1.48 | 254 ms |
| b1-A | 116 | 350 of 360 (97.2%) | 4.24 | 90 ms |
| sim-tuned | 120 | (not run on set 2) | 6.13 | 81 ms |

The b5-B5 lead didn't survive a fresh set, since it was picked from 16 snapshots on the first two.
b2-B2 and b5-B5 are effectively tied at about 95% reaching the cap, against 97% for the reversal
cost alone and 100% for the twitchy sim-tuned policy (on sets 1 and 3). Holding keys smoothly
costs about 5 in 100 runs so far, and most of the losses are on Hyper Hexagon.

### Burst 6 results and weight averaging (finished 08:56)

Burst 6's snapshots on the first seed set ranged from 102 to 116 with no clear trend. Averaging
the weights of a run's snapshots (`scripts/average_checkpoints.py`) was tried against the best
single snapshots, all on three seed sets (360 episodes).

| Policy | Reached 600 s | Presses/s |
|---|---|---|
| average of b5-B5 25 to 40 min and b6-B6 | 342 | 1.39 |
| b2-B2 | 343 | 1.48 |
| average of b6-A6 | 336 | 1.52 |
| b6-B6 25 min | 336 | 1.38 |
| average of b6-B6 10 to 40 min | 335 | 1.37 |
| b6-A6 40 min | 334 | 1.51 |

Averaging gives about the same as the best single checkpoints. The smooth policies have levelled
off near 95% at about 1.4 presses per second.

## Burst 7, the last one

The question is whether 95% is a limit of the reward or of the network's size, since the policy
now has to hold keys and dodge at the same time. `scripts/widen_checkpoint.py` copied the
averaged B5 and B6 policy into a 512 unit network whose new units start with zero outgoing weights,
so it plays the same as the original until training uses them (largest output difference 3.6e-6
after widening). Both arms run 70 minutes with a press cost of 0.0015, a reversal cost of 0.003,
a learning rate of 3e-5, and a snapshot every 10 minutes.

| Arm | Starts from | Hidden units |
|---|---|---|
| W7 | average of B5 and B6, widened | 512 |
| B7 | average of B5 and B6 | 256 |

### Burst 7 at 30 minutes

Counts are over the first two seed sets (240 episodes).

| Snapshot | B7 (256 units) | W7 (512 units) |
|---|---|---|
| 10 min | 212 | 217 |
| 20 min | 200 | 210 |
| 30 min | 216 | 216 |

Both arms let presses drift down to about 1.25 per second, and survival slid with them, the same
pattern as bursts 3 to 6. The wider network made no clear difference, so capacity doesn't look
like the limit. The press cost itself keeps trading survival for fewer presses as training goes
on, even at 0.0015 with a learning rate of 3e-5.

### Burst 7 at 70 minutes

| Snapshot | B7 (256 units) | W7 (512 units) |
|---|---|---|
| 40 min | 224 | 208 |
| 50 min | 220 | 217 |
| 60 min | 223 | 213 |
| 70 min | 224 | 219 |

The later snapshots recovered, with B7 settling at 220 to 224 at about 1.2 presses per second,
while the average of B5 and B6 had 230 on these two sets at 1.39. The wider network stayed behind
the narrow one at every snapshot from 40 minutes on, so it was dropped.

Since single B7 snapshots sat close together, their weights were averaged as well (B7's 40 to 70
minute snapshots). That average did better than any single snapshot on all three seed sets.

| Policy | Set 12345 | Set 50000 | Set 90000 | Total | Presses/s | Mean hold |
|---|---|---|---|---|---|---|
| average of B7 40 to 70 min | 116 | 116 | 116 | 348 | 1.21 | 332 ms |
| average of B5 and B6 | 113 | 117 | 112 | 342 | 1.39 | 271 ms |
| b2-B2 | 112 | 117 | 114 | 343 | 1.48 | 255 ms |

So averaging pays off once a run has levelled off, even though it was about neutral on the
swinging B6 snapshots. The B7 average survives more often while pressing 13% less and holding keys
about 24% longer, and 93% of its movement comes from holds of 200 ms or longer.

## Final smooth policy

`models/smooth-avg-b7.pt` is the weight average of b7-B7's 40, 50, 60, and 70 minute snapshots.
It's the pick, since it beat every earlier smooth policy on survival (348 of 360 across three seed
sets) while pressing the least (1.21 presses per second, 332 ms mean hold).

In the real game, with the standard 20 seeds per level and 600 s cap, it reached the cap in 116 of
120 episodes, with per level counts [19 19 20 19 19 20]. The four deaths (Hexagon episode 6 at
255.07 s, Hexagoner episode 4 at 155.27 s, Hyper Hexagon episode 14 at 153.53 s, and Hyper
Hexagoner episode 13 at 32.60 s) are the same episodes at the same times as in the batched
simulator evaluation. 119 of the 120 episodes passed 60 s.

The previous pick, `models/smooth-avg-b5b6.pt`, reached the cap in 113 of 120 real game episodes
with per level counts [18 18 20 18 19 20], also exactly what the simulator predicted. It stays in
`models/` for comparison.

`docs/movement-comparison.svg` (made by `scripts/plot_movement.py`) shows the same 15 seconds of
Hexagoner played by `sim-tuned` and `smooth-avg-b7`. The tuned policy pressed 71 times, and its
angle zigzags in small taps. The smooth policy pressed 11 times, and its angle moves in clean ramps
from held keys.

## Morning summary

The smooth policy went from a median hold of one 33 ms decision to a mean hold of 332 ms, with 93%
of its movement coming from holds of 200 ms or longer. It costs 4 of 120 real game episodes
against `sim-tuned`, which reached the cap in all 120 while pressing about 6 times a second.
`no-flips.pt` wasn't run in the real game, so its count is the batched simulator evaluation on
the same seeds, which matched the real game all three times it was checked.

| Model | Real game, reached 600 s | Presses/s | Mean hold |
|---|---|---|---|
| `sim-tuned.pt` | 120 of 120 | 5.7 to 6.1 | 81 to 85 ms |
| `no-flips.pt` | 118 of 120, simulator only | 4.24 | 90 ms |
| `smooth-avg-b5b6.pt` | 113 of 120 | 1.39 | 271 ms |
| `smooth-avg-b7.pt` | 116 of 120 | 1.21 | 332 ms |

What the night showed, in the order it was learned:

- Sticky actions at play time stopped helping once the policy was trained with a press cost.
- A press cost of 0.0015 to 0.002 with a reversal cost of 0.003 gets presses down to about 1.4 a
  second for about 5% of survival.
- Longer training with the press cost keeps lowering presses and survival together, so a single
  long run isn't the answer.
- A 512 unit network did no better than the 256 unit one, so capacity isn't the limit.
- Averaging the weights of a run's later snapshots, once survival has levelled off, gave the best
  policy of the night.
- The batched simulator evaluation matched the real game episode for episode all three times it
  was checked (the reversal cost run and both smooth picks).

Watching the smooth policy play Hexagoner for a minute in a window:

```bash
cd ~/Documents/super-hexagon && .venv/bin/python scripts/eval_policy.py models/smooth-avg-b7.pt --watch --levels 1 --episodes 1 --max-seconds 60
```

Ideas that weren't tried:

- Putting how long the current key has been held into the observation, so the policy can plan
  holds instead of discovering them through the cost.
- Annealing the press cost down once presses settle, to win back survival while keeping the
  holds.
- Averaging across arms that started from the same checkpoint, or keeping a running average of the
  weights during training.
- Evaluating on more seed sets, since a single set of 120 swings by about 5 episodes.
