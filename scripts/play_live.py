import argparse
import ctypes
import os
import random
import shutil
import struct
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

PROJECT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_DIR / "env"))
sys.path.insert(0, str(PROJECT_DIR / "scripts"))

import numpy as np
import torch

from shxenv import build_observation
from shxgame import GAME_DIR, HOOK_PATH, TEMPLATE_DIR, check_game_binary
from train_ppo import Agent

LEVEL_NAMES = ["Hexagon", "Hexagoner", "Hexagonest", "Hyper Hexagon", "Hyper Hexagoner", "Hyper Hexagonest"]
# level select cursor for each level, the same table as shxLevelCursor in hook/shxcore.h
LEVEL_CURSOR = [0, 5, 4, 3, 2, 1]

# fixed address of the superhexagon app pointer in the non-PIE executable, see hook/shxhook.c
ADDR_APP = 0x7F5808
ADDR_SUPERHEX_VPTR = 0x596710

# superhex field offsets, the same as hook/shxcore.h
OFF_LATCH = 0x50
OFF_DEATH_TIMER = 0x104
OFF_CAMERA_DISTANCE = 0x10C
OFF_SIDES = 0x114
OFF_WALLS = 0x180
OFF_WALL_COUNT = 0x2890
OFF_RUN_TIME = 0x2894
OFF_WALL_SPEED = 0x28D8
OFF_PLAYER_ANGLE = 0x28F0
OFF_STAGE = 0x5424
OFF_TITLE_SUBMENU = 0x5460
OFF_LEVEL_CURSOR = 0x5464
OFF_LEVEL_MESSAGE = 0x5584
OFF_STAGE_CHANGE_A = 0x55DC
OFF_STAGE_CHANGE_B = 0x55E4
OFF_TITLE_CURSOR = 0xBFC0
OFF_KEYS = 0x3C07C
KEY_LEFT = 0x164
KEY_RIGHT = 0x166
BLOCK_SIZE = 0x55E8
WALL_RECORD_SIZE = 0x14
WALL_CAPACITY = 500
MAX_WALLS = 256

STAGE_TITLE = -1
STAGE_LEVEL_SELECT = -2
STAGE_ENDING = 4
LEVEL_MESSAGE_ENDING = 3
GAME_OVER_CAMERA = 320
FRAME_SKIP = 2

XK_LEFT = 0xFF51
XK_RIGHT = 0xFF53
XK_RETURN = 0xFF0D
XK_ESCAPE = 0xFF1B
KEY_PRESS = 2
KEY_RELEASE = 3
KEY_PRESS_MASK = 1
KEY_RELEASE_MASK = 2


class XKeyEvent(ctypes.Structure):
    _fields_ = [
        ("type", ctypes.c_int),
        ("serial", ctypes.c_ulong),
        ("send_event", ctypes.c_int),
        ("display", ctypes.c_void_p),
        ("window", ctypes.c_ulong),
        ("root", ctypes.c_ulong),
        ("subwindow", ctypes.c_ulong),
        ("time", ctypes.c_ulong),
        ("x", ctypes.c_int),
        ("y", ctypes.c_int),
        ("x_root", ctypes.c_int),
        ("y_root", ctypes.c_int),
        ("state", ctypes.c_uint),
        ("keycode", ctypes.c_uint),
        ("same_screen", ctypes.c_int),
    ]


# XEvent is a union padded to 24 longs
class XEvent(ctypes.Union):
    _fields_ = [("xkey", XKeyEvent), ("pad", ctypes.c_long * 24)]


# Sends key events straight to the game's X window, so they can't land in whatever window has
# focus, the way kernel level input would.
class GameWindowKeys:
    def __init__(self, window):
        self.x11 = ctypes.CDLL("libX11.so.6")
        self.x11.XOpenDisplay.restype = ctypes.c_void_p
        self.x11.XOpenDisplay.argtypes = [ctypes.c_char_p]
        self.x11.XDefaultRootWindow.restype = ctypes.c_ulong
        self.x11.XDefaultRootWindow.argtypes = [ctypes.c_void_p]
        self.x11.XKeysymToKeycode.restype = ctypes.c_ubyte
        self.x11.XKeysymToKeycode.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
        self.x11.XSendEvent.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_int, ctypes.c_long,
                                        ctypes.POINTER(XEvent)]
        self.x11.XFlush.argtypes = [ctypes.c_void_p]
        self.x11.XCloseDisplay.argtypes = [ctypes.c_void_p]
        self.display = self.x11.XOpenDisplay(None)
        if not self.display:
            raise RuntimeError("couldn't open the X display, is DISPLAY set?")
        self.window = window
        self.root = self.x11.XDefaultRootWindow(self.display)
        self.keycodes = {sym: self.x11.XKeysymToKeycode(self.display, sym)
                         for sym in (XK_LEFT, XK_RIGHT, XK_RETURN, XK_ESCAPE)}

    def send(self, keysym, down):
        event = XEvent()
        key = event.xkey
        key.type = KEY_PRESS if down else KEY_RELEASE
        key.send_event = 1
        key.display = self.display
        key.window = self.window
        key.root = self.root
        key.time = 0
        key.same_screen = 1
        key.keycode = self.keycodes[keysym]
        mask = KEY_PRESS_MASK if down else KEY_RELEASE_MASK
        self.x11.XSendEvent(self.display, self.window, 1, mask, ctypes.byref(event))
        self.x11.XFlush(self.display)

    def tap(self, keysym, hold=0.06, gap=0.06):
        self.send(keysym, True)
        time.sleep(hold)
        self.send(keysym, False)
        time.sleep(gap)

    def close(self):
        if self.display:
            self.x11.XCloseDisplay(self.display)
            self.display = None


# Reads the running game's superhex object through /proc, which works for a child process.
class GameMemory:
    def __init__(self, pid):
        self.pid = pid
        self.fd = os.open(f"/proc/{pid}/mem", os.O_RDONLY)
        self.app = 0

    def read(self, address, size):
        return os.pread(self.fd, size, address)

    def find_app(self):
        try:
            app = struct.unpack("<Q", self.read(ADDR_APP, 8))[0]
        except OSError:
            # opened before the child finished exec, so the handle points at the old image
            os.close(self.fd)
            self.fd = os.open(f"/proc/{self.pid}/mem", os.O_RDONLY)
            return False
        if app == 0:
            return False
        if struct.unpack("<Q", self.read(app, 8))[0] != ADDR_SUPERHEX_VPTR:
            raise RuntimeError("superhex vtable doesn't match this build")
        self.app = app
        return True

    def run_time(self):
        return struct.unpack("<f", self.read(self.app + OFF_RUN_TIME, 4))[0]

    def death_timer(self):
        return struct.unpack("<f", self.read(self.app + OFF_DEATH_TIMER, 4))[0]

    def keys(self):
        data = self.read(self.app + OFF_KEYS + KEY_LEFT, 3)
        return data[0], data[2]

    def state(self):
        return self.parse(self.read(self.app, BLOCK_SIZE))

    def snapshot(self):
        return self.read(self.app, BLOCK_SIZE)

    def parse(self, b):

        def i32(offset):
            return struct.unpack_from("<i", b, offset)[0]

        def f32(offset):
            return struct.unpack_from("<f", b, offset)[0]

        used = min(max(i32(OFF_WALL_COUNT), 0), WALL_CAPACITY)
        walls = []
        for i in range(used):
            base = OFF_WALLS + i * WALL_RECORD_SIZE
            if not b[base + 0x10]:
                continue
            slot, distance, width = struct.unpack_from("<iii", b, base)
            walls.append(SimpleNamespace(slot=slot, distance=distance, width=width))
            if len(walls) == MAX_WALLS:
                break
        return SimpleNamespace(
            latch=b[OFF_LATCH], death_timer=f32(OFF_DEATH_TIMER), camera=i32(OFF_CAMERA_DISTANCE),
            sides=i32(OFF_SIDES), run_time=f32(OFF_RUN_TIME), wall_speed=f32(OFF_WALL_SPEED),
            player_angle=i32(OFF_PLAYER_ANGLE), stage=i32(OFF_STAGE), title_submenu=i32(OFF_TITLE_SUBMENU),
            level_cursor=i32(OFF_LEVEL_CURSOR), level_message=i32(OFF_LEVEL_MESSAGE),
            stage_change_a=i32(OFF_STAGE_CHANGE_A), stage_change_b=i32(OFF_STAGE_CHANGE_B),
            walls=walls, wall_count=len(walls))

    def title_cursor(self):
        return struct.unpack("<i", self.read(self.app + OFF_TITLE_CURSOR, 4))[0]

    def close(self):
        os.close(self.fd)


# The real game with the hook only faking Steam: without SHX_SHM the hook never takes over the
# loop, so the game runs on its own real time clock and reads real key events.
def launch_game(instance_dir, sound):
    check_game_binary()
    instance_dir = Path(instance_dir).resolve()
    save_dir = instance_dir / "data" / "SuperHexagon"
    save_dir.mkdir(parents=True, exist_ok=True)
    for template_file in TEMPLATE_DIR.iterdir():
        shutil.copyfile(template_file, save_dir / template_file.name)
    env = dict(os.environ)
    env.update({
        "LD_PRELOAD": str(HOOK_PATH),
        "SHX_DATA_DIR": str(instance_dir / "data"),
        "SHX_LOG": str(instance_dir / "hook.log"),
        "SHX_SEED": str(random.randrange(1, 1 << 30)),
        "SDL_VIDEODRIVER": "x11",
    })
    env.pop("SHX_SHM", None)
    if not sound:
        env["SDL_AUDIODRIVER"] = "dummy"
    log = open(instance_dir / "game.log", "wb")
    process = subprocess.Popen(["./SuperHexagon"], cwd=GAME_DIR, env=env, stdin=subprocess.DEVNULL,
                               stdout=log, stderr=subprocess.STDOUT)
    return process, log


def find_window(pid, timeout=30.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = subprocess.run(["xdotool", "search", "--pid", str(pid), "--name", "Super Hexagon"],
                                capture_output=True, text=True)
        ids = result.stdout.split()
        if ids:
            return int(ids[-1])
        time.sleep(0.25)
    raise RuntimeError("the game window never appeared")


def wait_until(predicate, timeout, poll=0.01):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(poll)
    return False


class EndingReached(Exception):
    pass


# Walks the game's menus to the level with real key presses, the way shxRunReset in
# hook/shxcore.h does with key state, except that the level select cursor moves by pressing right.
def go_to_level(memory, keys, level, timeout=60.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        s = memory.state()
        if s.stage == STAGE_ENDING or s.level_message == LEVEL_MESSAGE_ENDING:
            raise EndingReached()
        if s.latch:
            time.sleep(0.02)
            continue
        if s.stage == STAGE_LEVEL_SELECT:
            if s.level_cursor != LEVEL_CURSOR[level]:
                keys.tap(XK_RIGHT)
                continue
            keys.tap(XK_RETURN)
            started = wait_until(lambda: memory.state().stage >= 0 and memory.state().death_timer == 0, 3.0)
            if started:
                return
            continue
        if s.stage == STAGE_TITLE:
            if s.title_submenu != 0 or memory.title_cursor() != 0:
                raise RuntimeError("the title screen isn't on its first entry, refusing to press select there")
            keys.tap(XK_RETURN)
            continue
        if s.stage >= 0:
            dead = s.death_timer > 0
            if dead and s.level_message > 0:
                if s.camera == GAME_OVER_CAMERA:
                    keys.tap(XK_RETURN)
                else:
                    time.sleep(0.05)
                continue
            held = s.stage_change_a == 2 or s.stage_change_b == 1
            if dead and (held or s.camera != GAME_OVER_CAMERA):
                time.sleep(0.05)
                continue
            keys.tap(XK_ESCAPE)
            continue
        time.sleep(0.05)
    raise RuntimeError(f"didn't reach level {level} within {timeout:.0f} s")


# Plays one run in real time. A decision comes every second tick, like training, and each key
# change is followed in memory to see which tick it reached the game on.
def play_episode(memory, keys, agent, level, max_seconds, poll, double_read, record=None):
    action_keys = {1: XK_LEFT, 2: XK_RIGHT}
    last_action = 0
    first = memory.state()
    print(f"    start: angle {first.player_angle}, sides {first.sides}, run time {first.run_time:.0f}", flush=True)
    start = memory.run_time()
    hold_start = start
    next_decision = start
    cap = start + max_seconds * 60
    lateness = []
    skipped = 0
    waiting = None
    decisions = 0
    torn = 0
    last_rt = start
    odd_since = None
    try:
        while True:
            rt = memory.run_time()
            # In hyper mode gamelogic adds 3600 to the run time partway through a tick and takes
            # it off at the end (decomp/NOTES-input.md), so a read can land 60 s ahead. Only a
            # jump that lasts past a few ticks is real.
            if not last_rt - 0.5 <= rt <= last_rt + 30:
                now = time.monotonic()
                odd_since = odd_since or now
                if now - odd_since < 0.05:
                    time.sleep(poll)
                    continue
            odd_since = None
            last_rt = rt
            # the game reads input right before each update, so a key that made it in time first
            # shows up together with the next tick, and every tick past that is lateness
            if waiting is not None:
                want, sent_at = waiting
                if memory.keys() == want:
                    lateness.append(max(rt - sent_at - 1, 0.0))
                    waiting = None
            # the run time stops once the player dies, so a death can come between decisions
            if rt < next_decision and memory.death_timer() <= 0:
                time.sleep(poll)
                continue
            raw = memory.snapshot()
            # a snapshot taken while a tick ran can mix two ticks, so take it again
            if double_read:
                again = memory.snapshot()
                if again != raw:
                    if again[OFF_RUN_TIME:OFF_RUN_TIME + 4] == raw[OFF_RUN_TIME:OFF_RUN_TIME + 4]:
                        torn += 1
                    continue
            s = memory.parse(raw)
            if s.run_time != rt or memory.run_time() != rt:
                continue
            if s.death_timer > 0 or s.stage < 0:
                return s.run_time / 60, False, lateness, skipped, decisions, torn
            if s.run_time >= cap:
                return s.run_time / 60, True, lateness, skipped, decisions, torn
            if s.run_time > next_decision + 0.5:
                skipped += 1
                print(f"    decision at run time {s.run_time:.1f}, expected {next_decision:.1f}, "
                      f"tick check read {rt:.1f}, wall {time.strftime('%H:%M:%S')}", flush=True)
            obs = build_observation(s, level, last_action, int(round(s.run_time - hold_start)))
            with torch.no_grad():
                action = int(agent.logits(torch.from_numpy(obs)).argmax())
            if record is not None:
                record.append((s.run_time, action, obs))
            decisions += 1
            if action != last_action:
                if last_action:
                    keys.send(action_keys[last_action], False)
                if action:
                    keys.send(action_keys[action], True)
                waiting = ((int(action == 1), int(action == 2)), s.run_time)
                hold_start = s.run_time
                last_action = action
            next_decision = s.run_time + FRAME_SKIP
    finally:
        if last_action:
            keys.send(action_keys[last_action], False)


def main():
    parser = argparse.ArgumentParser(description="plays the real game live, with no lockstep")
    parser.add_argument("model")
    parser.add_argument("--levels", type=int, nargs="+", default=[0])
    parser.add_argument("--episodes", type=int, default=3)
    parser.add_argument("--max-seconds", type=float, default=600.0)
    parser.add_argument("--instance", default=str(PROJECT_DIR / "runs" / "live" / "instance"))
    parser.add_argument("--sound", action="store_true", help="play the game's music")
    parser.add_argument("--poll-ms", type=float, default=0.5, help="how often to check the game's tick")
    parser.add_argument("--single-read", action="store_true",
                        help="take each snapshot once, without checking it against a second copy")
    parser.add_argument("--relaunch-each", action="store_true", help="start a fresh game for every episode")
    parser.add_argument("--record", default=None, help="save each episode's observations and actions here")
    parser.add_argument("--place", type=int, nargs=4, metavar=("X", "Y", "WIDTH", "HEIGHT"),
                        help="move and resize the game window, so several can share the screen")
    args = parser.parse_args()
    torch.set_num_threads(1)

    checkpoint = torch.load(args.model, map_location="cpu", weights_only=True)
    agent = Agent(checkpoint["obs_size"], 3, checkpoint["args"]["hidden"])
    agent.load_state_dict(checkpoint["model"])
    agent.eval()

    process = log = memory = keys = None

    def start_game():
        nonlocal process, log, memory, keys
        process, log = launch_game(args.instance, args.sound)
        memory = GameMemory(process.pid)
        if not wait_until(memory.find_app, 30.0, poll=0.1):
            raise RuntimeError("the game never created its app object")
        window = find_window(process.pid)
        if args.place:
            x, y, width, height = args.place
            subprocess.run(["xdotool", "windowsize", str(window), str(width), str(height),
                            "windowmove", str(window), str(x), str(y)], check=False)
        keys = GameWindowKeys(window)
        wait_until(lambda: memory.state().stage == STAGE_TITLE, 30.0)
        time.sleep(1.0)

    def stop_game():
        if keys:
            keys.close()
        if memory:
            memory.close()
        if process and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
        if log:
            log.close()

    print(f"model {args.model}, live play, decisions every {FRAME_SKIP} ticks", flush=True)
    all_lateness = []
    try:
        start_game()
        for level in args.levels:
            results = []
            for episode in range(args.episodes):
                if args.relaunch_each and episode > 0:
                    stop_game()
                    start_game()
                try:
                    go_to_level(memory, keys, level)
                except EndingReached:
                    # there's no menu path out of the Hyper Hexagonest ending, so relaunch
                    stop_game()
                    start_game()
                    go_to_level(memory, keys, level)
                record = [] if args.record else None
                seconds, capped, lateness, skipped, decisions, torn = play_episode(
                    memory, keys, agent, level, args.max_seconds, args.poll_ms / 1000, not args.single_read,
                    record)
                if record:
                    out = Path(args.record) / f"level{level}-episode{episode + 1:02d}.npz"
                    out.parent.mkdir(parents=True, exist_ok=True)
                    np.savez(out, run_time=np.array([r[0] for r in record]),
                             action=np.array([r[1] for r in record]), obs=np.stack([r[2] for r in record]),
                             seconds=seconds)
                all_lateness.extend(lateness)
                on_time = sum(1 for t in lateness if t < 0.5)
                results.append((seconds, capped))
                print(f"  {LEVEL_NAMES[level]} episode {episode + 1}: {seconds:.2f}s"
                      f"{' (hit the cap)' if capped else ''}, key changes {len(lateness)}, "
                      f"on the next tick {on_time}, late {len(lateness) - on_time}, "
                      f"decisions {decisions}, decision ticks missed {skipped}, torn snapshots {torn}", flush=True)
            seconds = np.array([r[0] for r in results])
            capped = sum(r[1] for r in results)
            print(f"{LEVEL_NAMES[level]:17} {len(results)} episodes: mean {seconds.mean():6.2f}s "
                  f"min {seconds.min():6.2f}s max {seconds.max():6.2f}s, hit the "
                  f"{args.max_seconds:.0f} s cap {capped} times", flush=True)
    finally:
        stop_game()
    if all_lateness:
        late = np.array(all_lateness)
        print(f"key changes {len(late)}: on the next tick {np.mean(late < 0.5):.1%}, "
              f"one tick late {np.mean((late >= 0.5) & (late < 1.5)):.1%}, "
              f"later {np.mean(late >= 1.5):.1%}", flush=True)


if __name__ == "__main__":
    main()
