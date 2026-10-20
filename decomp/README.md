# Game internals

The three `NOTES-*.md` files are the reverse engineering notes behind the hook and the simulator.
They cover the `superhex` object's fields and offsets, the main loop and timing, input, and wave
generation. Line numbers in them refer to Ghidra 12.1.2's pseudocode for the native Linux build
(SHA-256 `6f13c58d136b84df212cc23a560f7383e2f689005af18072e51d8a7439d2ba1d`), which isn't included.

## Your own decompilation

The simulator compiles the game's own logic, so building it needs that pseudocode, and since it's
the game's code this repository doesn't ship it. To build the simulator, open your copy of the
`SuperHexagon` binary in Ghidra, let it analyze, and export the decompiled C of each function
listed in `COMPILED` in `sim/tools/ghidra2c.py` into this folder, one file per function, named the
way that list names them (for example `superhex_gamelogic.c` for `superhex::gamelogic`). Keep each
function's decompiler output as Ghidra shows it, including the `/* superhex::gamelogic(...) */`
comment line at the top, since the converter reads the class name from it. `make -C sim` then
converts and compiles them, and `.gitignore` keeps these files out of git. The files used during
the project came from Ghidra 12.1.2, and another Ghidra version may decompile a little differently,
which `scripts/check_sim.py` would catch.

`generatewave-harness/harness.c` is a small driver that compiles an exported copy of
`gameclass::generatewave` (saved as `generatewave-harness/gw.c`) and lists the walls each pattern
id produces for each random draw, and `summarize.py` tabulates its output.
