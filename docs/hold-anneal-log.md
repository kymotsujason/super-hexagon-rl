# Smoothing follow-up, 2026-10-19 afternoon

This session tried the three ideas left at the end of `docs/overnight-log.md`, starting from
`models/smooth-avg-b7.pt` (348 of 360 on the three seed sets, 1.21 presses per second, 332 ms mean
hold). The ideas were putting how long the current key has been held into the observation,
annealing the press cost down, and keeping a running average of the weights during training.

The result is `models/smooth-hold-ema.pt`. It reached the 600 s cap in 349 of 360 episodes on the
three usual seed sets at 1.19 presses per second, and in 351 of 360 on three fresh sets where
`smooth-avg-b7.pt` reached 337. In the real game it reached the cap in 116 of 120 episodes on the
first set, the same episodes at the same times as the simulator predicted. However, the hold input
isn't what made it better. A control without it reached 692 of 720 against 700 across all six
sets, within noise, and the policy barely uses the input. The gain came from a lower press cost
(0.0008 instead of 0.0015) at a higher learning rate, with the running average of the weights as
the policy.

## What changed in the code

- The observation has a 45th value, the seconds the last action has been held, capped at 1 s.
  `hook/shxcore.h` counts it in ticks for both the hook and the simulator, and during replays too.
  `env/shxenv.py` mirrors it, and `SHX_PROTO_VERSION` went from 3 to 4.
- `Agent` in `scripts/train_ppo.py` keeps its input size and reads only that many observation
  values, so checkpoints with 44 inputs still load and play. `smooth-avg-b7.pt` gave exactly its
  logged result after the change (116 of 120, per level [19 19 20 19 19 20], 1.21 presses per
  second, 332 ms).
- `--add-hold` gives a 44 input checkpoint the new input with zero weights, so it plays the same
  until training uses it (largest output difference 0).
- `--press-cost-end` and `--anneal-start` anneal the press cost linearly.
- Every run keeps an exponential moving average of the weights (`--ema-decay`, 0.999 per update) and
  optionally a uniform average from a set point (`--swa-start-minutes` or `--swa-start-progress`),
  saved as `ema_*.pt` and `swa_*.pt` beside each snapshot.
- `--total-steps` with a generous `--max-minutes` now drives the learning rate, the anneal, and
  the `--snapshot-progress` snapshots by steps, so a run slowed by a CPU quota gets the same
  training, only later.
- `sim/Makefile` now rebuilds `runtime.o` when the hook headers change. Without that, the larger
  `ShxCore` left a stale object behind and the simulator crashed.

`scripts/check_obs_replay.py` played 12 real game episodes (2,218 decisions) with the C
observation matching the Python one at every decision, replays included.

## Round 1

Four arms started from `smooth-avg-b7.pt` and trained 70 minutes with a reversal cost of 0.003, a
learning rate of 3e-5, and 512 simulators each. Another project had the RTX 3080 busy, so two arms
ran on it and two on the GTX 1660, paired so each comparison shared a GPU. The 3080 arms took about
274 million steps and the 1660 arms about 458 million.

| Arm | GPU | Hold input | Press cost |
|---|---|---|---|
| h1-H | 3080 | yes | 0.0015 |
| h1-C | 3080 | no | 0.0015 |
| h1-HA | 1660 | yes | 0.0015 to 0.0008 |
| h1-A | 1660 | no | 0.0015 to 0.0008 |

Each arm's last raw checkpoint, EMA, uniform average from 30 minutes, and average of the 40 to
70 minute snapshots were run on the three seed sets (360 episodes).

| Arm | Raw | EMA | Uniform | Snapshots | Presses/s |
|---|---|---|---|---|---|
| h1-H | 341 | 339 | 344 | 340 | 1.16 to 1.18 |
| h1-C | 338 | 343 | 345 | 342 | 1.16 to 1.17 |
| h1-HA | 342 | 348 | 344 | 339 | 1.17 to 1.19 |
| h1-A | 347 | 344 | 343 | 346 | 1.18 to 1.19 |

None beat 348. Every candidate pressed a little less than `smooth-avg-b7.pt` at about the same
survival, and the spread of 338 to 348 is about what noise gives. With about 96% of episodes
reaching the cap, the binomial spread over 360 episodes is about 4 either way.

The hold input went almost unused. Setting it to 0, 0.1, 0.3, or 1.0 s changed the greedy action
in 0.1 to 0.4% of 9,000 decisions for both hold arms, and its weights grew to a norm of 0.2 to
0.3 against a median of 1.8 for the other inputs. The policy already holds keys well from the last
action and the walls, so the new input adds little the network doesn't already have.

Averaging weights across the four arms (all from the same start) gave 343 to 346, no better than
the best single arm.

## Round 2

The anneal arms did slightly better without pressing more, so round 2 pushed the press cost lower.
All four arms ran 70 minutes from round 1's leaders with a reversal cost of 0.003. From 15:57 the
whole session was held to about 25% of the CPU, which throttled these arms for their last 32
minutes, so the 1660 arms took about 410 million steps and the 3080 arms about 184 million.

| Arm | Starts from | Hold input | Press cost | Learning rate |
|---|---|---|---|---|
| h2-HAlow | h1-HA EMA | yes | 0.0008 to 0.0003 | 3e-5 |
| h2-Alow | h1-A raw | no | 0.0008 to 0.0003 | 3e-5 |
| h2-Azero | h1-A raw | no | 0.0008 to 0 | 3e-5 |
| h2-HAlr | h1-HA EMA | yes | 0.0008 | 1e-4 |

| Arm | Raw | EMA | Uniform | Snapshots | EMA presses/s |
|---|---|---|---|---|---|
| h2-HAlow | 351 | 352 | 349 | 355 | 1.23 |
| h2-Alow | 345 | 349 | 344 | 347 | 1.23 |
| h2-Azero | 346 | 352 | 348 | 349 | 1.30 |
| h2-HAlr | 345 | 349 | 342 | 347 | 1.19 |

A lower press cost buys survival with presses. Annealing to 0.0003 gave h2-HAlow 349 to 355 at
1.22 to 1.26 presses per second, and annealing to 0 let h2-Azero's raw policy climb to 1.48.
Holding the cost at 0.0008 with a learning rate of 1e-4 kept h2-HAlr at 1.17 to 1.19 while its EMA
reached 349. In this round the EMA beat the raw checkpoint in each of the four arms.

## Fresh seed sets

Every candidate above was picked on the same three seed sets, and `smooth-avg-b7.pt` was picked on
them too, so each set of numbers flatters its pick. Three new sets (130000, 170000, and 210000)
that no policy was chosen on give a fairer comparison.

| Policy | 130000 | 170000 | 210000 | Fresh total | Presses/s |
|---|---|---|---|---|---|
| h2-HAlr EMA | 118 | 117 | 116 | 351 | 1.19 |
| h2-Alow EMA | 118 | 115 | 117 | 350 | 1.23 |
| h2-HAlow snapshots | 118 | 115 | 116 | 349 | 1.23 |
| h2-HAlow EMA | 117 | 114 | 118 | 349 | 1.23 |
| h1-A raw | 115 | 117 | 114 | 346 | 1.19 |
| h1-HA EMA | 116 | 114 | 113 | 343 | 1.18 |
| `smooth-avg-b7.pt` | 113 | 110 | 114 | 337 | 1.21 |

On the fresh sets `smooth-avg-b7.pt` falls from 348 to 337, while the round 2 policies stay near
350. Over all six sets (720 episodes), h2-HAlr's EMA reached the cap in 700 against 685 for
`smooth-avg-b7.pt`, and h2-HAlr was the only round 2 arm whose candidates all stayed under 1.21 presses per second.
h2-HAlow's EMA (701) and h2-Alow's EMA (699) share their settings and differ in the hold input
and their round 1 starting point, so the input made no clear difference there either.

## Control for the hold input

h3-Alr repeated h2-HAlr's settings without the hold input, starting from h1-A's raw checkpoint
(the best round 1 arm without it) and stopping at the same 184.5 million steps. The step budget
kept its learning rate and snapshots on the same schedule even though it ran under a CPU quota.

| Policy | 12345 | 50000 | 90000 | 130000 | 170000 | 210000 | Total | Presses/s |
|---|---|---|---|---|---|---|---|---|
| h2-HAlr EMA | 116 | 115 | 118 | 118 | 117 | 116 | 700 | 1.19 |
| h3-Alr EMA | 119 | 115 | 116 | 117 | 112 | 113 | 692 | 1.18 |
| h3-Alr snapshots | 115 | 114 | 117 | 119 | 115 | 112 | 692 | 1.19 |
| h3-Alr uniform | 114 | 115 | 115 | 117 | 113 | 113 | 687 | 1.18 |
| h3-Alr raw | 115 | 111 | 114 | 117 | 115 | 109 | 681 | 1.18 |

The control's EMA reached 350 of 360 on the three usual sets at 1.18 presses per second, so it
beats `smooth-avg-b7.pt` there too. Over 720 episodes it's 8 behind h2-HAlr's EMA, about 1%, which
is within the noise for two policies that also started from different checkpoints. h2-HAlr's EMA
still barely uses the input. Its weights grew to a norm of 0.45 against a median of 1.85, and
setting the input to 0, 0.1, 0.3, or 1.0 s changed the greedy action in 0.2 to 0.3% of 9,000
decisions. So the hold input didn't help, and the gain over `smooth-avg-b7.pt` comes from the lower
press cost, the higher learning rate, and using the running average as the policy.

## Final pick

`models/smooth-hold-ema.pt` is h2-HAlr's EMA. On the three usual seed sets it reached the cap in
349 of 360 at 1.19 presses per second, 340 ms mean hold, and 93% of movement from holds of 200 ms
or longer.

In the real game, with the standard 20 seeds per level from 12345 and a 600 s cap, it reached the
cap in 116 of 120 episodes, with per level counts [20 20 20 18 18 20]. The four deaths (Hyper
Hexagon episode 4 at 231.30 s and episode 20 at 9.42 s, Hyper Hexagoner episode 1 at 17.73 s and
episode 13 at 32.60 s) are the same episodes at the same times as in the batched simulator
evaluation. That's the same count as `smooth-avg-b7.pt` on this one set. Its edge shows on the
other five sets, where it reached the cap in 584 of 600 against 569.

## What this session showed

- The hold input went almost unused when added to a policy that already holds well, and a control
  without it came within noise of the pick (692 against 700 of 720).
- The press cost sets the trade between survival and presses. 0.0008 with a learning rate of 1e-4
  kept presses at 1.19 while survival rose, and going lower raised both.
- A running EMA of the weights did about as well as averaging snapshots afterwards, and usually
  better than the raw checkpoint, at no extra cost.
- Averaging weights across arms that started from the same checkpoint didn't beat the best arm.
- A pick made on the same seeds it's judged on looks a few episodes better than it is, so fresh
  seed sets are worth the few minutes they take.

Ideas still open:

- Training a hold aware policy from `sim-tuned.pt` instead of widening a finished one, so the input
  is there while holds are learned. That means redoing the night's press cost bursts.
- A press cost between 0.0008 and 0.0003 at a learning rate of 1e-4, which may sit closer to 355
  survival at about 1.2 presses per second.
