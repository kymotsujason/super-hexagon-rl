# Training

This page covers how the models in `models/` were trained and what worked. The agent is a PPO policy
from `scripts/train_ppo.py` with a 256 unit MLP. It reads a 44 value egocentric observation
(`env/shxenv.py`), makes 30 decisions a second, and gets a reward of -1 on death with gamma 0.995.

## How the models are judged

Survival is measured with greedy play on 20 seeded episodes for each of the six levels, with a 600 s
cap, and a count such as "116 of 120" is the number that reached the cap. The standard seed set
starts at 12345, and two more start at 50000 and 90000, since one set of 120 swings by about 5
episodes, which is as large as most gaps between runs. Three fresh sets (130000, 170000, and 210000)
checked the final picks on seeds no policy was chosen on.

`scripts/eval_batch.py` plays a seed set's 120 episodes at once in the simulator in about a minute,
against 10 to 20 for the real game. Each time it was checked against the real game it matched
episode for episode, with the same deaths at the same times to the hundredth of a second, so most
comparisons below come from it. Smoothness is measured as presses per second (a press starts
whenever a direction key goes down), mean hold, and the share of movement from holds of 200 ms or
longer. The early smoothness runs used `scripts/eval_style.py` instead (5 episodes per level, 300 s
cap).

## First runs

The first run trained in the real game on Hexagon only for 20 minutes. It averaged 136.6 s on
Hexagon, while Hexagoner and Hexagonest, which weren't in training, averaged 24.3 s and 29.3 s. That
trainer used roughly 1% of what the games can produce. Building the observation in C inside the
hook, driving many games from one process (`env/shxvec.py`), and overlapping actor rollouts with the
GPU update raised throughput from about 6,000 agent steps a second to 122,045. Training also weights
levels towards the ones the agent survives least on, restarts 25% of deaths 1 to 3 s earlier, and
allows 600 s episodes.

Three runs on all six levels then compared training in the real game and in the simulator. The table
gives mean survival in the real game over 20 episodes per level with a 600 s cap.

| Policy | Hexagon | Hyper Hexagon | Other four levels |
|---|---|---|---|
| Real game, 40 min | 455.8 s | 518.2 s | 596.7 to 600.0 s |
| Simulator, 20 min | 57.1 s | 76.5 s | 585.2 to 600.0 s |
| Simulator tuned, 20 min | 600.0 s | 600.0 s | 600.0 s |

In the real game run (`models/game-trained.pt`) Hexagon, the easiest level, scored lowest, since the
level weighting aimed at 120 s and every level passed that early, so sampling went close to uniform.

The first simulator run reached about 422,000 steps a second, and 197 of 200 simulated Hexagon
deaths came in the square phase of stage 0. The real game policy averaged 463 s on Hexagon in the
simulator against 456 s in the real game, so the simulator plays square phases faithfully and the
gap came from this run's learning.

The tuned run (`models/sim-tuned.pt`) took one gradient step per 1,024 samples, the ratio of the
real game runs, instead of one per 8,192, and raised the level target from 120 s to 600 s so the
stage 0 levels got most of the sampling. It also started from the real game checkpoint at a learning
rate of 1e-4. In the real game all 120 episodes reached the cap, and on new seeds with a 30 minute
cap all 30 episodes reached 1,800 s, which is 15 hours of play without a death.

## Smoothing with press and reversal costs

The tuned policy changes action about 9 times a second, mostly as single 33 ms taps, and flips
straight between left and right about 2.4 times a second.

Sticky play keeps the previous action unless the best one beats it by a probability margin. A margin
of 0.2 cut action changes from 9.2 to 6.6 a second with all 30 episodes still reaching 300 s, while
0.5 dropped survival to 23 of 30. However, once a policy was trained with a press cost, sticky play
barely lengthened holds and cost survival (b2-B2 below fell from 112 to 110 and 105 of 120), so it
was dropped.

The reversal cost (`--reversal-cost`) charges every straight flip between left and right. At 0.003
it removed direct flips and about 92% of left, right, left wiggles, and reached the cap in 113 of
120 real game episodes against 120 for the tuned policy. However, it still tapped, at 3.49 presses a
second against 5.74.

### Overnight bursts

The press cost charges every new press of a direction, so holding a key is free and tapping isn't.
Seven bursts of 30 to 70 minutes tested it, each starting from the best checkpoint so far, with a
reversal cost of 0.003 throughout.

Burst 1 tried press costs of 0, 0.002, and 0.006 on the 0.003 reversal run. Holds got about 2.5 to 3
times longer. However, survival dropped to 68 and 95 of 120, while the arm without a press cost
recovered to 118. In burst 2 the 0.002 arm (b2-B2) came back to 112 at 1.48 presses a second and 255
ms holds. The 0.006 arm stayed at 94, and a copy at three times the learning rate fell to 57.

Survival then leveled off around 104 to 112 of 120 at a press cost of 0.002. One likely reason is
the discount. At gamma 0.995 a death 10 s away weighs about 0.22 of its full penalty while each
press is paid right away, so the policy can trade some risk for fewer presses. However, arms with
gamma 0.998 or a press cost of 0.001 each reached 219 of 240 (two seed sets) against 229 for b2-B2.
Presses kept falling across bursts (1.53, then 1.44, then 1.33 per second in training) and greedy
survival slipped with them, so a run's last checkpoint was wherever that slide happened to stop.

Bursts 5 and 6 mostly used a press cost of 0.0015 and a learning rate of 3e-5, and saved snapshots
every 5 minutes. Single snapshots swung a lot (108, 69, 112, 109, 108, 112, 114, 115 of 120 for one
run), and the best one, picked from 16 snapshots on two seed sets, fell back on a third. Averaging a
run's snapshot weights (`scripts/average_checkpoints.py`) gave about the same as the best single
ones.

Burst 7 copied `smooth-avg-b5b6.pt`, the bursts 5 and 6 average, into a 512 unit network
(`scripts/widen_checkpoint.py`) whose new units start with zero outgoing weights. Over 70 minutes it
stayed behind the 256 unit arm at every snapshot from 40 minutes on, so capacity doesn't look like
the limit. The 256 unit arm leveled off at 220 to 224 of 240, and the average of its 40 to 70 minute
snapshots (`smooth-avg-b7.pt`) beat every single snapshot on all three seed sets.

| Policy | Reached 600 s of 360 | Presses/s | Mean hold |
|---|---|---|---|
| `no-flips.pt`, reversal cost only | 350 | 4.24 | 90 ms |
| `smooth-b2.pt` | 343 | 1.48 | 255 ms |
| `smooth-avg-b5b6.pt` | 342 | 1.39 | 271 ms |
| `smooth-avg-b7.pt` | 348 | 1.21 | 332 ms |

In the real game `smooth-avg-b7.pt` reached the cap in 116 of 120 episodes, per level [19 19 20 19
19 20], with each death the same episode and time as the simulator predicted. `smooth-avg-b5b6.pt`
reached 113, also exactly as predicted. So smoothing cost 4 of 120 real game episodes against the
tuned policy, while the hold went from a median of one 33 ms decision to a mean of 332 ms.

## Follow-up with hold input and averaging

The follow-up started from `smooth-avg-b7.pt` and tested a hold time input, annealing the press cost
down, and a running average of the weights. The observation gained a 45th value, the seconds the
last action has been held, capped at 1 s. `--add-hold` gives a 44 input checkpoint that input with
zero weights, so it plays the same until training uses it. Every run now keeps an exponential moving
average (EMA) of the weights at 0.999 per update, and `--total-steps` drives the schedules by steps,
so a run slowed by a CPU quota gets the same training, only later.

Two rounds of four arms trained 70 minutes each, round 1 from `smooth-avg-b7.pt` and round 2 from
round 1's leaders. Another project had the RTX 3080 busy, so arms were split between it and a GTX
1660 and paired so each comparison shared a GPU, which is why step counts differ (about 274 against
458 million in round 1). Each arm's last raw checkpoint, EMA, and average of later snapshots ran on
the three seed sets (360 episodes). Every arm used a learning rate of 3e-5 except h2-HAlr at 1e-4.

| Arm | Hold input | Press cost | Raw | EMA | Snapshots |
|---|---|---|---|---|---|
| h1-H | yes | 0.0015 | 341 | 339 | 340 |
| h1-C | no | 0.0015 | 338 | 343 | 342 |
| h1-HA | yes | 0.0015 to 0.0008 | 342 | 348 | 339 |
| h1-A | no | 0.0015 to 0.0008 | 347 | 344 | 346 |
| h2-HAlow | yes | 0.0008 to 0.0003 | 351 | 352 | 355 |
| h2-Alow | no | 0.0008 to 0.0003 | 345 | 349 | 347 |
| h2-Azero | no | 0.0008 to 0 | 346 | 352 | 349 |
| h2-HAlr | yes | 0.0008 | 345 | 349 | 347 |

No round 1 candidate beat 348, and with about 96% of episodes reaching the cap the binomial spread
over 360 episodes is about 4 either way. The hold input went almost unused, since setting it to 0,
0.1, 0.3, or 1.0 s changed the greedy action in 0.1 to 0.4% of 9,000 decisions. Averaging weights
across the four arms gave 343 to 346, no better than the best single arm.

In round 2 a lower press cost bought survival with presses. Annealing to 0.0003 put h2-HAlow at 1.22
to 1.26 presses a second, and annealing to 0 let h2-Azero's raw policy climb to 1.48. Holding the
cost at 0.0008 with a learning rate of 1e-4 kept h2-HAlr at 1.17 to 1.19, and the EMA beat the raw
checkpoint in all four arms.

### Fresh seed sets and the control

Every candidate above, and `smooth-avg-b7.pt` too, was picked on the same three seed sets, which
flatters each pick. A control, h3-Alr, also repeated h2-HAlr's settings without the hold input,
starting from h1-A's raw checkpoint and stopping at h2-HAlr's 184.5 million steps.

| Policy | 12345 | 50000 | 90000 | 130000 | 170000 | 210000 | Total | Presses/s |
|---|---|---|---|---|---|---|---|---|
| h2-HAlr EMA | 116 | 115 | 118 | 118 | 117 | 116 | 700 | 1.19 |
| h3-Alr EMA | 119 | 115 | 116 | 117 | 112 | 113 | 692 | 1.18 |
| `smooth-avg-b7.pt` | 116 | 116 | 116 | 113 | 110 | 114 | 685 | 1.21 |

On the fresh sets `smooth-avg-b7.pt` fell from 348 to 337, while the round 2 policies stayed near
350. The control ended 8 behind over 720 episodes, about 1%, which is within the noise for two
policies that also started from different checkpoints. h2-HAlr's EMA still barely uses the input,
which changed the greedy action in 0.2 to 0.3% of 9,000 decisions. So the hold input didn't help,
and the gain came from the lower press cost, the higher learning rate, and using the EMA as the
policy.

## Final pick

`models/smooth-hold-ema.pt` is h2-HAlr's EMA. It reached the cap in 349 of 360 on the three usual
seed sets at 1.19 presses a second, with a 340 ms mean hold and 93% of movement from holds of 200 ms
or longer, and in 351 of 360 on the fresh sets.

In the real game, on the standard seeds, it reached the cap in 116 of 120 episodes, per level [20 20
20 18 18 20], with each death the same episode and time as the simulator predicted. That ties
`smooth-avg-b7.pt` on this one set, and its edge shows on the other five, where it reached the cap
in 584 of 600 against 569. `models/sim-tuned.pt` still survives most often, but it taps about 6
times a second. `docs/figures/movement-light.svg` (drawn by `scripts/plot_figures.py`) shows the two
playing the same 15 s of Hexagoner.

## What worked and what didn't

These are the things that worked:

- Training in the simulator worked once its update matched the real game runs' gradient steps per
  sample and the level target pushed sampling towards the weak levels.
- The batched simulator evaluation matched the real game episode for episode every time it was
  checked.
- A reversal cost of 0.003 removed direct flips at a cost of 7 of 120 real game episodes.
- A press cost turned taps into holds, and 0.0008 at a learning rate of 1e-4 kept presses at 1.19
  while survival rose.
- Averaging weights paid off once a run had leveled off, and a running EMA did about as well at no
  extra cost.
- Fresh seed sets showed that a pick looks a few episodes better on the seeds it was chosen on.

These are the things that didn't:

- Sticky play stopped helping once the policy was trained with a press cost.
- Long training at a fixed press cost kept lowering presses and survival together.
- A 512 unit network did no better than the 256 unit one.
- The hold time input went almost unused, and a control without it came within noise.
- Averaging weights across arms from the same checkpoint didn't beat the best arm.

Two ideas are still open. One is training a hold aware policy from `sim-tuned.pt`, so the input is
there while holds are learned, and the other is a press cost between 0.0008 and 0.0003 at a learning
rate of 1e-4.
