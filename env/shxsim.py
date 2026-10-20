import ctypes
from pathlib import Path

import numpy as np

from shxgame import (CMD_DUMP, CMD_REPLAY, CMD_RESET, CMD_STEP, ERROR_NAMES, OBS_SIZE, PROJECT_DIR,
                     SHX_MAX_REPLAY, SUPERHEX_SIZE, GameError, ShxShared)

SIM_PATH = PROJECT_DIR / "sim" / "shxsim.so"

sim_lib = None
if SIM_PATH.is_file():
    sim_lib = ctypes.CDLL(str(SIM_PATH))
    sim_lib.shxSimCreate.argtypes = [ctypes.c_char_p, ctypes.c_size_t, ctypes.c_uint64, ctypes.c_uint32,
                                     ctypes.c_void_p, ctypes.c_int]
    sim_lib.shxSimPointerOffsets.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_int]
    sim_lib.shxSimPointerOffsets.restype = ctypes.c_int
    sim_lib.shxSimCreate.restype = ctypes.c_void_p
    sim_lib.shxSimDestroy.argtypes = [ctypes.c_void_p]
    sim_lib.shxSimShared.argtypes = [ctypes.c_void_p]
    sim_lib.shxSimShared.restype = ctypes.c_void_p
    sim_lib.shxSimState.argtypes = [ctypes.c_void_p]
    sim_lib.shxSimState.restype = ctypes.c_void_p
    sim_lib.shxSimCommand.argtypes = [ctypes.c_void_p]
    sim_lib.shxSimCommand.restype = ctypes.c_int
    sim_lib.shxSimBatchStep.argtypes = [
        ctypes.c_int, ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p,
        ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_int,
    ]
    sim_lib.shxSimBatchStep.restype = ctypes.c_int


def default_state_path():
    return PROJECT_DIR / "sim" / "initial_state.bin"


def default_ranges_path():
    return PROJECT_DIR / "sim" / "initial_state.ranges"


def default_tick_path():
    return PROJECT_DIR / "sim" / "initial_state.tick"


# the game's mapped memory, minus its own executable image, whose addresses overlap small integers
def game_pointer_ranges(pid):
    ranges = []
    for line in Path(f"/proc/{pid}/maps").read_text().splitlines():
        fields = line.split()
        path = fields[5] if len(fields) > 5 else ""
        if path.endswith("/SuperHexagon"):
            continue
        lo, hi = (int(v, 16) for v in fields[0].split("-"))
        ranges.append((lo, hi))
    return np.array(ranges, dtype=np.uint64)


def save_initial_state(game):
    state = game.dump()
    default_state_path().write_bytes(state)
    game_pointer_ranges(game.process.pid).tofile(default_ranges_path())
    default_tick_path().write_text(str(int(game.shared.tick)))
    return state


# One simulated game with the same reset, step, replay, and dump calls as ShxGame, built from a
# dump of the real game's superhex object.
class ShxSim:
    def __init__(self, state=None, ranges=None, tick=None, seed=1):
        if sim_lib is None:
            raise GameError(f"simulator not built, run make in {SIM_PATH.parent}")
        if state is None:
            state = default_state_path().read_bytes()
        if ranges is None:
            ranges = np.fromfile(default_ranges_path(), dtype=np.uint64).reshape(-1, 2)
        ranges = np.ascontiguousarray(ranges, dtype=np.uint64)
        if tick is None:
            tick = int(default_tick_path().read_text())
        if len(state) != SUPERHEX_SIZE:
            raise GameError(f"state is {len(state)} bytes, expected {SUPERHEX_SIZE}")
        self.handle = sim_lib.shxSimCreate(state, len(state), tick, seed, ranges.ctypes.data, len(ranges))
        if not self.handle:
            raise GameError("shxSimCreate failed")
        self.shared = ShxShared.from_address(sim_lib.shxSimShared(self.handle))
        self.obs = np.ctypeslib.as_array((ctypes.c_float * OBS_SIZE).from_address(
            ctypes.addressof(self.shared) + ShxShared.obs.offset))

    def _run(self):
        error = sim_lib.shxSimCommand(self.handle)
        if error:
            raise GameError(f"simulator error {error}: {ERROR_NAMES.get(error, 'unknown')}")
        return self.shared

    # The vector env posts commands and waits for them separately so real games can run in
    # parallel. A simulator runs the command right away and keeps any error for wait().
    def _post(self, command):
        self._pending_error = None
        try:
            command()
        except GameError as error:
            self._pending_error = error

    def post_reset(self, level=0, seed=1, render=False):
        self._post(lambda: self.reset(level, seed))

    def post_replay(self, level, seed, actions, frame_skip):
        self._post(lambda: self.replay(level, seed, actions, frame_skip))

    def wait(self):
        error, self._pending_error = getattr(self, "_pending_error", None), None
        if error:
            raise error
        return self.shared

    def step(self, keys, frame_skip=1, render=False):
        sh = self.shared
        sh.command = CMD_STEP
        sh.keys = keys
        sh.frame_skip = frame_skip
        return self._run()

    def reset(self, level=0, seed=1, render=False):
        sh = self.shared
        sh.command = CMD_RESET
        sh.level = level
        sh.seed = seed
        return self._run()

    def replay(self, level, seed, actions, frame_skip):
        if len(actions) > SHX_MAX_REPLAY:
            raise GameError(f"replay of {len(actions)} actions is over the {SHX_MAX_REPLAY} limit")
        sh = self.shared
        sh.command = CMD_REPLAY
        sh.level = level
        sh.seed = seed
        sh.frame_skip = frame_skip
        sh.replay_count = len(actions)
        ctypes.memmove(ctypes.addressof(sh.replay_actions), bytes(actions), len(actions))
        return self._run()

    # byte offsets of the pointers replaced on load, which never match the real game's dump
    def pointer_offsets(self):
        out = np.zeros(8192, dtype=np.uint32)
        count = sim_lib.shxSimPointerOffsets(self.handle, out.ctypes.data, len(out))
        return out[:count].copy()

    def dump(self):
        self.shared.command = CMD_DUMP
        self._run()
        return ctypes.string_at(sim_lib.shxSimState(self.handle), SUPERHEX_SIZE)

    def close(self):
        if self.handle:
            del self.obs
            del self.shared
            sim_lib.shxSimDestroy(self.handle)
            self.handle = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
