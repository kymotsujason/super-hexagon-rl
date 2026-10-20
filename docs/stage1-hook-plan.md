# Stage 1 lockstep hook plan

This plan turns the native Linux build of Super Hexagon into a steppable RL environment through an
`LD_PRELOAD` library. It rests on the static analysis in `decomp/NOTES-core.md`,
`decomp/NOTES-input.md`, and `decomp/NOTES-generatewave.md`. Nothing here has been run yet, so
every throughput or behavior claim below is a design expectation to measure in the first build.

## What the analysis settled

The binary (`SuperHexagon`, SHA-256 `6f13c58d...2ba1d`, buildid 8838351) is an unstripped,
non-PIE x86-64 ELF. It links SDL2 dynamically from `lib64/` through `RPATH`, imports
`SDL_GetPerformanceCounter`, `SDL_GetPerformanceFrequency`, `SDL_GetTicks`, `SDL_Delay`, and
`SDL_PollEvent`, and imports `rand` and the Steam API from shared libraries. A preload library can
replace each of those.

The game logic already runs in fixed steps. The frame loop in
`ofAppGlutWindow::runAppViaInfiniteLoop` (0x4889c0) accumulates real time, then calls
`superhex::update()` once per elapsed 1/60 s, and each call gets exactly `period / freq` as its
frame time. `update` runs `calculateTime`, `gameinput`, `gamelogic`, and music processing, and the
logic scales its changes by a time scale that comes out at 1.0 every tick. So driving `update`
ourselves at a fixed rate reproduces normal play exactly, with no need to emulate the accumulator.

Two things read real time. `superhex::draw()` stores `ofGetElapsedTimeMillis()` at
`superhex+0x3c710`, which scripts the stage mode 4 phases, and `gamelogic` calls
`ofGetElapsedTimeMillis()` for the music loop and beat pulse. Both go through `SDL_GetTicks`, so
the hook has to serve a virtual clock there too.

Random numbers come only from game code. `rand` is reached only through `ofRandom`, which only
`gamelogic`, `generatewave`, `gameinput`, `musicclass::play`, and `changetostage` call, and the
bundled libraries don't import `rand`. Replacing `rand` with a seeded generator makes every run
repeatable from its seed.

Wave patterns are hand written code, not a data table. `gameclass::generatewave` handles 45
pattern ids, and `gamelogic` picks the id from the stage, the wave counter, the run time, the
wall speed, and the side count, using `ofRandom`. Inside a pattern, random numbers pick the base
slot and a few variants, and walls sit at fixed offsets from the base. The harness in
`decomp/generatewave-harness/` compiles Ghidra's C for this function and lists the walls each id
produces for each random draw. Only the case list and one case (id 600) were checked against
the machine code so far, so that table needs checking against the real function before the
simulator relies on it.

Input is a byte array of held keys at `superhex+0x3c07c`. `generickeypoll` rebuilds the left and
right flags from it every tick, and rotation reads held state, not presses. Writing
`keys[0x164]` (left, `superhex+0x3c1e0`) or `keys[0x166]` (right, `superhex+0x3c1e2`) before
`update` is the whole action interface.

Steam has to be faked, not just blocked. Score uploads and achievements are gated on a flag set
only when `SteamAPI_Init` succeeds inside `scoreclass::updatescoreapi`. However, scores and
settings are saved through `SteamRemoteStorage` whenever both of its cloud checks (vtable slots
0x90 and 0x98) return true, and otherwise to `suphex.dat` under `SDL_GetPrefPath`. A training
run that beats a best time would otherwise write the bot's time into the user's cloud save.

## Hook design

### Taking over the loop from `SDL_PollEvent`

The game's loop calls `SDL_PollEvent` once per pass, after `setup()` and one `update()` have run.
The hook's `SDL_PollEvent` takes over on its first call and never returns into the game's loop
while training runs. From inside it, the hook runs its own loop.

```c
for (;;) {
    wait_for_command(shm);                 // step, reset, render, or quit
    for (int i = 0; i < cmd.frame_skip; i++) {
        app->keys[0x164] = cmd.action == LEFT;
        app->keys[0x166] = cmd.action == RIGHT;
        virtual_ticks += 1;                // one 1/60 s tick
        window->lastFrameTime = 1.0 / 60.0;
        app->vtable->update(app);          // same call the game's loop makes
        if (dead(app)) break;
    }
    if (cmd.render) ofAppGlutWindow_display(window, app);   // 0x485ff0, draws one frame
    write_observation(shm, app);
    signal_done(shm);
}
```

This needs no writes to code pages, since the hook calls `update` through the app's vtable (slot
`+0x18`) and `display` at its fixed address. Training skips `display` entirely, so the only cost
per tick is game logic. That should be far faster than real time, but the actual rate is the
first thing to measure. The hook also drains the real `SDL_PollEvent` now and then so the window
stays responsive and a quit request ends the process cleanly.

If taking over from inside `SDL_PollEvent` turns out to break something (for example, state the
game expects to unwind after the poll), the fallback is to patch a jump at the entry of
`runAppViaInfiniteLoop` in a constructor, after checking the binary's SHA-256 and the bytes at that
address.

### Virtual time

| Function | Hook behavior |
|---|---|
| `SDL_GetPerformanceFrequency` | Fixed value such as 60,000,000 |
| `SDL_GetPerformanceCounter` | `virtual_ticks * freq / 60` |
| `SDL_GetTicks` | `virtual_ticks * 1000 / 60` |
| `SDL_Delay` | Returns without sleeping |

Before the takeover, the virtual clock still has to move so that `setup` and the first `update`
behave normally. Advancing it inside `SDL_Delay` and on each counter read covers that.

### Random numbers

The hook exports `rand` and `srand` backed by its own `random_r` state. A reset command carries a
seed, and the hook reseeds right before it presses start, so the same seed and the same actions
give the same run. The menu also consumes random numbers, which is why reseeding at the start of
each run matters instead of seeding once at launch.

### Steam and save data

The hook exports replacements for the Steam API functions the game imports, so the Steam client
doesn't need to run and nothing reaches Steam.

| Function | Replacement |
|---|---|
| `SteamAPI_Init` | True on the first call (from `main`), false after |
| `SteamAPI_RestartAppIfNecessary` | False |
| `SteamAPI_RunCallbacks` | Does nothing |
| `SteamAPI_RegisterCallResult` | Does nothing |
| `SteamAPI_UnregisterCallResult` | Does nothing |
| `SteamRemoteStorage` | Fake object, cloud checks return false |
| `SteamFriends` | Fake object, slot 0 returns a name |
| `SteamUser`, `SteamUserStats` | Fake objects returning zero |

With `SteamAPI_Init` false after the first call, the score flag never gets set and
`reportScore` and `reportAchievement` do nothing. With the cloud checks false, saves go to
`suphex.dat` under `SDL_GetPrefPath`, and each instance gets its own `XDG_DATA_HOME` so nothing
touches `~/.local/share/SuperHexagon`. That folder should also get a settings file that marks the
tutorial done (`superhex+0x557c`), since the first run otherwise plays a tutorial.

### Reset and level select

Resets go through the game's own menu flow, driven by the same key bytes. From level select
(`superhex+0x5424 == -2`), the hook sets the cursor `superhex+0x5464` to the requested level,
reseeds `rand`, and holds select (Enter, `keys[0x0d]` at `superhex+0x3c089`) for one tick,
which starts the run in that same tick. After a death, it waits until the camera distance
`superhex+0x10c` reaches 320 and taps Up to restart the same level, which the input notes put at
roughly 73 ticks. The hook never sends Escape, since Escape on the title screen calls `ofExit`,
and it never selects title submenu 2, which clears scores.

Restoring a saved state for the curriculum (restarting a few seconds before a death) is a later
step. The `superhex` object holds heap pointers (strings, sound players), so a full copy isn't
safe, and the snapshot has to cover only the gameplay fields plus the `rand` state.

### Shared memory protocol

Each game process maps one shared memory block named from an environment variable. It holds a
command (step, reset, render, quit), the action, frame skip, seed, and level, plus the observation
the hook writes back, with a pair of process-shared semaphores for the handoff. The observation is
raw game state, and the Python side builds the egocentric features from it.

| Observation field | Source |
|---|---|
| Walls | Enabled records from `superhex+0x180`, up to `+0x2890` |
| Player angle | `superhex+0x28f0` |
| Sides | `superhex+0x114` |
| Wall speed | `superhex+0x28d8` |
| Rotation mode | `superhex+0x2900` |
| Run time | `superhex+0x2894`, read after `gamelogic` |
| Death timer | `superhex+0x104` |
| Stage mode | `superhex+0x5424` |
| Hyper flag | `superhex+0x5478` |
| Tick counter | Hook's virtual clock |

A Python class wraps one or more of these processes behind a Gymnasium style `reset` and `step`,
which PufferLib can vectorize later.

### Running without a display

During training the hook never draws, but FNA3D still creates a GL device at startup. The bundled
SDL has X11, Wayland, KMSDRM, and dummy drivers and no offscreen driver. So the first options to
try are running under Xvfb or a headless gamescope, and preloading the system SDL2 in place of
the bundled one if it offers an offscreen EGL driver. Audio goes to SDL's dummy driver through
`SDL_AUDIODRIVER=dummy`.

## Build order

1. Write the preload library with the Steam fakes, virtual time, and `rand`, without the loop
   takeover. Check that the game launches outside Steam, runs at normal speed, and writes saves
   only to the instance folder.
2. Add the loop takeover and the shared memory protocol, with a small Python client that steps
   with random actions. Measure ticks per second with rendering off.
3. Add reset and level select through the menu flow, and check that two runs with the same seed
   and the same actions produce identical state traces for a full episode.
4. Add the render command and a viewer, so a policy can be watched at normal speed.
5. Run several instances at once and measure how throughput scales with process count.

## Measured on 2026-10-18

Build step 2 works with the `SDL_PollEvent` takeover, so the jump patch wasn't needed.
`scripts/bench_step.py` drove one game process with random actions for 20 s per run under a two
core systemd quota, with rendering off.

| Frame skip | Steps per second | Ticks per second | Game CPU | Client CPU |
|---|---|---|---|---|
| 1 | 97,978 | 97,978 | 63% | 55% |
| 4 | 41,601 | 165,378 | 85% | 27% |

The game side costs about 5 microseconds per tick, so at frame skip 1 the Python round trip is
about half the cost. `scripts/watch_random.py` renders each step at 60 per second, and its
screenshots showed normal gameplay. A fresh instance folder starts in the first run tutorial
(`superhex+0x5575` set), so these runs include tutorial ticks, and step 3 should start instances
from a save with the tutorial done.

Build step 3 added a reset command that walks the game's own menus to level select, sets the
cursor, reseeds `rand`, and starts the level on the next tick (3 ticks from a game over screen).
Instances now start from `env/template/SuperHexagon`, a save with the tutorial done, the hyper
levels unlocked, windowed mode, no sound, and vsync off. A run keeps the player angle the menu
left it at, which depends on the last level played, so the reset sets both angle fields to the
game's own start value (`180 / sides`). With that, `scripts/check_determinism.py` passed 12 of 12
cases (six levels, two seeds each). Each case compared full episodes tick by tick for a replay in
the same process after other episodes, and for a second process whose clock was 137 ticks out
of phase. A different seed changed the walls from the first tick in every case.

Build step 5 runs instances without a window. The bundled SDL has no offscreen driver, so
`ShxGame(headless=True)` preloads the system SDL2 (`sdl2-compat` 2.32.74 on SDL3 3.4.18) ahead
of the bundled copy and sets `SDL_VIDEODRIVER=offscreen`. FNA3D still gets an OpenGL device on
the RTX 3080 through EGL, the workers' `/proc/<pid>/maps` showed the system library, and the
determinism check passed 12 of 12 cases with its second process headless.

`scripts/bench_parallel.py` ran one Python worker and one headless game per instance for 15 s
per point, under a `CPUQuota=1600%` cap (16 of the 32 threads on the i9-13900K).

| Instances | Ticks/s, skip 1 | Ticks/s, skip 4 | Cores used, skip 1 |
|---|---|---|---|
| 1 | 134,666 | 190,895 | 1.2 |
| 2 | 256,292 | 409,782 | 2.4 |
| 4 | 459,382 | 648,655 | 4.9 |
| 8 | 856,797 | 1,229,952 | 9.5 |
| 12 | 1,112,458 | 1,885,242 | 13.8 |
| 13 | 805,611 | 1,500,974 | 12.9 |

Throughput per instance falls from about 135k to 93k ticks per second at frame skip 1 as games
spread onto hyperthread siblings and the efficiency cores. Other programs were using several
cores during the sweep, and whole machine usage reached 73 to 79% at 13 instances while these
runs stayed at or under 13.8 cores. So 12 instances is the default, and long runs should check
the machine's load first.

## Open questions

- Which code writes `superhex+0x5628`, the start time the mode 4 script subtracts.
- Whether the beat pulse at `superhex+0x2928` affects anything besides drawing.
