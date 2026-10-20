# The LD_PRELOAD hook

`hook/shxhook.so` turns the native Linux build of Super Hexagon into a steppable environment. It's
an `LD_PRELOAD` library built from `hook/shxhook.c` and never patches the game binary. The game logic
already runs in fixed 1/60 s steps, so calling `superhex::update` once per tick reproduces normal
play exactly. The hook only works with one build (SHA-256 `6f13c58d...2ba1d`, buildid 8838351, a
non-PIE x86-64 ELF), since it uses fixed addresses and checks the `superhex` vtable at startup.
`env/shxgame.py` also checks the binary's hash before launching.

## What the hook replaces

- `SDL_PollEvent` is where the hook takes over. On its first call from the main thread the hook
  runs its own loop and never returns into the game's loop. When the loop ends it clears the
  window's running flag, so the game shuts down normally.
- `SDL_GetPerformanceFrequency`, `SDL_GetPerformanceCounter`, `SDL_GetTicks`, `SDL_GetTicks64`, and
  `SDL_Delay` serve a virtual clock. The frequency is 60,000,000, and once the hook owns the loop the
  counter moves 1,000,000 counts per `update` tick. `SDL_Delay` returns at once on the main thread
  and still sleeps on other threads such as audio.
- `rand` and `srand` run on the hook's own `random_r` state, which a reset reseeds, so the same
  seed and actions give the same run.
- The Steam API functions and the `SteamRemoteStorage`, `SteamFriends`, `SteamUser`, and
  `SteamUserStats` accessors return fakes, so the Steam client doesn't need to run.

`superhex::draw()` normally stores the elapsed time that `gamelogic` reads, so each hook tick refreshes
that field (`superhex+0x3c710`) from the virtual clock, since training never draws.

### Steam and save data

`SteamAPI_Init` returns true on the first call, since `main` needs it, and false after. The game
retries it each tick in `scoreclass::updatescoreapi` and only enables score uploads and achievements
when it gets true, so a bot's time can't reach a leaderboard. The fake `SteamRemoteStorage` fails
its two cloud checks (vtable slots 0x90 and 0x98), so saves go to `suphex.dat` under
`SDL_GetPrefPath`. The hook refuses to start unless `SHX_DATA_DIR` is an absolute path, and it points
`XDG_DATA_HOME` there, so the real save is never touched.

## Lockstep protocol

The client creates a shared memory block named in `SHX_SHM`, initializes two process-shared
semaphores in it, and starts the game. The block is the `ShxShared` struct in `hook/shxproto.h`, with
a magic number, version (5), and size checked on both sides. It holds the command, the result, the
observation, up to 256 walls, and up to 32,768 replay actions.

The client writes a command and posts `commandReady`, then the hook runs it, fills in the result, and
posts `resultReady`. While waiting, the hook drains real SDL events and checks that the client is
alive. The commands are:

- Step holds the given keys (left, right, or none) for `frameSkip` ticks, from 1 to 600, and stops
  early on the tick a death starts.
- Reset walks the game's own menus to level select, sets the cursor, reseeds `rand`, and presses
  select to start the level.
- Replay does a reset, then plays recorded actions back one decision at a time, and reports an error
  if the player dies.
- Dump writes the whole `superhex` object to a `.dump` file beside the shared memory block.
- Quit ends lockstep.

When `render` is set, the hook draws one frame after the command.

### Reset

Reset presses the same key bytes a player would. After a death it waits for the camera distance to
reach 320 before sending Escape, which is only safe during a run since Escape on the title screen
quits. A run keeps the angle the menu left the player at, so reset sets both angle fields to
`180 / sides` unless told otherwise. The flags are:

- `SHX_RESET_KEEP_RANDOM` skips the reseed.
- `SHX_RESET_KEEP_ANGLE` keeps the menu's angle, the way a live game starts.
- `SHX_RESET_STOP_AT_SELECT` stops at level select.

Reset fails after 2,000 ticks, on a locked level, and once the game reaches its ending (passing 60 s
on Hyper Hexagonest), where the client has to relaunch the game.

## The observation

The C core in `hook/shxcore.h` builds 45 floats after every command. The simulator shares it, and
`env/shxenv.py` is the Python reference it's checked against. Slots are relative to the player's
slot, and times use the wall speed times 60 as the speed per second.

- Six slots of five values each. Every slot has a present flag, then the start time and length of its
  two nearest walls, where the start is `(distance - 145) / speed`. Starts are clamped to -1 to 3,
  lengths to 0 to 3, and missing walls read as 3 and 0.
- The player's position inside their slot, from -0.5 to 0.5.
- A one-hot for the side count, 4 to 6.
- The wall speed divided by 40.
- A one-hot for the level (six values) and one for the last action (none, left, right).
- How long the last action has been held, in seconds, clamped to 0 to 1.

The hold count restarts on a change of action and resets to zero on a reset. A replay counts it the
same way, so a replayed state matches the live one.

## Live mode

Without `SHX_SHM` the hook never opens shared memory, so `SDL_PollEvent` passes straight through and
the game keeps its own loop. The clock is real time scaled by `SHX_SPEED` (default 1), and the Steam
fakes, seeded `rand`, and data folder redirect still apply. `scripts/play_live.py` uses this to run
the real game with real key events and audio, reading its state from the game's memory.

## Headless runs and instance saves

The bundled SDL has no offscreen driver. With `headless=True`, `ShxGame` preloads the system SDL2
(`sdl2-compat` 2.32.74 on SDL3 3.4.18) and sets `SDL_VIDEODRIVER=offscreen`. Each instance starts from
`env/template/SuperHexagon`, a save with the tutorial done, the hyper levels unlocked, windowed mode,
no sound, and vsync off.

## Measured speed

`scripts/bench_step.py` drove one game process with random actions for 20 s per run under a two core
systemd quota, with rendering off, on 2026-10-18. These runs include tutorial ticks.

| Frame skip | Steps per second | Ticks per second | Game CPU | Client CPU |
|---|---|---|---|---|
| 1 | 97,978 | 97,978 | 63% | 55% |
| 4 | 41,601 | 165,378 | 85% | 27% |

The game side costs about 5 microseconds per tick, so at frame skip 1 the Python round trip is about
half the cost. `scripts/bench_parallel.py` ran one Python worker and one headless game per instance
for 15 s per point, under a `CPUQuota=1600%` cap (16 of the 32 threads on the i9-13900K).

| Instances | Ticks/s, skip 1 | Ticks/s, skip 4 | Cores used, skip 1 |
|---|---|---|---|
| 1 | 134,666 | 190,895 | 1.2 |
| 2 | 256,292 | 409,782 | 2.4 |
| 4 | 459,382 | 648,655 | 4.9 |
| 8 | 856,797 | 1,229,952 | 9.5 |
| 12 | 1,112,458 | 1,885,242 | 13.8 |
| 13 | 805,611 | 1,500,974 | 12.9 |

Throughput per instance falls from about 135k to 93k ticks per second at frame skip 1 as games
spread onto hyperthread siblings and the efficiency cores. Other programs were using several cores
during the sweep, so 12 instances is the default.

## Determinism

`scripts/check_determinism.py` passed 12 of 12 cases (six levels, two seeds each). Each case compared
full episodes tick by tick for a replay in the same process after other episodes, and for a second
process whose clock was 137 ticks out of phase. A different seed changed the walls from the first
tick in every case, and the same cases passed with the second process headless.
