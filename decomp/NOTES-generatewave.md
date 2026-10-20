# Notes on gameclass::generatewave(int)

Source analyzed: `gameclass_generatewave.c` (21,014 lines, Ghidra output, function at `0x421bc0`). Line numbers below refer to that file unless another file is named. Each claim is tagged as observed (read in the decomp, the disassembly, or harness output) or inferred.

## Method

I read the dispatch and a sample of cases by hand, then compiled an unmodified copy of the decompiled function into a test harness to enumerate every outcome. The copy only renames `this` to `self`, swaps the C++ header for a C one, and adds typedefs (`gameclass` as `unsigned char`, `undefined4` as `uint32_t`). The harness stubs `ofRandom(x)` with a depth-first enumerator that returns `choice + 0.5` and records the range `x`, and it reimplements `addenemy` from `gameclass_addenemy.c`. It then runs every id from 0 to 2000 and dumps the wall array and the touched fields after each call.

The harness lives at `/tmp/shx/gw/` (`harness.c`, `gw.c`, `summarize.py`, and `out_<ID>.txt` per id). That folder is in `/tmp`, so copy it somewhere permanent if it's needed later. Build and run it with these commands:

```bash
cd /tmp/shx/gw
gcc -O1 -o harness harness.c gw.c
./harness scan
./harness 101 20 100000
python3 summarize.py
```

The harness measures Ghidra's decompilation, not the shipped machine code. To check it against the binary I compared the list of case constants with the disassembly (`objdump -d` of `0x421bc0`, every `cmp $X,%esi`) and hand checked case 600 instruction by instruction. Both matched. The remaining ids haven't been checked against the machine code.

## Overall structure

The int parameter is a wave or pattern id (observed). The function is one big switch that GCC compiled as a binary search tree of compares, with no jump table (observed in the disassembly at `0x421bc2` onwards and in the decomp at lines 22, 756, 1155, 3940, 5373, 8165, 11890, 12053, 17916, 19286). There are 45 handled ids, and any other id falls to `LAB_004221b0` (line 12046), which spawns nothing.

The handled ids, grouped by family, are as follows:

- `0`, `100`, `106` (0x6a), and `601` (0x259), which are handled but never passed by any caller in the binary (observed, all 67 direct call sites are in `superhex::gamelogic`, and none of them load these constants)
- `90` to `95` (0x5a to 0x5f), the "opening" family
- `101` to `113` (0x65 to 0x71), `120`, `121`, `123`, `125` (0x78, 0x79, 0x7b, 0x7d), the big hexagon patterns
- `200`, `201`, `202`, `205` (0xc8, 0xc9, 0xca, 0xcd), short single-row fillers for 6 sides
- `300`, `301`, `302` and `500`, `501`, `502`, the same ideas written for 5, 4, and 3 sides
- `400` to `403`, `406`, `407` (0x190 to 0x193, 0x196, 0x197), side-count transition waves
- `600` (0x258), `1000` (0x3e8), `1001` (0x3e9), special sequences

The caller passes `0x195` (405) for the 3-sided transition (`superhex_gamelogic.c` line 1295), but generatewave has no case for it (observed in both the harness scan and the disassembly case list). That call takes the default path, spawns nothing, and leaves the wave timer at the 0 the caller just wrote, so a new wave gets picked on the next frame.

generatewave doesn't choose the pattern itself. It never reads the level, the side count (`gameclass+0xfc`), or the survival time (observed, the only fields it reads are listed under state written below). The side count is baked into the id, since the 5, 4, and 3 sided families use `% 5`, `% 4`, and `% 3` on their slot math in place of `% 6` (191 uses of `% 6`, 25 of `% 5`, 26 of `% 4`, 12 of `% 3`).

## Who picks the id

`superhex::gamelogic` holds the gameclass at `this + 0x18` (`superhex_gamelogic.c` line 75), so every superhex offset there is the gameclass offset plus 0x18 (observed). The selection logic reads the following gameclass fields (offsets converted):

| Gameclass offset | Superhex offset | Apparent meaning |
|---|---|---|
| `0x540c` | `0x5424` | stage index |
| `0x28b8` | `0x28d0` | wave counter |
| `0x287c` | `0x2894` | survival time |
| `0x28c0` | `0x28d8` | wall speed |
| `0xfc` | `0x114` | side count |
| `0x158` | `0x170` | side change state |
| `0x545c` | `0x5474` | resume flag |
| `0x5608` | `0x5620` | stage 4 clock |

The wave counter drives the structure (opening wave at counter 0, `0x5a` for stage 0 at line 1151 and `0x5b` for stage 1 at line 1413, filler `200` for early waves, then `ofRandom(100)` and `ofRandom(N)` rolls into the pattern ids, lines 1094 to 1330 for stage 0). Mirror pairs are chosen in the caller with `ofRandom(100) < 51` (for example `0x6e` versus `0x6d` at lines 823 to 828). Survival time above 3600 widens some rolls from `ofRandom(5)` to `ofRandom(6)`, and speed at or above 26.0 swaps `0x69` for `0x6c` (lines 1203 to 1212). Side count picks the 300, 400, and 500 families (lines 1223 to 1324). The stage index 4 branch picks pattern pools by a 64-bit time difference (`superhex+0x3c710` minus `superhex+0x5628`, line 588) compared against thresholds from 24,500 up to 168,000, which looks like a millisecond clock (inferred). I didn't trace the caller fully, so treat this section as a summary.

## Pattern shapes

Walls are 6, 5, 4, or 3 sided rings of records (slot, distance, width, type). Distances below are written the way `addenemy` takes them (raw stored value minus 4000). Most cases inline `addenemy`, so the decomp shows the raw values (4000 for 0, 0x1450 for 1200, and so on). Every pattern writes type 0 (observed across all 3,053 enumerated leaves). Widths are 200 for a normal wall, with longer widths for the long bars in some patterns (300 to 5400). Slots are a random base `b` plus a fixed offset mod N, except in a few fillers that use absolute slots.

The harness found 3,053 random leaves across the 45 ids and 428 distinct (delay, wall set) outcomes. Each 6 sided pattern with a single `ofRandom(6)` has 6 rotations, and the 2 fold symmetric ones (`0x67`, `0x6d`, `0x6e`, `0x78`, `0x79`, `0x7d`) only produce 3 distinct wall sets. The pairs `0x6d`/`0x6e`, `0x70`/`0x71`, and `0x78`/`0x79` are exact mirror images of each other (observed by testing slot reflection over the harness output).

### Id 0x65, three rows with an alternating gap

This is from lines 1315 to 1772 and the harness output. The slot offsets `+7` and `+8` appear in the decomp exactly like that (line 1589, `(iVar3 + 7) % 6`), which reads like hand written source.

```c
b = (int)ofRandom(6.0f);                         // also stored in +0x2930
for (o in {0, 1, 2, 4, 5}) addenemy((b + o) % 6, 0,    200, 0);   // gap at b+3
for (o in {3, 4, 5, 7, 8}) addenemy((b + o) % 6, 1200, 200, 0);   // gap at b
for (o in {0, 1, 2, 4, 5}) addenemy((b + o) % 6, 2400, 200, 0);
delay_base = 200.0f;                             // line 1771
```

### Id 0x6d, the double spiral

This is from the harness output for this case (decomp starts at line 5373, delay at line 5924).

```c
b = (int)ofRandom(6.0f);
addenemy((b+1)%6, 0, 300, 0); addenemy((b+2)%6, 0, 600, 0);
addenemy((b+4)%6, 0, 300, 0); addenemy((b+5)%6, 0, 600, 0);
// then a pair of opposite walls every 300 units, stepping one slot each time
k = 0;
for (d = 600; d <= 2400; d += 300, k++) {
    w = (d == 2100) ? 600 : 300;
    addenemy((b + k) % 6,     d, w, 0);
    addenemy((b + k + 3) % 6, d, w, 0);
}
delay_base = 180.0f;
```

### Id 300, five sided variant picker

This case starts at line 13627. Unlike the 6 sided patterns, the variant draw comes first and the base draw second.

```c
v = (int)ofRandom(5.0f);
b = (int)ofRandom(5.0f);
switch (v) {
case 0: for (o in {0, 3}) { addenemy((b+o)%5, 0, 200, 0); addenemy((b+o)%5, 400, 200, 0); }
        delay_base = 70;  break;
case 1: for (o in {1, 2, 3, 4}) addenemy((b+o)%5, 0, 200, 0);              delay_base = 60;  break;
case 2: for (d in {0, 400}) for (o in {0, 2, 4}) addenemy((b+o)%5, d, 200, 0); delay_base = 60; break;
case 3: for (o in {0, 1, 2, 4}) addenemy((b+o)%5, 0, 200, 0);              delay_base = 60;  break;
case 4: three rows of 4 at d = 0, 1000, 2000 with the gap moving b+3, b, b+3; delay_base = 160;
}
```

### Ids 0x5a to 0x5f, the opening family

These share one body (`LAB_00427a14`, lines 4436 to 4499) and differ only in a distance offset stored in `gameclass+0x28c4`. The offsets are -700 for 0x5a (line 4435), -565 for 0x5b (line 3944), 50 for 0x5c (line 3949), -50 for 0x5d (line 4501), -50 for 0x5e (line 2297), and 375 for 0x5f (line 3114). Ids 0x5d and 0x5e produce identical output in the harness.

```c
off = <per id>;  gc->f28c8 = 100.0f;
b = (int)ofRandom(6.0f);
addenemy(b % 6, off, 200, 0); addenemy((b+3) % 6, off, 200, 0);
b = (int)ofRandom(6.0f);
for (o in {0, 2, 4}) addenemy((b+o) % 6, off + 1000, 200, 0);
v = (int)ofRandom(11.0f);                        // absolute slots, not rotated
row = v < 4 ? {0,2,4} : v < 7 ? {1,3,5} : v == 7 ? {0,1,2,4} : v == 8 ? {1,3,4,5}
    : v == 9 ? {1,2,3,4,5} : {0,1,2,4,5};
for (s in row) addenemy(s, off + 2000, 200, 0);
delay_base = 200.0f;                             // shared tail LAB_00425c3b, line 754
```

Id 200 is the same `ofRandom(11)` table on its own, as a single row at distance 0 with a base delay of 60.

### Transition and marker walls

Some patterns add a wall with width 1 and a slot outside the board (observed). `superhex::gamelogic` treats any slot of 10 or more as a trigger when its distance reaches 0 (`superhex_gamelogic.c` lines 384 to 400). Slot 20 sets `gameclass+0x158 = 1` and clears `gameclass+0x5464`, slot 21 sets `gameclass+0x158 = 2` and clears `gameclass+0x5464`, and slot 22 sets `gameclass+0x53e0 = 1`. These are the meanings I'd infer for each:

- Slot 20 appears in ids 400 to 403 at distance 0 (raw 4000), and probably starts a decrease in sides, since the caller subtracts one from the side count while `0x158 == 1` (line 1227).
- Slot 21 appears in ids 406 and 407, and probably starts an increase in sides. Those ids fill every slot of a 4 or 5 sided board in three rows at 800, 1200, and 1600.
- Slot 22 appears at distance -1000 (raw 3000) in ids 0x67, 0x68, 0x6f, 0x70, and 0x71. Its flag `gameclass+0x53e0` is also set at palette changes in the caller, so it's likely a visual or rotation event (unconfirmed).

## Sources of randomness

All randomness goes through `ofRandom(float)`, and the function never calls `rand()` directly (observed, 68 `ofRandom` calls and no `rand()` in the decomp). Every result is truncated with `(int)` right away (observed, every line after an `ofRandom` call is an `(int)fVar13` cast). The call patterns are as follows:

- `ofRandom(6)` as the base slot of a 6 sided pattern, in every id from 0x65 to 0x7d, plus id 0, and twice (two rows) in the 0x5a family. Stored in `gameclass+0x2930`.
- `ofRandom(5)`, `ofRandom(4)`, `ofRandom(3)` as the base slot in the 5, 4, and 3 sided families (300 and 500 use 5, 301 and 501 use 4, 302 and 502 use 3).
- `ofRandom(11)` as a weighted pick of an absolutely positioned filler row, in id 200 and the last row of the 0x5a family.
- `ofRandom(4)` (id 202), `ofRandom(5)` (ids 300 and 301), `ofRandom(4)` (id 302), and `ofRandom(2)` (id 205) as a variant selector drawn before the base slot.
- `ofRandom(6)` as a combined variant and base in id 201, where results 0 to 4 pick a fixed absolute shape and result 5 draws a second `ofRandom(6)` for a rotated 5 wall ring.
- `ofRandom(100)` once in id 501 (`< 0x33`), which picks between two mirror images.

The draw count per call is fixed per id and path. It's 0 for ids 400 to 407, 600, 601, 1000, and 1001, 1 for most 6 sided patterns, 2 for 100, 202, 205, the 300 family, and 501, 1 or 2 for 201, and 3 for the 0x5a family. There are no repeat counts or loops driven by randomness (observed, the harness never hit its 64 draw cap and the largest tree is 400 leaves, for id 501).

`ofRandom` itself is `cvtsi2ss(rand()) * x * 2^-31` in single precision (disassembly at `0x486720`, constant `0x30000000` at `0x596e48`, which is exactly 2^-31). Since `cvtsi2ss` rounds, any `rand()` result of 2^31 - 64 or more becomes 2^31 and `(int)ofRandom(6)` returns 6, which is out of range (about 3 in 100 million per draw, inferred from float rounding). Most cases wrap it with `% N`, but a sim should copy the float math exactly in place of `rand() % N`.

No `srand` is imported by the binary, and neither bundled library (`libSDL2-2.0.so.0`, `libsteam_api.so`) imports `srand`, `srandom`, or `rand` (observed with `objdump -T`). If nothing else in the process seeds glibc, the sequence starts as if `srand(1)` was called (inferred). Every `ofRandom` call anywhere in the game advances the same stream, and the binary has 154 call sites in total, so the caller's selection rolls and any other per frame randomness interleave with these draws.

## State written besides walls

| Offset | Type | Meaning |
|---|---|---|
| `+0x168` | records | wall array, 0x14 bytes each |
| `+0x2878` | int | wall count |
| `+0x28b4` | float | wave delay timer |
| `+0x28c0` | float | wall speed (read only) |
| `+0x28c4` | int | opening distance offset |
| `+0x28c8` | float | wall scroll factor |
| `+0x2930` | int | scratch base slot |
| `+0x53e4` | int | unknown flag |
| `+0x5464` | int | spawn pause flag |

The wave delay is the main output. Each case stores a base value (for example 200.0 at line 1771) and the shared tail at lines 21005 to 21011 rescales it as `delay = base * 20.0f / speed` in single precision (observed in the disassembly at `0x427030`, `mulss` then `divss`). If speed is 0 the base is kept. The default path rescales whatever value is already there, unless it's negative (lines 12046 to 12051). The caller then subtracts the frame delta (`gameclass+0x28a8`, a double set from `getExpectedFrameDelta()` in `gameclass_restart.c`) each frame and spawns the next wave once the timer reaches 0 or below (`superhex_gamelogic.c` lines 793 to 800 and 1094 to 1100). Since walls move `speed * delta` per frame, the spacing between waves in distance units is about `base * 20`, independent of speed (inferred).

These are the base delays per id:

| Ids | Base delay |
|---|---|
| `0`, `0x5a`-`0x5f`, `0x65`, `0x6a`-`0x6c`, `0x6f`-`0x71`, `0x78`, `0x79` | 200 |
| `0x66`, `0x7d` | 220 |
| `0x67`, `0x68` | 240 |
| `0x69` | 300 |
| `0x6d`, `0x6e` | 180 |
| `0x7b` | 350 |
| `100`, `1001` | 50 |
| `200`, `201`, `205` | 60 |
| `202` | 60 or 70 |
| `300` | 60, 70, or 160 |
| `301` | 60, 70, 85, or 250 |
| `302` | 60, 70, or 85 |
| `400`-`403`, `406`, `407` | 5 |
| `500`, `600`, `601` | 120 |
| `501` | 160 |
| `502` | 300 |
| `1000` | 1200 |

The remaining fields work as follows:

- `gameclass+0x28c4` is set to 0 on entry (line 21) and to the per id offset in the opening family. It's only used inside this function (observed).
- `gameclass+0x28c8` is set to 100.0 by the opening family (lines 2296, 3113, 3943, 3948, 4434, 4500). In the caller it's `superhex+0x28e0`, set to 100.0 at the start of a run and -40.0 on death, and wall motion switches to a `factor * delta * 5` mode while it's 1.0 or below (`superhex_gamelogic.c` lines 181, 260, 352 to 372, 432 to 450). So it's probably the start and death scroll factor (inferred).
- `gameclass+0x2930` holds the last random draw (base slot or variant). Nothing outside this function reads it in the decomps I have.
- `gameclass+0x53e4` is set to 1 by 0x67, 0x68, 0x6f, 0x70, and 0x71 (lines 759, 6449, 8170, 9303, 10947). Nothing in the decomps I have reads it, so its meaning is unknown.
- `gameclass+0x5464` is set to 1 by the transition ids 400 to 407 (line 12045), and while it's set the caller stops counting down the wave timer (`superhex_gamelogic.c` line 793). The marker wall clears it when it reaches the center, so no new wave spawns until the side change starts (inferred from the two pieces of code).

generatewave doesn't change sides, rotation, or speed directly (observed, no writes to `0xfc`, `0x158`, or `0x28c0`).

## Timing and determinism

Inside generatewave the only float timing dependency is the delay rescale by wall speed (`gameclass+0x28c0`), which the caller grows by the frame delta each wave (`superhex_gamelogic.c` lines 1100 to 1116). Wall slots, distances, and widths are integer constants plus random integers, so they're exact. The caller adds more timing dependencies that a sim has to copy:

- The wave timer is a float decremented by a double frame delta and clamped to 0 before the new delay is set, so overshoot is thrown away and spawns land on frame boundaries.
- Wall distances are ints updated as `(int)(dist - speed * delta)` each frame (`superhex_gamelogic.c` line 380), so truncation accumulates per frame.
- Speed and survival time thresholds (26.0, 3600, 7200) gate pattern choice.
- Stage index 4 selects pools by what looks like a millisecond clock (line 588), which would be wall time and not frame count (inferred, the writer of `superhex+0x3c710` isn't in these decomps).

With a fixed frame delta and a known rand stream, everything generatewave does is deterministic.

## Hand written or table driven

The code looks hand written per pattern, with GCC inlining `addenemy` (inferred). The evidence is as follows:

- Only 55 out of line `addenemy(` calls remain, all between lines 3952 and 4497 (id 0 and the opening family), and the rest are 647 inlined copies of the free slot search followed by five field stores.
- Offsets like `+7` and `+8` survive in the slot math (line 1589), which a table generator would normalize.
- Mirror patterns are separate cases, not a flag.
- The 5, 4, and 3 sided families are separate copies with their own modulus.
- Delays and widths are irregular per case (220, 240, 350, widths like 610 and 910 in 0x6a).
- The opening family reuses a member field as a local offset, which is a typical shortcut in hand written code.

Parsing the decompiled text is a poor way to transcribe it, since most writes are inlined into slot search loops and cases share tails through gotos (27 gotos into the shared tails `LAB_00425c3b`, `LAB_00427030`, and `LAB_0042703b` alone). The harness approach above is the mechanical route. It runs each id with every combination of draws and writes a table keyed by (id, draw path) with the ordered wall list and the base delay. The whole game fits in 3,053 rows, and a C sim would store, per id, a small tree of draw ranges (the range of a later draw can depend on an earlier result, as in ids 201 and 501) with a wall list at each leaf. At runtime it would make the same `ofRandom` calls with the exact float math, walk the tree, emit the walls, and apply `delay = base * 20 / speed`.

Before trusting that table, the next step would be to run the same enumeration against the real machine code in place of Ghidra's C. That means emulating `0x421bc0` (for example with Unicorn, which isn't installed here) or calling it under gdb, with `ofRandom` at `0x486720` hooked, then diffing against the harness output. Case 600 and the case list match so far, and the other 44 cases haven't been checked against the binary.

## Open items

- The 0x195 call that spawns nothing is either a bug in the game or a misread of the caller, and it's worth confirming in the disassembly of `superhex::gamelogic` near the call.
- What reads `gameclass+0x53e4` and `gameclass+0x53e0` (likely graphics or rotation code not in this folder).
- What `getExpectedFrameDelta()` returns (likely 1.0 at the target frame rate, unverified), which sets the time unit of the delays.
- The writer of `superhex+0x3c710` for the stage 4 clock.
- `addenemy` has no capacity check against the 500 record array (observed in `gameclass_addenemy.c`), so the count can in principle run past 500.
