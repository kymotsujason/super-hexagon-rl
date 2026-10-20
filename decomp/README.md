# Game internals

The `NOTES-*.md` files are the reverse engineering notes behind the hook and the simulator. They
cover the `superhex` object's fields, the main loop and timing, input, and wave generation. Their line
numbers refer to Ghidra 12.1.2's pseudocode for the native Linux build (SHA-256
`6f13c58d136b84df212cc23a560f7383e2f689005af18072e51d8a7439d2ba1d`), which isn't included.

## Your own decompilation

The simulator compiles the game's own logic, and since that's the game's code, this repository
doesn't ship it. To build the simulator from your copy of the game:

1. Open the `SuperHexagon` binary in Ghidra and let it analyze.
2. Export the decompiled C of each function in `COMPILED` in `sim/tools/ghidra2c.py` into this
   folder, one file per function, named as that list names them (`superhex_gamelogic.c` for
   `superhex::gamelogic`). Keep Ghidra's `/* superhex::gamelogic(...) */` comment at the top of
   each file, since the converter reads the class name from it.
3. Run `make -C sim`, then `scripts/check_sim.py`, which plays the simulator against the game and
   shows whether another Ghidra version's output behaves differently.

`.gitignore` keeps these files out of git. `generatewave-harness/harness.c` compiles an exported
`gameclass::generatewave` (saved as `generatewave-harness/gw.c`) and lists the walls each pattern
id makes for each random draw, and `summarize.py` tabulates them.
