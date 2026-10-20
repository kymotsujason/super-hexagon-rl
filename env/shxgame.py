import ctypes
import hashlib
import itertools
import mmap
import os
import shutil
import subprocess
import time
from pathlib import Path

import numpy as np

GAME_DIR = Path.home() / ".local/share/Steam/steamapps/common/Super Hexagon"
PROJECT_DIR = Path(__file__).resolve().parent.parent
HOOK_PATH = PROJECT_DIR / "hook" / "shxhook.so"
TEMPLATE_DIR = PROJECT_DIR / "env" / "template" / "SuperHexagon"
# the bundled SDL has no offscreen driver, the system one (sdl2-compat on SDL3) does
SYSTEM_SDL2 = Path("/usr/lib/libSDL2-2.0.so.0")
EXPECTED_SHA256 = "6f13c58d136b84df212cc23a560f7383e2f689005af18072e51d8a7439d2ba1d"

SHX_MAGIC = 0x31584853
SHX_PROTO_VERSION = 5
SHX_MAX_WALLS = 256
SHX_MAX_REPLAY = 32768
OBS_SIZE = 45

CMD_STEP = 1
CMD_QUIT = 2
CMD_RESET = 3
CMD_REPLAY = 4
CMD_DUMP = 5
SUPERHEX_SIZE = 0x3C758

KEY_LEFT = 1
KEY_RIGHT = 2
KEY_UP = 4
KEY_SELECT = 8

SEM_SIZE = 32

LEVEL_HEXAGON = 0
LEVEL_HEXAGONER = 1
LEVEL_HEXAGONEST = 2
LEVEL_HYPER_HEXAGON = 3
LEVEL_HYPER_HEXAGONER = 4
LEVEL_HYPER_HEXAGONEST = 5

ERROR_NAMES = {
    1: "game build doesn't match the hook",
    2: "bad command",
    3: "reset didn't reach the level in time",
    4: "reset refused (locked level or unexpected menu)",
    5: "the player died during a replay",
    6: "the game is in its ending and needs a relaunch",
    7: "the state dump couldn't be written",
}


class ShxWall(ctypes.Structure):
    _fields_ = [
        ("slot", ctypes.c_int32),
        ("distance", ctypes.c_int32),
        ("width", ctypes.c_int32),
        ("type", ctypes.c_int32),
    ]


class ShxShared(ctypes.Structure):
    _fields_ = [
        ("magic", ctypes.c_uint32),
        ("version", ctypes.c_uint32),
        ("struct_size", ctypes.c_uint32),
        ("client_pid", ctypes.c_uint32),
        ("command_ready", ctypes.c_uint8 * SEM_SIZE),
        ("result_ready", ctypes.c_uint8 * SEM_SIZE),
        ("command", ctypes.c_int32),
        ("keys", ctypes.c_int32),
        ("frame_skip", ctypes.c_int32),
        ("render", ctypes.c_int32),
        ("level", ctypes.c_int32),
        ("seed", ctypes.c_uint32),
        ("replay_count", ctypes.c_int32),
        ("reset_flags", ctypes.c_int32),
        ("tick", ctypes.c_uint64),
        ("hook_ready", ctypes.c_int32),
        ("error", ctypes.c_int32),
        ("ticks_run", ctypes.c_int32),
        ("stage", ctypes.c_int32),
        ("player_angle", ctypes.c_int32),
        ("sides", ctypes.c_int32),
        ("death_timer", ctypes.c_float),
        ("run_time", ctypes.c_float),
        ("wall_speed", ctypes.c_float),
        ("hit_flag", ctypes.c_float),
        ("camera_distance", ctypes.c_int32),
        ("rotation_mode", ctypes.c_int32),
        ("hyper", ctypes.c_int32),
        ("wall_count", ctypes.c_int32),
        ("reset_ticks", ctypes.c_int32),
        ("tutorial_active", ctypes.c_int32),
        ("last_action", ctypes.c_int32),
        ("pad2", ctypes.c_int32),
        ("obs", ctypes.c_float * OBS_SIZE),
        ("walls", ShxWall * SHX_MAX_WALLS),
        ("replay_actions", ctypes.c_uint8 * SHX_MAX_REPLAY),
    ]


# offsets printed from the C header, so a drift between the two layouts fails loudly
assert ctypes.sizeof(ShxShared) == 37240
assert ShxShared.command_ready.offset == 16
assert ShxShared.result_ready.offset == 48
assert ShxShared.command.offset == 80
assert ShxShared.replay_count.offset == 104
assert ShxShared.tick.offset == 112
assert ShxShared.last_action.offset == 184
assert ShxShared.obs.offset == 192
assert ShxShared.walls.offset == 372
assert ShxShared.replay_actions.offset == 4468


class Timespec(ctypes.Structure):
    _fields_ = [("tv_sec", ctypes.c_long), ("tv_nsec", ctypes.c_long)]


libc = ctypes.CDLL("libc.so.6", use_errno=True)
libc.sem_init.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_uint]
libc.sem_post.argtypes = [ctypes.c_void_p]
libc.sem_timedwait.argtypes = [ctypes.c_void_p, ctypes.POINTER(Timespec)]
libc.sem_destroy.argtypes = [ctypes.c_void_p]


class GameError(RuntimeError):
    pass


STAGE_ENDING = 4


def check_game_binary():
    digest = hashlib.sha256((GAME_DIR / "SuperHexagon").read_bytes()).hexdigest()
    if digest != EXPECTED_SHA256:
        raise GameError(f"SuperHexagon binary changed (sha256 {digest}), the hook needs checking first")


_instance_counter = itertools.count()


# one hooked game process driven in lockstep through shared memory
class ShxGame:
    def __init__(self, instance_dir, seed=1, timeout=30.0, headless=False):
        if not HOOK_PATH.is_file():
            raise GameError(f"hook not built, run make in {HOOK_PATH.parent}")
        check_game_binary()

        self.instance_dir = Path(instance_dir).resolve()
        self.instance_dir.mkdir(parents=True, exist_ok=True)
        # every launch starts from the same save, with the tutorial done and hyper levels unlocked
        save_dir = self.instance_dir / "data" / "SuperHexagon"
        save_dir.mkdir(parents=True, exist_ok=True)
        for template_file in TEMPLATE_DIR.iterdir():
            shutil.copyfile(template_file, save_dir / template_file.name)
        self.timeout = timeout
        self.process = None
        self._closed = False

        self.shm_name = f"/shx-{os.getpid()}-{next(_instance_counter)}"
        self.shm_path = Path("/dev/shm") / self.shm_name.lstrip("/")
        size = ctypes.sizeof(ShxShared)
        fd = os.open(self.shm_path, os.O_RDWR | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            os.ftruncate(fd, size)
            self._map = mmap.mmap(fd, size)
        finally:
            os.close(fd)

        self.shared = ShxShared.from_buffer(self._map)
        # the hook writes the 45 value observation here after every command
        self.obs = np.frombuffer(self._map, dtype=np.float32, count=OBS_SIZE, offset=ShxShared.obs.offset)
        base = ctypes.addressof(self.shared)
        self._command_sem = base + ShxShared.command_ready.offset
        self._result_sem = base + ShxShared.result_ready.offset
        self.shared.magic = SHX_MAGIC
        self.shared.version = SHX_PROTO_VERSION
        self.shared.struct_size = size
        self.shared.client_pid = os.getpid()
        if libc.sem_init(self._command_sem, 1, 0) != 0 or libc.sem_init(self._result_sem, 1, 0) != 0:
            self._cleanup_shm()
            raise GameError("sem_init failed")

        env = dict(os.environ)
        preload = str(HOOK_PATH)
        if headless:
            if not SYSTEM_SDL2.is_file():
                raise GameError(f"headless mode needs {SYSTEM_SDL2}")
            # preloading it ahead of the RPATH copy makes the game use it for libSDL2-2.0.so.0
            preload += ":" + str(SYSTEM_SDL2)
            env["SDL_VIDEODRIVER"] = "offscreen"
            env.pop("DISPLAY", None)
            env.pop("WAYLAND_DISPLAY", None)
        env.update({
            "LD_PRELOAD": preload,
            "SHX_DATA_DIR": str(self.instance_dir / "data"),
            "SHX_LOG": str(self.instance_dir / "hook.log"),
            "SHX_SHM": self.shm_name,
            "SHX_SEED": str(seed),
            "SDL_AUDIODRIVER": env.get("SDL_AUDIODRIVER", "dummy"),
        })
        self._game_log = open(self.instance_dir / "game.log", "wb")
        try:
            self.process = subprocess.Popen(
                ["./SuperHexagon"], cwd=GAME_DIR, env=env,
                stdin=subprocess.DEVNULL, stdout=self._game_log, stderr=subprocess.STDOUT,
            )
            self._wait_result()
            if not self.shared.hook_ready or self.shared.error:
                raise GameError(f"hook didn't start lockstep (error {self.shared.error}), see {self.instance_dir / 'hook.log'}")
        except BaseException:
            self.close()
            raise

    def _wait_result(self):
        deadline = time.monotonic() + self.timeout
        while True:
            ts = Timespec()
            now = time.clock_gettime(time.CLOCK_REALTIME) + 0.5
            ts.tv_sec = int(now)
            ts.tv_nsec = int((now - int(now)) * 1e9)
            if libc.sem_timedwait(self._result_sem, ctypes.byref(ts)) == 0:
                return
            err = ctypes.get_errno()
            if err not in (110, 4):  # ETIMEDOUT, EINTR
                raise GameError(f"sem_timedwait failed with errno {err}")
            if self.process is None or self.process.poll() is not None:
                code = self.process.returncode if self.process else None
                raise GameError(f"game exited with code {code}, see {self.instance_dir}")
            if time.monotonic() > deadline:
                raise GameError("timed out waiting for the game")

    # post_* start a command without waiting, so one process can keep many games busy at once,
    # and wait() collects the result
    def post_step(self, keys, frame_skip=1, render=False):
        sh = self.shared
        sh.command = CMD_STEP
        sh.keys = keys
        sh.frame_skip = frame_skip
        sh.render = 1 if render else 0
        libc.sem_post(self._command_sem)

    def post_reset(self, level=LEVEL_HEXAGON, seed=1, render=False, flags=0):
        sh = self.shared
        sh.command = CMD_RESET
        sh.level = level
        sh.seed = seed
        sh.reset_flags = flags
        sh.render = 1 if render else 0
        libc.sem_post(self._command_sem)

    # resets with the seed, then replays the recorded actions, which lands on the same state
    def post_replay(self, level, seed, actions, frame_skip):
        if len(actions) > SHX_MAX_REPLAY:
            raise GameError(f"replay of {len(actions)} actions is over the {SHX_MAX_REPLAY} limit")
        sh = self.shared
        sh.command = CMD_REPLAY
        sh.level = level
        sh.seed = seed
        sh.reset_flags = 0
        sh.frame_skip = frame_skip
        sh.render = 0
        sh.replay_count = len(actions)
        ctypes.memmove(ctypes.addressof(sh.replay_actions), bytes(actions), len(actions))
        libc.sem_post(self._command_sem)

    def wait(self):
        self._wait_result()
        sh = self.shared
        if sh.error:
            raise GameError(f"hook reported error {sh.error}: {ERROR_NAMES.get(sh.error, 'unknown')}")
        return sh

    def step(self, keys, frame_skip=1, render=False):
        self.post_step(keys, frame_skip, render)
        return self.wait()

    # walks the menus to the level, reseeds rand right before it starts, and returns the first state
    def reset(self, level=LEVEL_HEXAGON, seed=1, render=False, flags=0):
        self.post_reset(level, seed, render, flags)
        return self.wait()

    # the whole superhex object as bytes, for checking the simulator against the game
    def dump(self):
        sh = self.shared
        sh.command = CMD_DUMP
        sh.render = 0
        libc.sem_post(self._command_sem)
        self.wait()
        path = Path(str(self.shm_path) + ".dump")
        data = path.read_bytes()
        if len(data) != SUPERHEX_SIZE:
            raise GameError(f"dump was {len(data)} bytes, expected {SUPERHEX_SIZE}")
        return data

    def replay(self, level, seed, actions, frame_skip):
        self.post_replay(level, seed, actions, frame_skip)
        return self.wait()

    def close(self):
        if self._closed:
            return
        self._closed = True
        if self.process is not None and self.process.poll() is None:
            self.shared.command = CMD_QUIT
            libc.sem_post(self._command_sem)
            try:
                self.process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()
        if hasattr(self, "_game_log"):
            self._game_log.close()
        self._cleanup_shm()

    def _cleanup_shm(self):
        if hasattr(self, "obs"):
            del self.obs
        if hasattr(self, "shared"):
            del self.shared
        if hasattr(self, "_map"):
            # callers may still hold the struct step() returned, then the mapping goes with it
            try:
                self._map.close()
            except BufferError:
                pass
        self.shm_path.unlink(missing_ok=True)
        Path(str(self.shm_path) + ".dump").unlink(missing_ok=True)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
