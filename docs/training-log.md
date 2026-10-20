# Training log

## First PPO run on 2026-10-18

The run trained `scripts/train_ppo.py` on Hexagon only, with 10 headless envs, frame skip 2, a 44
value egocentric observation (`env/shxenv.py`), reward -1 on death, gamma 0.995, and a 256 unit
MLP. It ran for 20 minutes under a `CPUQuota=1600%` cap and took 6.2 million agent steps (12.5
million game ticks). The checkpoint is `runs/train/ppo-first/model.pt`.

Mean survival in training (sampled actions, 120 s episode cap) went from 3.2 s to 97 s. The
greedy evaluation in the real game used `scripts/eval_policy.py` with seeds 12345 to 12374 and
a 300 s cap.

| Level | Episodes | Mean | Median | Min | Max | Past 60 s |
|---|---|---|---|---|---|---|
| Hexagon | 30 | 136.6 s | 118.3 s | 72.1 s | 265.2 s | 100% |
| Hexagoner | 10 | 24.3 s | 14.6 s | 6.3 s | 70.0 s | 20% |
| Hexagonest | 10 | 29.3 s | 25.2 s | 12.3 s | 53.9 s | 0% |

Hexagoner and Hexagonest weren't in training, so those rows measure transfer only.

The evaluation found a reset bug. A Hexagon run long enough to change stage partway sets
`superhex+0x55dc`, and `gamelogic` then puts the stage back every tick until the death sequence
finishes. The reset pressed Escape too early and looped. It now waits for the game over screen
and for both stage change flags to clear, which takes about 76 ticks after such a run. The
determinism check passed 12 of 12 again after the fix.

Training throughput was about 7,000 agent steps per second early and 5,200 by the end, which is
10,000 to 15,000 game ticks per second. The headless benchmark gets about 1.1 million ticks per
second from 12 instances, so the trainer's pipeline used roughly 1% of what the games can produce.

## Pipeline speedups on 2026-10-19

Profiling the first trainer showed rollouts took 52 s of each minute and the PPO update 7 s, so
the time went into moving data, not into the game. These changes were measured one at a time
with 1 minute runs under a `CPUQuota=1600%` cap.

| Change | Steps per second | Cores used |
|---|---|---|
| First trainer, 10 envs over gymnasium pipes | about 6,000 | not measured |
| One process driving 24 games, CPU rollouts | 27,850 to 36,260 | 2.0 |
| 4 actor processes x 12 games | 65,792 | 3.1 |
| Rollout overlapped with the update, 4 x 16 | 88,461 | 4.7 |
| GAE in numpy and fused Adam, 4 x 16 | 92,086 | 4.5 |
| Same, 6 x 16 | 122,045 | 6.2 |
| Same, 8 x 16 | 119,719 | 7.5 |

The pieces, in the order they went in:

- The hook builds the 44 value observation in C. `scripts/check_obs_replay.py` matched it exactly
  against the Python reference on 3,645 decisions across the six levels.
- A replay command resets with a seed and replays recorded actions, which restarts a run a few
  seconds before a death. The same check confirmed replays land on the recorded state.
- `env/shxvec.py` drives many games from one process, and `hook/shxbatch.so` steps them in one
  call. The batch call alone didn't help, since the remaining cost was waking each game process.
- `scripts/train_ppo.py` runs actor processes, each with its own games and a CPU copy of the
  policy, and overlaps the next rollout with the GPU update (actors run one update behind).
- Training samples levels by weight towards the ones the agent survives least on, restarts 25%
  of deaths 1 to 3 s earlier, and allows 600 s episodes.

Two more reset traps came up at this speed. A run that finishes a level shows a message on the
game over screen that only select dismisses (`superhex+0x5584` at 1 or 2). Passing 60 s on Hyper
Hexagonest marks the ending (value 3), which starts after the next death and has no menu path
out, so the hook refuses the reset and the env relaunches that game.

6 x 16 is the default. 8 x 16 used more CPU for no gain and pushed the whole machine to about
78% for that minute, together with other programs.

## All six levels on 2026-10-19

The run used the new trainer with 4 actors of 16 games under a `CPUQuota=500%` cap (another job
was using about 13 cores at the time). It ran 40 minutes from scratch and took 149.5 million agent
steps at about 62,000 per second, using 3.5 cores on average. The checkpoint is
`runs/train/ppo-all-levels/model.pt`. Mean survival on fresh training episodes ended between
347 s and 518 s per level, and the Hyper Hexagonest ending was reached 2,677 times.

The greedy evaluation in the real game ran 20 seeded episodes per level with a 600 s cap.

| Level | Mean | Median | Worst | Past 60 s | Hit 600 s |
|---|---|---|---|---|---|
| Hexagon | 455.8 s | 600.0 s | 73.3 s | 20 of 20 | 14 |
| Hexagoner | 600.0 s | 600.0 s | 600.0 s | 20 of 20 | 20 |
| Hexagonest | 597.0 s | 600.0 s | 540.1 s | 20 of 20 | 19 |
| Hyper Hexagon | 518.2 s | 600.0 s | 22.1 s | 18 of 20 | 17 |
| Hyper Hexagoner | 600.0 s | 600.0 s | 600.0 s | 20 of 20 | 20 |
| Hyper Hexagonest | 596.7 s | 600.0 s | 534.0 s | 20 of 20 | 19 |

The determinism check (12 of 12) and the observation and replay check (3,645 decisions, no
mismatches) passed on the final hook before the evaluation. Frames captured from the game window
show the policy in Hyper Mode at 33 s and 38 s (`runs/watch-hyper-hexagonest/frames.png`).

Hexagon, the easiest level, scored lowest. The level weighting aims at 120 s, every level passed
that early in training, so sampling went close to uniform after that. Raising the target or
weighting by deaths per minute would push training towards the weak spots.

## Simulator only, 20 minutes, on 2026-10-19

`train_ppo.py --backend sim` ran 1,024 simulators on 8 threads with a 128 step rollout (131,072
samples per update) for 20 minutes. It took 506.2 million agent steps at about 422,000 per
second, using 5.4 cores on average. The checkpoint is `runs/train/sim-20min/model.pt`.

The greedy evaluation in the real game used the same 20 seeds per level and 600 s cap as before.

| Level | Mean | Worst | Past 60 s | Hit 600 s |
|---|---|---|---|---|
| Hexagon | 57.1 s | 31.4 s | 7 of 20 | 0 |
| Hexagoner | 587.8 s | 355.7 s | 20 of 20 | 19 |
| Hexagonest | 585.5 s | 310.1 s | 20 of 20 | 19 |
| Hyper Hexagon | 76.5 s | 21.1 s | 5 of 20 | 1 |
| Hyper Hexagoner | 585.2 s | 303.6 s | 20 of 20 | 19 |
| Hyper Hexagonest | 600.0 s | 600.0 s | 20 of 20 | 20 |

So a policy trained only in the simulator plays the real game, and its real game results match
what the simulator showed in training (about 65 to 75 s on the two stage 0 levels, 485 to 518 s
on the rest). In 200 simulated Hexagon episodes, 197 deaths happened with the shape at 4 sides,
the square phase of stage 0. The policy trained in the real game averaged 463 s on Hexagon in
the simulator against 456 s in the real game, so the simulator plays square phases faithfully and
the gap comes from this run's learning. Its update takes 16 gradient steps per 131,072 samples,
against 16 per 16,384 in the real game runs, and entropy fell to 0.09 early before recovering.
Smaller minibatches, starting from the real game checkpoint, or a higher level target would all
push more learning into the square phases.

## Tuned simulator run on 2026-10-19

This run changed three things from the 20 minute simulator run and kept the rest.

- A 64 step rollout over 1,024 simulators gives 65,536 samples per update, split into 16
  minibatches of 4,096. That's one gradient step per 1,024 samples, the ratio of the real game
  runs, against one per 8,192 before.
- The level target went from 120 s to 600 s, so levels near the cap drop to the 0.15 floor
  weight and the stage 0 levels get most of the sampling.
- It started from the real game checkpoint (`runs/train/ppo-all-levels/model.pt`), with the
  learning rate lowered to 1e-4 so the start wouldn't undo it.

It ran 20 minutes and took 227.1 million agent steps at about 189,000 per second (the smaller
minibatches made the update take 779 s of the 20 minutes), using 3.5 cores on average. Fresh
episode survival in training ended between 533 s and 558 s on every level. The checkpoint is
`runs/train/sim-tuned/model.pt`.

In the real game, with the same 20 seeds per level and 600 s cap as the earlier evaluations,
all 120 episodes reached the cap. A second evaluation on new seeds (777000 up) with a 30 minute
cap ran 5 episodes per level, and all 30 reached 1,800 s, which is 15 hours of play without a
death. Frames from the game window show it playing Hexagon normally at 4, 10, and 16 s.

| Policy | Hexagon | Hyper Hexagon | Other four levels |
|---|---|---|---|
| Real game, 40 min | 455.8 s | 518.2 s | 596.7 to 600.0 s |
| Simulator, 20 min | 57.1 s | 76.5 s | 585.2 to 600.0 s |
| Simulator tuned, 20 min | 600.0 s | 600.0 s | 600.0 s |

The table shows mean survival over 20 episodes with a 600 s cap. The 600 s cap no longer
separates the tuned policy from a perfect one, so longer caps are the next measure.

## Smoothness on 2026-10-19

The tuned policy's greedy play changes action about 9 times per second, mostly as single 33 ms
taps, and flips straight between left and right about 2.4 times per second. Two fixes were
tried, judged on survival and on the jitter measures from `scripts/eval_style.py` (simulator,
5 episodes per level, 300 s cap).

Sticky play keeps the previous action unless the best one beats it by a probability margin. On
the tuned policy, the margins compared like this.

| Margin | Reached 300 s | Changes/s | Flips/s | Wiggles/s | Mean hold |
|---|---|---|---|---|---|
| 0 | 30 of 30 | 9.2 | 2.31 | 1.79 | 85 ms |
| 0.1 | 30 of 30 | 7.6 | 2.00 | 1.34 | 98 ms |
| 0.2 | 30 of 30 | 6.6 | 1.85 | 1.09 | 111 ms |
| 0.3 | 29 of 30 | 5.9 | 1.81 | 0.91 | 126 ms |
| 0.5 | 23 of 30 | 4.9 | 1.52 | 0.41 | 156 ms |

The reversal cost (`--reversal-cost`) charges the reward whenever the action goes straight from
left to right or back. Four runs started from `runs/train/sim-tuned/model.pt` and trained 15
minutes each in parallel (about 60 million steps each, 9.2 cores together).

| Cost | Reached 300 s | Changes/s | Flips/s | Wiggles/s | Mean hold |
|---|---|---|---|---|---|
| 0 | 30 of 30 | 9.3 | 2.42 | 1.86 | 86 ms |
| 0.003 | 28 of 30 | 7.0 | 0.00 | 0.14 | 103 ms |
| 0.01 | 29 of 30 | 9.7 | 0.00 | 0.15 | 86 ms |
| 0.03 | 23 of 30 | 9.1 | 0.00 | 0.11 | 84 ms |
| 0.003, sticky 0.2 | 29 of 30 | 5.0 | 0.00 | 0.14 | 136 ms |

In the real game, with the same 20 seeds per level and 600 s cap as the earlier evaluations,
the number of episodes reaching the cap was:

| Policy | Hexagon | Hexagoner | Hexagonest | Hyper Hex. | Hyper Hexagoner | Hyper Hexagonest | Total |
|---|---|---|---|---|---|---|---|
| Tuned, greedy | 20 | 20 | 20 | 20 | 20 | 20 | 120 |
| Cost 0.003, greedy | 17 | 18 | 20 | 18 | 20 | 20 | 113 |
| Cost 0.003, sticky 0.2 | 17 | 15 | 13 | 19 | 19 | 20 | 103 |

The reversal cost removes direct flips and about 92% of left, right, left wiggles, for 7 fewer
episodes reaching the cap. Sticky play cuts the remaining taps (46% fewer action changes than the
tuned policy) but costs another 10. The cost runs only trained 15 minutes, and their training
survival stayed about 5% under the control, so longer training may win some of it back.

## Overnight smoothing on 2026-10-19

Seven bursts of 30 to 70 minutes added a press cost to the reversal cost, which charges every new
press of a direction while holds stay free. The final policy, `models/smooth-avg-b7.pt`, is a
weight average of the last burst's later snapshots. It presses 1.21 times a second with a 332 ms
mean hold and reached the 600 s cap in 116 of 120 real game episodes, per level [19 19 20 19 19
20], each death the same episode and time as the batched simulator evaluation predicted.
`docs/overnight-log.md` has every burst.

