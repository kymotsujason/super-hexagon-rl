# Live play in the real game

Every real game result before this ran in lockstep. `hook/shxhook.c` takes over the game's loop,
runs `superhex::update` one 1/60 s tick at a time, and waits for the policy after each decision.
The game code is the real game's, but the game pauses while the policy thinks, and keys go
straight into the game's key array. This page covers the other way to run it, where the game keeps
its own real time loop and the policy has to keep up, the way a person would.

## How the live player works

`scripts/play_live.py` launches the real `SuperHexagon` binary with the hook preloaded but without
`SHX_SHM`. Without that variable the hook never takes over the loop (`SDL_PollEvent` passes
straight through), so it only fakes Steam (score uploads stay off) and serves a clock that runs at
real speed. The game's own loop then accumulates real time and runs an update for each 1/60 s.

- The player reads the `superhex` object through `/proc/<pid>/mem`, which Linux allows for a child
  process at `ptrace_scope` 1. It builds the same 45 value observation as
  `env/shxenv.py`, tracking the last action and how long it has been held itself.
- It decides on every second tick, like training, by polling the run time every 0.5 ms. It reads
  each snapshot twice and only uses it when both copies match (see the torn snapshots below).
- It sends key events to the game's X window with `XSendEvent`. They reach the game without the
  window having focus, and they can't land in another window the way `/dev/uinput` or `ydotool`
  events would.
- It starts each run from the menus with real presses of Return, Right, and Escape, and runs
  start from the game's own random state (a fresh `SHX_SEED` per launch), not a chosen seed.
- After each key change it watches the game's key array to see which tick the key reached the
  game on.

```bash
cd ~/Documents/super-hexagon; .venv/bin/python scripts/play_live.py models/smooth-hold-ema.pt --levels 1 --episodes 1 --max-seconds 120
```

`--sound` turns the music and sound effects on at full volume, and `--sound <VOLUME>` sets a
volume from 0 to 10 instead (put it after the model path). `--place <X> <Y> <WIDTH> <HEIGHT>` moves
the window.
`--relaunch-each` starts a fresh game for every episode, `--record <DIR>` saves each episode's
observations and actions, and `--single-read` turns off the second snapshot read.

## How much latency the policy can take

`scripts/eval_batch.py` gained three options to test this in the simulator before trying it
live. `--delay-decisions N` applies each action N decisions (33 ms each) late. `--late-prob P`
makes each decision's key land one tick (17 ms) late with chance P. `--own-features` fills the
last action and hold values from the policy's own choices, as a live player knows them. Each one
reproduced the normal result exactly when set to do nothing (116 of 120 on seed set 12345).

| Latency | Reached 600 s | Mean survival |
|---|---|---|
| none | 349 of 360 | 586 s |
| one decision, game's features | 0 of 360 | 16 s |
| two decisions, game's features | 0 of 360 | 6 s |
| one decision, own features | 1 of 24 (120 s cap) | 29 s |
| 10% of keys one tick late | 66 of 120 | 436 s |
| 30% of keys one tick late | 13 of 120 | 265 s |
| every key one tick late | 0 of 120 | 121 s |

The first three rows cover the three usual seed sets, the fourth is 4 episodes per level, and the
last three are seed set 12345, where the policy normally reaches the cap in 116 of 120. The policy
depends on every key reaching the game on the very next tick, since lockstep always gave it that.
Even one late key in ten loses 50 of those 116 long runs. A live player can
only match lockstep if nearly every key gets in before the game's next input read.

## The hyper mode run time

The first live attempt's three finished runs were all on hyper levels, and each died about 2 s
after 120 s of play, with decisions stopping at about 120 s. In hyper mode `gamelogic` adds 3600 to
the run time at line 533 and takes it off at line 1854 of `decomp/superhex_gamelogic.c` (Ghidra's
pseudocode, which isn't included, see `decomp/README.md`). A test run
read the run time as 180.02 s after 3,598 decisions (120 s), so a read had landed between the two.
I don't know why that happens around 120 s in particular, although something slow running in
between, such as saving a new best time, would explain it. The player then scheduled its next
decision a minute out and stopped deciding. Between ticks the run time
never jumps (the simulator matched the tick count exactly through 9,000 ticks of Hyper Hexagon),
so lockstep was never affected. The player now treats a jump of more than 30 ticks as a read inside
a tick unless it lasts past 50 ms, and it only uses a snapshot when the run time reads the same
before and after it. After that change a Hyper Hexagon run went past 120 s to the 135 s cap with
one decision per two ticks and none missed.

## Live results

Six windows ran at once, one per level, each in its own game at 800 by 600 with a 600 s cap. The
five lower levels were stopped after three episodes each, once their timing and survival had
settled, and Hyper Hexagonest ran its full ten. The lockstep column is the same policy's count over
the six seed sets (120 episodes per level).

| Level | Live episodes | Live at 600 s | Lockstep at 600 s |
|---|---|---|---|
| Hexagon | 3 | 3 | 112 of 120 |
| Hexagoner | 3 | 3 | 119 of 120 |
| Hexagonest | 3 | 3 | 117 of 120 |
| Hyper Hexagon | 3 | 3 | 116 of 120 |
| Hyper Hexagoner | 3 | 3 | 117 of 120 |
| Hyper Hexagonest | 10 | 7 | 119 of 120 |

Across those 25 episodes, 31,777 key changes reached the game and 3 of them were a tick late, and
3 of 396,633 decision ticks were missed when the game skipped ahead. At that rate the latency
tests above predict no real loss, and the five lower levels reached the cap in 15 of 15. That's
too few episodes to pin down a survival rate, but it's what lockstep would predict.

### Hyper Hexagonest openings

All three Hyper Hexagonest deaths came in the first 13 s (12.82, 3.82, and 4.98 s), and every run
that got past the opening reached 600 s. Four more tests looked only at openings, most of them
starting a fresh game for every episode.

| Test | Episodes | Died early |
|---|---|---|
| 70 s cap | 8 | 1 |
| 15 s cap, one snapshot read | 29 | 4 |
| 15 s cap, two snapshot reads | 27 | 4 |
| 15 s cap, recorded | 30 | 1 |

That's 10 early deaths in 94 live openings, about 11%. In the simulator the same policy died early
in none of 800 openings (200 seeds at each of four start angles, 30, 54, 60, and 90, with the live
start delay of 8 ticks), and lockstep reached the cap in 119 of 120.

These causes were ruled out:

- Late keys. In all 13 early deaths, every key landed on the next tick.
- A stalled player. In all 13, decisions continued right up to the death.
- The start angle and start delay. Live runs start at the angle the menus leave (54, 60, and 90
  were seen) a few ticks in, while lockstep starts at 30 on tick 0, and neither made a difference
  in the simulator.
- Torn snapshots. The run time changes early in an update, so a snapshot taken right after it
  changes often lands while the update is still moving walls. Reading twice found 874 such
  snapshots in 27 episodes, and a separate probe showed some of them differ in wall distances and
  the death timer. However, the early death rate was the same with and without the second read. The second read stays on, since it can only
  help.

The recorded death at 5.48 s shows the policy making a bad choice in a tight opening. It waited
until 0.04 s before a ring of walls arrived, moved right into the gap and kept going, and then
held right for 13 decisions while pinned at the edge of its slot, with the only gap three slots
away. The observations and keys look normal throughout.

Why openings like that come up live and not in the simulator is still open. Two deaths repeated
exactly in separate games, 3.82 s with 111 decisions twice and 5.48 s with 161 decisions twice, so
live openings seem to come from a small set that includes a few the policy can't handle. The next
step is to reseed the live game's random generator at the level start through `/proc/<pid>/mem`,
so a live run and a simulator run share a seed and can be compared tick by tick until they part.

### What it means for showing it off

The policy plays the real game live, in real time, with real key events, and nothing measured so
far separates it from lockstep on Hexagon through Hyper Hexagoner. On Hyper Hexagonest about one
opening in ten ends early, always within the first 15 s, and every run that got past that has
reached its cap so far. Restarting after a quick death is enough for a demo.

## Recording a demo

`ffmpeg -f x11grab` only records black from the game window, likely because the desktop runs a
rootless Xwayland that doesn't expose the game's GL contents through the root window. Spectacle
screenshots work (`scripts/capture_watch.sh` uses it), and OBS's screen capture through PipeWire
should work for video, although it wasn't tried here.
