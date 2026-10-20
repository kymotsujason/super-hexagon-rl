# Live play

Every other real game result in this project ran in lockstep, with the hook pausing the game after
each decision until the policy answered. Live play is the other way. The game runs on its own clock
and the policy has to keep up, the way a person does.

## How the live player works

`scripts/play_live.py` launches the real game with the hook preloaded but without `SHX_SHM`, so
the hook never takes over the loop. It only fakes Steam and serves a clock that runs at real speed.

- It reads the `superhex` object through `/proc/<pid>/mem`, which Linux allows for a child process,
  and builds the same 45 value observation as `env/shxenv.py`.
- It decides on every second tick, like training, by polling the run time every 0.5 ms, and it only
  uses a snapshot when two reads of it match.
- It sends key events straight to the game's X window with `XSendEvent`, so they can't land in
  another window, and presses a held key again whenever the game drops it (see below).
- It starts each run from the menus with real key presses, from the game's own random state.

```bash
.venv/bin/python scripts/play_live.py models/smooth-hold-ema.pt --levels 1 --episodes 1 --max-seconds 120
```

| Flag | What it does |
|---|---|
| `--sound <VOLUME>` | music and effects, volume 0 to 10, 10 if left out |
| `--place <X> <Y> <W> <H>` | moves and sizes the window |
| `--relaunch-each` | starts a fresh game for every episode |
| `--record <DIR>` | saves each episode for `scripts/replay_capture.py` |

Put `--sound` after the model path, or it reads the path as the volume. `SHX_LIVE_DEBUG=1` logs the
menu walk.

## How much latency the policy can take

`scripts/eval_batch.py` can delay actions in the simulator. `--delay-decisions N` applies each
action N decisions (33 ms each) late, and `--late-prob P` makes each key land one tick (17 ms) late
with chance P. Both reproduce the normal result exactly when set to do nothing.

| Latency | Runs reaching 600 s |
|---|---|
| none | 116 of 120 |
| 10% of keys one tick late | 66 of 120 |
| 30% of keys one tick late | 13 of 120 |
| every key one tick late | 0 of 120 |
| every key one decision late | 0 of 120 |

These are seed set 12345. The policy depends on every key reaching the game on the very next tick,
since lockstep always gave it that. Live, 31,774 of 31,777 key changes did.

## Why live runs died early

The live player first lost about one run in ten early, while the simulator and lockstep almost
never did. The cause was keyboard focus. When a game window gains or loses focus, SDL releases
every key it thinks is held. The player only sent a press when its action changed, so after a
focus change the game had let go of the key while the player still meant to hold it, and the player
drifted into a wall. Focus moves whenever a window opens or closes, and whenever someone clicks
around the desktop.

These were ruled out first, each with a test that could have failed:

- How a level starts. Reset flags in `hook/shxcore.h` can keep the random state and angle and stop
  at level select, so a lockstep start copies a live one. Live style starts died early in 0 of 60
  real game openings per level, and in 0 to 1 of 300 per test in the simulator.
- Late keys. A fresh press moved the player a full step in all 233 live presses measured.
- Drawing. A game drawing every frame matched a headless one exactly over four 30 s runs.
- Parsing. The live player's observation matched the C one in all 16,200 lockstep steps checked.

What settled it was replaying live games in the simulator. With `--record`, the player captures the
whole game state, the hook's random state, and the memory map at the first decision, and
`scripts/replay_capture.py` loads that into the simulator, replays the live actions, and compares
every observation. All 55 surviving live episodes matched at every decision. All 5 early deaths
stopped matching at one decision while a key was being held, and replaying that decision with the
key let go matched the live game exactly in every one. From the same starts, the policy on its own
survived in the simulator.

A direct test confirmed it. Moving focus from one game window to another turned the first game's
held key into a released one, both ways round, and so did a synthetic focus event sent to a
window, which made a stress test possible without moving the desktop's real focus.

| Test | Old player | Fixed player |
|---|---|---|
| Focus event every 0.5 to 3 s, 30 s runs | 0 of 20 | 19 of 20 |
| Fresh game each run, 15 s runs | 26 of 30 | 18 of 18 |
| One game, 15 s runs | 29 of 30 | 30 of 30 |

The fixed player pressed a dropped key again 87 times in the last two tests, mostly when the other
window relaunched, so focus changes are common in everyday use.

## Smaller pitfalls

In hyper mode `gamelogic` adds 3600 to the run time partway through each tick and takes it off at
the end (Ghidra lines 533 and 1854). A read in between sees the run time 60 s ahead. The first live
hyper runs hit that around 120 s and stopped deciding, so the player now ignores a jump of more
than 30 ticks unless it lasts past 50 ms.

A new game window takes keyboard focus when it opens, so typing elsewhere while live runs start can
send keys into the game. If the title screen's cursor has moved, the player starts a fresh game
instead of pressing select there.

Twice, with two games launched at the same moment, the menu walk gave up after 60 s although one
game had started its level. The cause isn't known, and 24 later menu walks with two games launched
together didn't stall. The player now starts a fresh game and tries once more.

## Results

![Six live games, one per level, played by the agent](live-grid.png)

With six windows at once, one per level, each capped at 600 s, the player reached the cap in all
15 runs on the five lower levels and 7 of 10 on Hyper Hexagonest, before the focus fix. Lockstep
reaches it in 112 to 119 of 120 per level over six seed sets.

## Recording a demo

`ffmpeg -f x11grab` only records black from the game window, likely because the desktop runs a
rootless Xwayland. Spectacle screenshots work (`scripts/capture_watch.sh` uses it), and OBS's
screen capture through PipeWire should work for video, although it wasn't tried.
