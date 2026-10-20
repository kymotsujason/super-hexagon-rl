# Core loop, timing, RNG, and state layout

Target is the native Linux build, `SuperHexagon` with SHA-256
`6f13c58d136b84df212cc23a560f7383e2f689005af18072e51d8a7439d2ba1d` (Steam depot 221643, buildid
8838351). It's an x86-64 ELF, not stripped, type `EXEC` (not position independent), so the
addresses below are fixed. Everything here comes from Ghidra 12.1.2 pseudocode through REA and from
`objdump` and `nm`.

## Objects

`main` (0x41d790) allocates one `superhex` of 0x3c758 bytes, zeroes it, runs the constructor, and
stores the pointer in the global `SuperHexagonApp` (0x7f5808). The window object is the global
`window` (0x7f5908), an `ofAppGlutWindow` that is really a small SDL and FNA3D layer.

| Object | Location |
|---|---|
| `gameclass` | `superhex+0x18` |
| `graphicsclass` | `superhex+0xbfc8` |
| `scoreclass` | `superhex+0x57f0` |
| `musicclass` | `superhex+0x1e4a8` |
| `helpclass` | `superhex+0x3bf30` |
| `deviceclass` | `superhex+0x3bfa8` |
| key state bytes | `superhex+0x3c07c+keycode` |

Since `gameclass` sits at `+0x18`, a field at `gameclass+X` is at `superhex+X+0x18`. The
offsets below are all `superhex` offsets unless they say otherwise.

## Frame loop

`ofAppGlutWindow::runAppViaInfiniteLoop` (0x4889c0) is a fixed timestep accumulator. Window
fields are `+0xd0` period in counter ticks (`freq/60`), `+0xd8` last counter reading, `+0xe0`
accumulator, `+0xe8` estimated sleep precision, `+0x500` counter frequency, and `+200` (0xc8) the
last frame time in seconds.

```c
setup(); update();                     // vtable +0x10, +0x18
last = SDL_GetPerformanceCounter();
while (running) {
    acc += SDL_GetPerformanceCounter() - last;
    while (acc + sleepPrecision < period) { SDL_Delay(1); acc += ...; }
    while (acc < period) { acc += ...; }  // busy wait
    pollEvents();                         // SDL_PollEvent loop, keys -> keyPressed/keyReleased
    while (acc >= period) {
        acc -= period;
        window.lastFrameTime = period / freq;   // exactly 1/60 s from integer ticks
        app->update();                           // vtable +0x18
    }
    display();   // FNA3D clear, app->draw() (vtable +0x20), FNA3D_SwapBuffers
}
```

`superhex::update` (0x47a440) runs `calculateTime`, `gameinput`, `gamelogic`, and
`musicclass::processmusic` in that order. `calculateTime` (0x470c20) turns the frame time into a
time scale at `+0x28c0` (double, `dt / (1/60)` rounded to 0.1). Game logic multiplies its per tick
changes by this value. The loop sets `window+200` to `period / freq` right before each `update`
and can run several updates before one `display`, so `update` always sees exactly one period. With
a counter frequency of 1,000,000,000 (normal on Linux) the period is 16,666,666 ticks, the frame
time is 0.016666666 s, and the scale is 1.0 on every tick at 60 fps. The hook sets the same value.
`NOTES-input.md` describes `calculateTime` using the measured frame time, which holds only when
looking at `update` alone.

`superhex::draw` (0x47a540) stores `ofGetElapsedTimeMillis()` (real `SDL_GetTicks` minus the
start time) at `+0x3c710`. `gamelogic` reads `+0x3c710 - +0x5628` to script wall speed and
rotation phases in stage mode 4, and uses `ofGetElapsedTimeMillis` for the music loop and the
beat value at `+0x2924`, which feeds the visual pulse at `+0x2928`.

## Random numbers

`ofRandom(x)` (0x486720) returns `rand() * x * 4.656613e-10`. `rand` is imported from glibc and
`srand` isn't imported at all. `objdump` shows `rand@plt` called only from `ofRandom`, and
`ofRandom` called only from `gamelogic` (72 sites), `generatewave` (68), `gameinput` (9),
`musicclass::play` (3), and `gameclass::changetostage` (2). Neither `lib64/libSDL2-2.0.so.0` nor
`lib64/libsteam_api.so` imports `rand`, so rendering doesn't consume random numbers.

## Walls

The wall array starts at `+0x180` with 500 records of 0x14 bytes. The count of used records is
at `+0x2890` (`gameclass+0x2878`). `gameclass::addenemy(slot, dist, width, type)` (0x421a20)
reuses the first disabled record or appends one, and stores `dist + 4000`.

| Field | Record offset | Type |
|---|---|---|
| slot | +0x0 | int |
| distance | +0x4 | int |
| width | +0x8 | int |
| type | +0xc | int |
| enabled | +0x10 | byte |

A record with slot 20, 21, or 22 isn't a wall. When it reaches the center it sets the rotation
direction field (`+0x170` to 1 or 2) or a flag at `+0x53f8`. Walls move inwards by
`speed * timescale` per tick with integer truncation, and once `distance` reaches 0 the width
shrinks by the same amount until the record is disabled.

## Player and collision

| Field | Offset | Notes |
|---|---|---|
| player angle | `+0x28f0` | int degrees, starts at `180 / sides` |
| previous angle | `+0x28ec` | restored on a side hit |
| player slot | `+0x13c` | `angle / (360 / sides)`, also reused as scratch |
| sides | `+0x114` | 6 after restart |
| wall speed | `+0x28d8` | float |
| time scale | `+0x28c0` | double |
| hit flag | `+0x28f8` | float, set to 1.0 on a fatal hit |
| death timer | `+0x104` | float, 0 while alive, set to 1.0 on death then counts up |
| run time | `+0x2894` | float, ticks at 60 per second |
| wave delay | `+0x28cc` | float, a new wave is generated when it runs out |
| stage mode | `+0x5424` | int, -2 menu, 0 to 4 stage modes |
| hyper flag | `+0x5478` | byte |
| level thresholds | `+0x5498` | int array, run time thresholds for rank ups |
| rank | `+0x293c` | int, 0 to 6 for POINT through HEXAGON |
| rotation mode | `+0x2900` | int, picked with `ofRandom` |

The collision test in `gamelogic` looks at enabled walls in the player's slot with distance at or
under 150. If the wall's inner edge is between `145 - speed` and 150 the player is hit. If the edge
has already passed and the wall is long (width over 199), the move is treated as a side hit and
the angle goes back to the previous value. In the rest of that branch (lines 327 to 340 of
`superhex_gamelogic.c`) the player likely dies only if a wall also blocks the restored slot
(inferred), but the condition mixes in the first wall's distance. On a hit with the death timer at 0,
`gamelogic` plays the death sound, sets the death timer to 1.0, and clears `+0x28e0`.

## Open questions

- Which code writes `+0x5628`, the start time that the mode 4 script subtracts.
- Whether the beat pulse at `+0x2928` affects anything besides drawing.
