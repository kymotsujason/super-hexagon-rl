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

At the time, the recorded death at 5.48 s looked like a bad choice by the policy, since it held right
for 13 decisions while its angle didn't change. The next section shows what really happened.

## Why live runs died early

The cause turned out to be keyboard focus, not the model or the openings. When a game window
gains or loses focus, SDL releases every key it thinks is held. The live player only sent a key
press when its action changed, so after a focus change the game had let go of the key while the
player still meant to hold it, and the player drifted into a wall. Focus moves whenever a window
opens or closes (every relaunch) and whenever someone clicks around the desktop.

These were ruled out first, each with a test that could have failed:

- How a level starts. `hook/shxcore.h` gained reset flags that keep the random state and the
  player's angle, and one that stops at level select so the levels can be browsed with real key
  presses first. Starts made the live way died early in 0 to 1 of 300 openings per test in the
  simulator, and in 0 of 60 per level in the real game in lockstep, the same as normal starts.
- Late keys. The movement right after a fresh key press was a full step in all 233 live presses
  measured, the same as in the simulator.
- Drawing. A lockstep game that drew a frame after every step matched a headless one exactly for
  four 30 s episodes.
- The live player's own parsing. Its observation matched the C one in all 16,200 lockstep steps
  checked across the six levels.

What settled it was replaying live games in the simulator. With `--record`, `play_live.py` now
captures the whole game state, the hook's random number state, and the memory map at the first
decision of each episode, and `scripts/replay_capture.py` loads that into the simulator
(`shxSimSetRandom` sets its random state), replays the live actions, and compares every
observation. Of 60 captured live episodes on Hyper Hexagonest and Hexagoner, all 55 that survived
matched the simulator at every decision. All 5 early deaths stopped matching at one decision, in
each case while the same key had been held for at least four decisions, and in each one the policy
on its own survived the full 15 s from the same captured start in the simulator. Replaying that
decision with the held key let go, from its start or one tick in, matched the live game exactly in
all 5.

A direct test confirmed it. With two game windows, holding a key in one and moving focus to the
other turned that game's key array from held to released, both ways round. Synthetic focus events
sent straight to a window did the same, which allowed a stress test without moving the desktop's
real focus.

The fix is in `play_live.py`. On every poll it compares the game's key array with the key it means
to hold, and presses the key again when the game has dropped it. With a focus event sent to both
windows every 0.5 to 3 s, the fixed player reached the 30 s cap in 19 of 20 Hyper Hexagonest
episodes (pressing a dropped key again 3,283 times), while the old player reached it in none of
20, dying between 3.32 and 18.23 s. Several of the old player's death times (3.82, 4.32, and
4.98 s) are the same as live early deaths seen before the fix.

Under normal use the fix held as well. Hyper Hexagonest with a fresh game every episode reached
the 15 s cap in 18 of 18 runs, and Hexagoner in one game reached it in 30 of 30, where the same
setups had died early in 4 of 30 and 1 of 30 before. The player pressed a dropped key again 29 and
58 times, mostly when the other window relaunched, so focus changes are common in everyday use.
Three of those 48 runs drifted from the simulator replay by a tick where a key came back late, and
all three survived.

A new game window takes keyboard focus when it opens, so typing elsewhere while live runs start
can send keys into the game. One run met its title screen with the cursor moved off the first
entry, likely from that. The player now starts a fresh game when it finds the title screen like
that, as it does for a stuck menu walk.

## Menu walk stalls

Twice, with two games started at the same moment, the menu walk gave up after 60 s without seeing
the level start, even though one of the games had started it. The game's app pointer is set once
about 0.2 s after launch and doesn't change, so a stale object isn't the reason, and 24 later menu
walks in three pairs of runs started together didn't stall. The cause isn't known yet. The player now
starts a fresh game and tries once more when it happens, and `SHX_LIVE_DEBUG=1` logs every menu
state and key press so the next stall can be read.

## Recording a demo

`ffmpeg -f x11grab` only records black from the game window, likely because the desktop runs a
rootless Xwayland that doesn't expose the game's GL contents through the root window. Spectacle
screenshots work (`scripts/capture_watch.sh` uses it), and OBS's screen capture through PipeWire
should work for video, although it wasn't tried here.
