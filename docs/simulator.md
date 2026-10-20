# C simulator

`sim/shxsim.so` runs Super Hexagon's game logic without the game process. It compiles Ghidra's
decompilation of the gameplay functions from the native Linux build (SHA-256 `6f13c58d...2ba1d`)
and starts from a dump of the real game's `superhex` object, so it plays the same game the hook
drives, not a reimplementation of it.

## How it's built

`sim/tools/ghidra2c.py` turns the decompiled functions in `decomp/` into C in `sim/gen/logic.c`.
It flattens `Class::method` names to `Class__method`, renames `this` to `self`, qualifies bare
calls to methods of the same class, and rewrites Ghidra's branchless float selects
(`~-(uint)(cond) & (uint)value`) into real float selects, since compiled as written they convert
the float by value. Every call outside the compiled set becomes a stub in `sim/gen/stubs.c` that
does nothing. `sim/tools/extract_songdata.py` copies the six beat tables out of the binary into
`sim/gen/songdata.c`. `make -C sim` runs both and builds the library.

The compiled set is everything `superhex::update` reaches for gameplay. That covers input,
`gamelogic`, `generatewave` and the wave helpers, stage changes, visual effect timers,
`helpclass::updateglow`, and the music state that picks tracks and beats. The functions below are
written by hand in `sim/runtime.c` instead, each matching the machine code at the address noted
there.

| Function | Why it's hand written |
|---|---|
| `oneRound`, `ofRandom` | Ghidra's return types are wrong |
| `getExpectedFrameDelta` | returns 1.0f at 60 fps |
| `getLastFrameTimeMillis` | per simulator frame time |
| `ofGetElapsedTimeMillis` | reproduces the hook's clock |
| `musicclass::loadsongdata` | parses the extracted beat tables |
| `graphicsclass` palette calls | keep the palette number, skip colours |
| string and vector helpers | leave valid empty values |

Saves, scores, Steam, sound output, and drawing are stubs. Each simulator holds its own copy
of the `superhex` object, its own `rand` state (glibc `random_r`, the same generator the hook
seeds), and a result block with the hook's shared memory layout, so `ShxSim` in `env/shxsim.py`
has the same reset, step, replay, and dump calls as `ShxGame`.

The dump holds pointers into the real game's memory (strings, vectors, sound players). On load,
every 8 byte value that points into the game's mapped memory, or at its empty string at
0x7f56f8, becomes a pointer to the simulator's own empty string. The game's mapped ranges come from
`/proc/<pid>/maps` at dump time and are saved as `sim/initial_state.ranges`, next to the state
(`initial_state.bin`) and the hook tick it was taken at (`initial_state.tick`).
`scripts/check_sim.py` writes all three.

## Validation

`scripts/check_sim.py` launches a headless game, dumps it at the title screen, builds a simulator
from the dump, and plays the same seeded episodes in both. At every decision it compares the
stage, angle, sides, death timer, run time, wall speed, hit flag, camera, rotation mode, hyper flag,
tutorial flag, every enabled wall, and the observation (44 values at the time, 45 since the hold
input was added). Every 50 decisions it compares the whole 247 KB object byte for byte, except the ranges in `sim/dump_ignore.txt` (colour tables,
sound player objects, the song vector, the music start time, and the elapsed millisecond counter)
and the pointers replaced on load.

On 2026-10-19, with the trained policy from `runs/train/ppo-all-levels` choosing actions, 24
episodes (six levels, four seeds, up to 180 s each, about 69 minutes of play) matched on every
check. Three of them ended in the Hyper Hexagonest ending, where both the game and the simulator
refused the next reset.

Several things the validation found along the way are now part of the design:

- Compiled with `-ffp-contract=off -fwrapv -fno-strict-aliasing`, since the original binary
  doesn't fuse multiplies into adds and the decompiled code puns types.
- `helpclass::updateglow` has to run, since `updatevisualeffects` reads its counter.
- The palette number and transition flag (`superhex+0xe714` and `+0xe710`) have to be kept,
  since `gamelogic` branches on them.
- The hook rounds its lockstep clock up to a whole millisecond, so the simulator can reproduce
  the music beat from the tick count.

## Speed

Measured on 2026-10-19 with random actions.

| Setup | Agent steps per second |
|---|---|
| One simulator through Python | 1,077,761 |
| 64 simulators, 1 thread | 1,322,869 |
| 256 simulators, 4 threads | 1,599,362 |
| 256 simulators, 8 threads | 1,857,113 |

`train_ppo.py --backend sim` runs about a thousand simulators on 8 threads in one process, with
actions from one GPU batch per step. Its first minute ran at about 467,000 agent steps per second
on about 5 cores, against 122,045 for the real game pipeline.

A 20 minute simulator run took 506.2 million steps, and its policy played the real game about
as the simulator predicted. The results are in `docs/training-log.md`.

## Limits

- The validation covers the six levels with this policy and these seeds. Code paths it never
  reached (the arcade mode, the leaderboard screens, and the inside of the ending) aren't
  checked, and stubs there could differ from the game.
- Stage mode 4 (the ending) runs on the elapsed millisecond clock, which matches the hook
  only after the clock rounding. The ending isn't used in training, since episodes stop when it
  starts.
- `shxsim.so`, `sim/gen/`, and the initial state are derived from the game's code and data, so
  they stay on your machine and aren't part of this repository.
- A game update changes the binary's hash, and then the decompilation, the addresses, and the
  initial state all need redoing.
