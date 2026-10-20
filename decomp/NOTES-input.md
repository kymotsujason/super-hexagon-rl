# Input path notes for Super Hexagon (Linux x86-64)

Sources are the Ghidra exports in this folder, and line numbers refer to those files. A few facts were also checked against the shipped binary with `objdump` and `nm`, marked "(asm)". Each claim is tagged as observed (read straight from code) or inferred (a reading of what the code is for).

All offsets are from the `superhex` object (`this` in every `superhex::` method) unless written as `gc+X`, which means offset X inside the embedded gameclass.

## Embedded objects

| Object | superhex offset | Evidence |
|---|---|---|
| gameclass | `0x18` | `superhex_setup.c` 49, 58 |
| scoreclass | `0x57f0` | `superhex_gameinput.c` 222 |
| graphicsclass | `0xbfc8` | `superhex_prepareGame.c` 15 |
| musicclass | `0x1e4a8` | `superhex_setup.c` 41 |
| helpclass | `0x3bf30` | `superhex_setup.c` 40 |
| deviceclass | `0x3bfa8` | `superhex_prepareGame.c` 14 |
| key state `bool[1000]` | `0x3c07c` | `superhex_keyPressed.c` 7 |

Observed. The gameclass offset is confirmed three ways. `setup` passes `this+0x18` to `gameclass::start` and `gameclass::savesettings`, every `gameclass::` call in gameinput passes `this+0x18`, and `gameclass::restart` writes the frame delta double at `gc+0x28a8` while gameinput reads it at `superhex+0x28c0` (0x18 + 0x28a8).

Inferred. scoreclass is probably a member of gameclass (`gc+0x57d8`), and the key array is likely exactly 1000 bytes, since the next field (`0x3c464`) is at `0x3c07c + 0x3e8`.

Other input storage in `superhex_generickeypoll.c` (observed offsets, inferred meaning):

- `0x3c4fd + i`: controller button i held (loop of 0x1e at 539-541).
- `0x3c6f8 + i`: controller d-pad, i = up, down, left, right (125-137).
- `0x3c568 + 8*i`: controller axis i as double (139-156).
- `0x3c700`: axis deadzone, double (131).
- `0x3c464`, `0x3c465`, `0x3c466`: touch tap, touch held, and a flag that clears the tap at end of update (asm, `superhex::update` at `0x47a4d3`).
- `0x3c468`, `0x3c490`, `0x3c4f8`: touch x array, touch y array, touch count.

## Per tick call order and the frame delta

Observed (asm, `superhex::update` at `0x47a440`): `calculateTime` (call at `0x47a49e`), `helpclass::updateglow`, `gameinput` (call at `0x47a4ba`), `gamelogic` (call at `0x47a4c2`), `musicclass::processmusic`. There's no branch between them.

The step is _not_ a fixed 1.0. `superhex::calculateTime` (asm, `0x470c20`) rewrites the double at `superhex+0x28c0` every tick as the measured last frame time divided by 1/60 (constant at `0x55aca8`), rounded to one decimal by `oneRound` (`0x47c2f0`, multiply by 10, add 0.5, truncate, divide by 10). `update` clamps a frame time of two nominal frames or more back to nominal (`0x47a46f`), and a NaN, zero, or over 1 s frame also falls back to nominal. So `dt` sits around 1.0 at 60 fps and stays under 2.0. `gameclass::restart` seeds it from `getExpectedFrameDelta()` (`gameclass_restart.c` 38-40), whose default is 1.0f in `.data` at `0x7f48a8` (asm), but `calculateTime` overwrites it the next tick. `NOTES-core.md` shows that the loop in `runAppViaInfiniteLoop` always hands `update` exactly one period.

Inferred. For a deterministic env, write `1.0` to `superhex+0x28c0` at gameinput entry, since gameinput and gamelogic both read it after `calculateTime` has run.

## Rotation inputs

### Logical input flags

`generickeypoll` (called first thing in gameinput, line 36) zeroes `0x41` through `0x48` (generickeypoll.c 34-41) and rebuilds them from the key array each tick. Observed meanings:

| Flag | Role | Keys (keycode, byte) |
|---|---|---|
| `0x43` | rotate left, menu left | Left `0x164` @`0x3c1e0`, `0x41` @`0x3c0bd`, `0x61` @`0x3c0dd`, `0x15` @`0x3c091`, `0x66` @`0x3c0e2` |
| `0x44` | rotate right, menu right | Right `0x166` @`0x3c1e2`, `0x44` @`0x3c0c0`, `0x64` @`0x3c0e0`, `0x4c` @`0x3c0c8`, `0x6c` @`0x3c0e8`, `0x16` @`0x3c092`, `0x67` @`0x3c0e3` |
| `0x41` | up, also restarts | Up `0x165` @`0x3c1e1`, `0x57` @`0x3c0d3`, `0x77` @`0x3c0f3` |
| `0x42` | down | Down `0x167` @`0x3c1e3`, `0x53` @`0x3c0cf`, `0x74` @`0x3c0f0` |
| `0x45` | select | `0x5a` @`0x3c0d6`, space `0x20` @`0x3c09c`, `0x56` @`0x3c0d2`, Enter `0x0d` @`0x3c089` |
| `0x47` | back | Escape `0x1b` @`0x3c097` |
| `0x48` | leaderboards | Tab `0x09` @`0x3c085` |

Sources are generickeypoll.c 42-52 (left and right), 85-102 (the rest). The left and right byte offsets match the disassembly of `generickeypoll` (asm, `0x46b786` through `0x46c48a`).

In ASCII the left group is Left, `A`, `a`, `0x15`, `f`, and the right group is Right, `D`, `d`, `L`, `l`, `0x16`, `g`. Up is Up, `W`, `w`. Down is Down, `S`, `t`. Select is `Z`, space, `V`, Enter. SDL keycodes for letters are lowercase, so the uppercase entries only fire if the window layer delivers uppercase codes. The arrow codes `0x164` and `0x166` are unambiguous. Gameinput also reads `C`/`c` (`0x3c0bf`, `0x3c0df`, line 535) and Backspace (`0x3c084`, line 1327) directly.

When both left and right are held, the newer press wins. Observed: `0x3c074` and `0x3c078` count frames held for the left and right groups (-1 when released), and when both are set the smaller count wins (generickeypoll.c 53-84). The controller uses `0x3c708` and `0x3c70c` the same way (157-188), and touch uses `0x3c064` and `0x3c06c` (485-518).

### Controller

Observed, generickeypoll.c 103-189, active when `this[0xb8]` (controller enabled) is set and `*(int*)(this+0xb4)` (mapping mode) is 0:

- Left (`0x43`): button 4 (`0x3c501`), d-pad left (`0x3c6fa`), axis 1 below minus deadzone, axis 4 above deadzone.
- Right (`0x44`): button 5 (`0x3c502`), d-pad right (`0x3c6fb`), axis 1 above deadzone, axis 4 below minus deadzone.
- Select: buttons 0, 3, 7. Back: buttons 1, 2. Tab: button 6. Up and down: d-pad up and down, axis 0.

Mode 1 (190-471) is a custom mapping held as strings at `0x60`..`0x90` (prefix `b` for button, `p` for hat, `+` and `-` for axis) with indices at `0x98`..`0xb0`, which depend on `flibitButtonMap`.

### Where movement happens

Observed, `superhex_gameinput.c` 227-299. It runs only when `*(float*)(this+0x104) == 0.0` (line 72) and `*(int*)(this+0x5424) >= -1` (line 227):

- `0x43` held: `angle = (int)(prevAngle + speed * dt)` and the int at `0x55a8` is set to 1 (lines 228-235).
- `0x44` held: `angle = (int)(prevAngle - speed * dt)` and the int at `0x55a8` is set to -1 (237-244).
- Then wrap into 0..359 (295-299).

| Field | superhex | gameclass | Type | Meaning |
|---|---|---|---|---|
| angle | `0x28f0` | `gc+0x28d8` | int | player angle, degrees |
| prevAngle | `0x28ec` | `gc+0x28d4` | int | last tick's angle |
| speed | `0x28f4` | `gc+0x28dc` | int | degrees per frame |
| dt | `0x28c0` | `gc+0x28a8` | double | frame delta, about 1.0 |
| segment | `0x13c` | `gc+0x124` | int | angle / (360 / sides) |
| sides | `0x114` | `gc+0xfc` | int | 6 by default |

Positive rotation (angle increasing) is the left key. Speed is 7 for stage 0 and stage 1 and 9 for stage 2 (gameinput 1101, 1135, 1168), and `gameclass::changetostage` sets 9 or 7 when hyper stages change (`gameclass_changetostage.c` 46, 86). Gamelogic sets 4 on the level select screen (`superhex_gamelogic.c` 1749). The move is `speed * dt` degrees per tick.

prevAngle is written by gamelogic at the end of the collision block: `*(int*)(this+0x28ec) = angle` (gamelogic 349). If the new angle runs into the side of a long wall, gamelogic puts the old angle back (`0x28f0 = 0x28ec`, line 329), which is the wall push back. Truncation to int means the left and right step can differ by one degree when `dt` isn't a whole number (inferred from the cast at gameinput 230 and 239, since `(int)(100 + 6.65)` is 106 but `(int)(100 - 6.65)` is 93).

Two side effects (observed). The float `0x28fc` is a lean that follows the input and is clamped to +-10 (233, 242, 276-294). If `this[0x55a4]` is set and no key is held, the player keeps sliding in direction `0x55a8` until reaching the centre of a segment and then snaps (246-275). `0x55a4` is set somewhere not found yet.

Escape during a run (observed, 301-310): if `0x47` is set and `this[0x50]` is clear, it sets `*(float*)(this+0x28f8) = 2.0`, `this[0x50] = 1`, `this[0xbfb8] = 1`, which forces the death path and mutes the death sound (gamelogic 199-204).

## Menu state machine

### Main state fields

| Field | Type | Meaning (inferred from use) |
|---|---|---|
| `0x5424` (`gc+0x540c`) | int | `-1` title menu, `-2` level select, `0`..`2` a stage, `4` special |
| `0x5478` | byte | hyper mode flag |
| `0x5460` | int | title submenu id |
| `0xbfc0` | int | menu cursor |
| `0x5464` | int | level select cursor and secondary cursor |
| `0x104` (`gc+0xec`) | float | 0 while playing, ramps after death |
| `0x10c` (`gc+0xf4`) | int | camera distance, 40 in play, 320 in menu |
| `0x54` | int | screen kind, 0 in game |
| `0x50` | byte | wait until all keys released |
| `0xbc` | float | menu key repeat timer |
| `0xbfbc` | int | platform menu style, 0 on PC |

Observed for `0x5424`: `gameclass::stageselect` writes `-2` to `gc+0x540c` and calls `restart` (asm, `0x441594` and `0x44159e`). Going back to title writes `-1` (gameinput 212, 1016). Choosing a level writes 0, 1, or 2 (162, 169, 196). Gamelogic checks `== 4` together with `0x5584 == 3` for the secret ending (gamelogic 350).

Observed for `0x104`: `restart` zeroes `gc+0xec` (`gameclass_restart.c` 29). Death sets it to 1.0 (gamelogic 447), and it then climbs by `dt` up to 100 (185-188). Menu transitions set it to 60.0 (gameinput 205, 217, 1021). Gameplay rotation runs only when it's 0 (line 72).

Observed for `0x10c`: while `0x104 <= 0` it drops by 20 per tick down to 40 (gamelogic 177-182). After death, once `0x104 >= 60`, it rises by 20 per tick and is clamped at 320 (257-285). Start and restart need it to equal 320 (gameinput 1062).

### Title menu (`0x5424 == -1`)

This handler runs only when `0x104 != 0`, through the `LAB_0046d350` path. Observed, by `0x5460`:

- `0`, main menu (914-1006, PC when `0xbfbc == 0`): left and right cycle `0xbfc0` over 0..2. Select on 0 calls `stageselect` (level select), on 1 opens options (`0x5460 = 4`), on 2 sets `0x5460 = 1`. Escape here calls `ofExit(0)` (1004-1005), so a hook must never send Escape on the title screen.
- `1` (406-545): extras with left and right cycling `0x5464`. Select can start the secret ending when `0x5450 == 1` (430-446). `C`, `c`, or `0x49` go to state 2, Escape goes to state 0.
- `2` (546-576): select calls `gameclass::clearscore` and `savesettings` (549-552). This looks like a "clear scores" confirm, so a hook must never send select here.
- `3` (577-609): a two item toggle.
- `4` (610-711): options for fullscreen, vsync, music volume, sound volume, arcade mode (`0x57d0`), and clear scores.
- `5`, `6`, `7`: leaderboards. `8`: controller setup (`0xb8`, `0xb4`, `0xb9` is "waiting for a button"). `9`: arcade name entry. `10`: a message screen.

### Level select (`0x5424 == -2`)

Observed, gameinput 74-226. It runs when `0x104 == 0`, which holds because `stageselect` calls `restart`. With `0xbc <= 0`, left sets `0x5468 = -10` and right sets `+10`, and the cursor `0x5464` moves by one per completed 60 degree turn (left is +1 mod 6, right is +5 mod 6). Key repeat is 8 frames (`0xbc = 8.0`, line 85). Select picks a stage from the cursor (156-206):

| Cursor | Result |
|---|---|
| 0 | stage 0, Hexagon |
| 5 | stage 1, Hexagoner |
| 4 | stage 2, Hexagonest |
| 3 | stage 0 hyper, needs `0x5430 == 1` |
| 2 | stage 1 hyper, needs `0x5434 == 1` |
| 1 | stage 2 hyper, needs `0x5438 == 1` |

A locked choice sets `0x140 = 1` and does nothing. An accepted choice sets `0x104 = 60.0` and `0x10c = 320`. Then, in the same `gameinput` call, control reaches the start block at 1060, because `0x45` is still set, `0x10c == 320`, and `0x5424 >= 0`. So one select press on level select starts the run in that same tick. Escape on level select goes back to title (1014-1026).

### Start and restart block

Observed, gameinput 1060-1194. It fires when all of these hold:

- `0x41` (up) or `0x45` (select) is set.
- `this[0xc9] == 0` (meaning unknown).
- `0x10c == 320`.
- `0x5424 >= 0`.

It doesn't check `0x50`. It sets `0x54 = 0`, calls `gameclass::restart` (zeroes `0x104` so play begins, zeroes the walls and the timer), starts the music, and sets speed and wall pattern per stage, with the hyper variants calling `restarthyper`.

### Restart after death

Observed. After death, `0x5424` still holds the stage index and `0x104 != 0`, so each tick flows through `LAB_0046d4c8` (1010-1195) with these results:

- Up or select restart the same stage once `0x10c` reaches 320 (1060).
- Escape returns to level select with the cursor on the stage just played (1028-1042). This check doesn't wait for `0x10c`.
- Tab opens leaderboards (1044-1059).
- Left and right do nothing here.

Inferred timing. `0x104` needs about 59 ticks to go from 1 to 60, and `0x10c` then needs 14 ticks to go from 40 past 320, so restart becomes possible about 73 ticks (around 1.2 s at 60 fps) after the death tick.

### Tutorial

Observed. The first start with `0x557c == 0` sets `this[0x5575] = 1` and `0x5578 = 0` (gameinput 1080-1089), which is the tutorial. Its phases run in gamelogic 1756-1822, and it waits for a left then right press through `0x5580` (gameinput 48-70). Phase 4 clears `0x5575`, sets `0x557c = 1`, and saves (gamelogic 1815-1821). While `0x5575` is set, death restarts automatically with no game over screen (gamelogic 294-303), and best time tracking is off (83). Inferred: an install that has already been played has `0x557c = 1` in the save.

## Injecting actions

Write the key array bytes before `gameinput` runs, `superhex+0x3c1e0` for Left (0x164) and `superhex+0x3c1e2` for Right (0x166), set to 1 for held and 0 for released. The reasons are below (observed unless marked).

- `generickeypoll` rebuilds `0x41`..`0x48` from the key array at the start of every gameinput, so writing `0x43` and `0x44` before gameinput gets overwritten.
- The key array only changes in `keyPressed` and `keyReleased`, so a byte stays set until it's cleared. A real keyboard event for the same key will overwrite it, so don't press arrows on the real keyboard while the hook drives.
- Gameplay rotation is level triggered. A held byte rotates every tick with no edge detection.
- Set only one of the two bytes. If both are set, the press order counters at `0x3c074` and `0x3c078` decide the winner, which would make a "both" action depend on history.
- `gameinput` is at `0x46d200` (asm), and on entry `rdi` is the superhex pointer. Writing `dt = 1.0` at `superhex+0x28c0` in the same place makes the step deterministic (inferred).

Writing the angle (`0x28f0`) directly skips the speed limit, the lean, and the side wall push back, so use it only for resets or tests, and write `0x28ec` to match. Synthesizing SDL events goes through the same `keyPressed` write, but its latency relative to `update` is unverified.

### Edge and latch logic that affects byte writes

- The `0x50` latch (observed, generickeypoll.c 925-940): while `this[0x50]` is set, every flag in `0x41`..`0x47` is forced to 0 until a tick where all of them read 0, which then clears the latch. Many menu actions set it, and so does Escape during a run (gameinput 308). After any of those, the hook must send one tick with no keys before the next input counts. Starting a run from level select or from the restart block doesn't set it (156-206, 1060-1194).
- Menu left and right repeat every 8 frames while held (`0xbc`), so for menu navigation a byte held for 1 tick is one step.
- Holding select or up through the game over screen restarts the run automatically as soon as `0x10c == 320`, since that path doesn't check `0x50` or edges. That's the simplest reset.
- Escape held on the title screen exits the game, and select in title submenu 2 clears the saved scores. A hook should only ever send left, right, and select (or up), and only while `0x5424 >= 0` or `== -2`.

## Death detection

Observed, gamelogic 312-349 and 418-450.

- Walls live in an array at `superhex+0x184` with a 0x14 stride (`gc+0x16c`, 500 slots, cleared in `gameclass_restart.c` 10-18). The count is at `superhex+0x2890`. Per wall: side at `-4`, distance at `+0`, length at `+4`, active byte at `+0xc`.
- A wall that's active, on the player's segment (`0x13c`), and at distance 145 minus wall speed up to 150 sets the hit float `*(float*)(this+0x28f8) = 1.0` (lines 326-344). `0x28f8` is `gc+0x28e0`, it decays by `dt` each tick (308-311), and Escape sets it to 2.0.
- If `0x28f8 >= 1.0`, `0x104 == 0`, and `0x546c != 1`, the run is over. Gamelogic plays effect 4, sets `0x104 = 1.0` (441-450).
- Survival time is `*(float*)(this+0x2894)` (`gc+0x287c`), in frames, increased by `dt` while alive (gamelogic 87-94) and zeroed by `restart`. In hyper mode gamelogic adds 3600 near the end of the tick and subtracts it again at the very end (532-533, 1853-1854). So read it after gamelogic, or between ticks.
- Inferred signals for the env:
  - Alive in a run: `0x5424 >= 0` and `0x104 == 0.0`.
  - Died this tick: `0x104` changes from 0 to nonzero (or `0x28f8 >= 1.0` right after gamelogic).
  - Can restart: `0x10c == 320` and `0x104 >= 60`.
  - Reward: the change in `0x2894`.

## Open items

- Where `0x55a4` (snap to centre) and `0xc9` (start blocker) are set.
- What produces key codes `0x15` and `0x16`, and whether the `f`, `g`, `t`, and uppercase entries are reachable on Linux.
