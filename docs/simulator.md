# C simulator

`sim/shxsim.so` runs Super Hexagon's game logic without the game. It compiles Ghidra's
decompilation of the gameplay functions and starts from a dump of the real game's `superhex`
object, so it plays the same game the hook drives, thousands of times faster than real time.

## How it's built

`sim/tools/ghidra2c.py` turns the functions exported into `decomp/` (see `decomp/README.md`) into
`sim/gen/logic.c`. It flattens `Class::method` names, renames `this` to `self`, and rewrites
Ghidra's branchless float selects, which would convert floats by value if compiled as written.
Every call outside the compiled set becomes an empty stub, and `sim/tools/extract_songdata.py`
copies the six beat tables out of the binary. `make -C sim` runs both and builds the library with
`-ffp-contract=off -fwrapv -fno-strict-aliasing`, since the original binary never fuses multiplies
into adds and the decompiled code puns types.

The compiled set is everything `superhex::update` reaches for gameplay, which covers input,
`gamelogic`, `generatewave`, stage changes, visual effect timers, `helpclass::updateglow`, and the
music state. These functions are written by hand in `sim/runtime.c` instead.

| Function | Why it's hand written |
|---|---|
| `oneRound`, `ofRandom` | Ghidra's return types are wrong |
| `getExpectedFrameDelta` | returns 1.0f at 60 fps |
| `getLastFrameTimeMillis` | per simulator frame time |
| `ofGetElapsedTimeMillis` | reproduces the hook's clock |
| `musicclass::loadsongdata` | parses the extracted beat tables |
| `graphicsclass` palette calls | keep the palette number, skip colours |
| string and vector helpers | leave valid empty values |

Saves, scores, Steam, sound, and drawing are stubs. Each simulator has its own `superhex` object,
its own `rand` state (glibc `random_r`, as in the hook), and the hook's shared memory layout, so
`ShxSim` in `env/shxsim.py` has the same reset, step, replay, and dump calls as `ShxGame`.
`shxSimSetRandom` can also load a random state captured from a live game.

The dump holds pointers into the real game's memory. On load, every 8 byte value that points into
the game's mapped memory becomes a pointer to the simulator's own empty string. `scripts/check_sim.py`
writes the dump, the memory ranges, and the tick it was taken at as `sim/initial_state.*`.

## Validation

`scripts/check_sim.py` plays the same seeded episodes in a headless game and a simulator built from
its dump. At every decision it compares the game state, every enabled wall, and the observation,
and every 50 decisions the whole 247 KB object, except the ranges in `sim/dump_ignore.txt` and the
replaced pointers. On 2026-10-19, 24 episodes (six levels, four seeds, up to 180 s each, about 69
minutes of play) matched on every check.

Two later checks agreed. The fast evaluation in `scripts/eval_batch.py` predicted the real game's
deaths to the hundredth of a second each time it was checked, and 55 live games loaded into the
simulator with `scripts/replay_capture.py` matched it at every decision (see `docs/live-play.md`).

The checks also showed what the simulator can't skip. `helpclass::updateglow` has to run, since
`updatevisualeffects` reads its counter. The palette number and transition flag (`superhex+0xe714`
and `+0xe710`) have to be kept, since `gamelogic` branches on them. The hook rounds its clock to
whole milliseconds, so the simulator can rebuild the music beat from ticks.

## Speed

Measured on 2026-10-19 with random actions.

| Setup | Agent steps per second |
|---|---|
| One simulator through Python | 1,077,761 |
| 64 simulators, 1 thread | 1,322,869 |
| 256 simulators, 4 threads | 1,599,362 |
| 256 simulators, 8 threads | 1,857,113 |

Training with about a thousand simulators on 8 threads ran at about 467,000 agent steps per second
on about 5 cores, against 122,045 for the real game.

## Limits

- Code the checks never reached (arcade mode, the leaderboards, the inside of the ending) isn't
  verified, and stubs there could differ from the game. Training stops episodes when the ending
  starts.
- `shxsim.so`, `sim/gen/`, and the dump come from the game's code and data, so they stay on your
  machine.
- A game update changes the binary, and then the decompilation, the addresses, and the dump all
  need redoing.
