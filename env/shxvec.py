import ctypes
import random
from pathlib import Path

import numpy as np

from shxenv import ACTION_KEYS, LEVEL_COUNT, TICKS_PER_SECOND
from shxgame import OBS_SIZE, PROJECT_DIR, SHX_MAX_REPLAY, STAGE_ENDING, GameError, ShxGame

BATCH_PATH = PROJECT_DIR / "hook" / "shxbatch.so"
BATCH_TIMEOUT = 30.0
ERROR_IN_ENDING = 6
KEY_TABLE = np.array(ACTION_KEYS, dtype=np.int32)

batch_lib = None
if BATCH_PATH.is_file():
    batch_lib = ctypes.CDLL(str(BATCH_PATH))
    batch_lib.shxBatchStep.argtypes = [
        ctypes.c_int, ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p,
        ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_double,
    ]
    batch_lib.shxBatchStep.restype = ctypes.c_int


# Weights levels towards the ones the agent survives least on, with a floor so a level it has
# mastered still comes up. Survival is an average over recent fresh episodes, compared with a
# target length.
class LevelSampler:
    def __init__(self, levels, rng, target_seconds=120.0, floor=0.15, smoothing=0.05):
        self.levels = list(levels)
        self.rng = rng
        self.target = target_seconds
        self.floor = floor
        self.smoothing = smoothing
        self.average = {level: 0.0 for level in self.levels}

    def weights(self):
        return [self.floor + max(0.0, 1.0 - self.average[level] / self.target) for level in self.levels]

    # lets a trainer share one set of averages between several samplers
    def set_averages(self, averages):
        for level in self.levels:
            self.average[level] = averages.get(level, self.average[level])

    def sample(self):
        return self.rng.choices(self.levels, weights=self.weights())[0]

    def record(self, level, seconds):
        if level in self.average:
            self.average[level] += self.smoothing * (seconds - self.average[level])


# Runs many hooked games from one process. step() posts every command before waiting on any, so
# the games work in parallel while this process only moves data. Episodes reset in the same step
# (like gymnasium's same step autoreset), and some deaths restart a few seconds before the death
# by replaying the episode's recorded actions from its seed.
class ShxVecEnv:
    def __init__(self, num_envs, run_dir, levels=tuple(range(LEVEL_COUNT)), frame_skip=2, seed=1,
                 max_episode_seconds=600.0, restart_prob=0.25, restart_back_seconds=(1.0, 3.0),
                 min_restart_seconds=4.0, level_target_seconds=120.0, reversal_cost=0.0, press_cost=0.0):
        if not BATCH_PATH.is_file():
            raise GameError(f"batch library not built, run make in {BATCH_PATH.parent}")
        self.num_envs = num_envs
        self.frame_skip = frame_skip
        self.max_ticks = int(max_episode_seconds * TICKS_PER_SECOND)
        self.restart_prob = restart_prob
        self.restart_back = restart_back_seconds
        self.min_restart_decisions = int(min_restart_seconds * TICKS_PER_SECOND / frame_skip)
        # charged when the action goes straight from left to right or back, for smoother movement
        self.reversal_cost = reversal_cost
        # charged whenever a direction press starts, so holding a key is free and taps aren't
        self.press_cost = press_cost
        self.previous_action = np.zeros(num_envs, dtype=np.int64)
        self.flips = 0
        self.presses = 0
        self.decisions = 0
        self.rng = random.Random(seed)
        self.sampler = LevelSampler(levels, self.rng, target_seconds=level_target_seconds)
        self.obs = np.zeros((num_envs, OBS_SIZE), dtype=np.float32)
        self.instance_dirs = [Path(run_dir) / "envs" / f"g{i:02d}" for i in range(num_envs)]
        self.endings = 0
        self.games = []
        try:
            for i in range(num_envs):
                self.games.append(self._make_game(i))
        except BaseException:
            self.close()
            raise
        self.level = [0] * num_envs
        self.game_seed = [0] * num_envs
        self.fresh = [True] * num_envs
        # each episode's decisions, kept so a death can be replayed from the seed
        self.history = np.zeros((num_envs, SHX_MAX_REPLAY), dtype=np.uint8)
        self.length = np.zeros(num_envs, dtype=np.int64)

        self._shm = (ctypes.c_void_p * num_envs)()
        for i, game in enumerate(self.games):
            self._shm[i] = self._handle(game)
        self._keys = np.zeros(num_envs, dtype=np.int32)
        self._death = np.zeros(num_envs, dtype=np.float32)
        self._run_time = np.zeros(num_envs, dtype=np.float32)
        self._stage = np.zeros(num_envs, dtype=np.int32)

    def _post_new_episode(self, i, allow_restart):
        game = self.games[i]
        length = int(self.length[i])
        if allow_restart and length > self.min_restart_decisions and self.rng.random() < self.restart_prob:
            back = int(self.rng.uniform(*self.restart_back) * TICKS_PER_SECOND / self.frame_skip)
            keep = max(0, length - back)
            self.length[i] = keep
            self.previous_action[i] = self.history[i, keep - 1] if keep > 0 else 0
            self.fresh[i] = False
            game.post_replay(self.level[i], self.game_seed[i], self.history[i, :keep].tobytes(), self.frame_skip)
            return
        self.level[i] = self.sampler.sample()
        self.game_seed[i] = self.rng.randrange(1 << 30)
        self.length[i] = 0
        self.previous_action[i] = 0
        self.fresh[i] = True
        game.post_reset(self.level[i], self.game_seed[i])

    # waits for a posted reset or replay, and relaunches the game if Hyper Hexagonest's ending
    # got in the way, since there's no menu path out of it
    def _finish_new_episode(self, i, allow_restart):
        try:
            self.games[i].wait()
        except GameError:
            if self.games[i].shared.error != ERROR_IN_ENDING:
                raise
            self.endings += 1
            self._relaunch(i)
            self._post_new_episode(i, allow_restart=allow_restart)
            self.games[i].wait()
        self.obs[i] = self.games[i].obs

    def reset(self):
        for i in range(self.num_envs):
            self._post_new_episode(i, allow_restart=False)
        for i in range(self.num_envs):
            self._finish_new_episode(i, allow_restart=False)
        return self.obs.copy()

    def step(self, actions):
        actions = np.asarray(actions, dtype=np.int64)
        flipped = ((actions == 1) & (self.previous_action == 2)) | ((actions == 2) & (self.previous_action == 1))
        pressed = (actions > 0) & (actions != self.previous_action)
        self.flips += int(flipped.sum())
        self.presses += int(pressed.sum())
        self.decisions += self.num_envs
        self.previous_action = actions.copy()
        self._keys[:] = KEY_TABLE[actions]
        rows = np.arange(self.num_envs)
        self.history[rows, np.minimum(self.length, SHX_MAX_REPLAY - 1)] = actions
        self.length = np.minimum(self.length + 1, SHX_MAX_REPLAY)

        failed = self._batch_step()
        if failed >= 0:
            game = self.games[failed]
            process = getattr(game, "process", None)
            if process is not None and process.poll() is not None:
                raise GameError(f"game {failed} exited with code {process.returncode}, see {game.instance_dir}")
            raise GameError(f"game {failed} failed a step: error {game.shared.error}")

        dead = self._death > 0
        # reaching the ending means Hyper Hexagonest was beaten, which ends the episode with no penalty
        beaten = self._stage == STAGE_ENDING
        cut = ~dead & ~beaten & (self._run_time >= self.max_ticks)
        terminated = dead | beaten
        truncated = cut
        rewards = (np.where(dead, -1.0, 0.0) - self.reversal_cost * flipped
                   - self.press_cost * pressed).astype(np.float32)
        final_obs = {}
        finished = []
        ended = np.nonzero(terminated | truncated)[0]
        for i in ended:
            i = int(i)
            if cut[i]:
                final_obs[i] = self.obs[i].copy()
            seconds = float(self._run_time[i]) / TICKS_PER_SECOND
            if self.fresh[i]:
                self.sampler.record(self.level[i], seconds)
            finished.append({"env": i, "level": self.level[i], "seconds": seconds,
                             "fresh": self.fresh[i], "truncated": bool(cut[i]), "beaten": bool(beaten[i])})
            if beaten[i]:
                self.endings += 1
                self._relaunch(i)
        for i in ended:
            self._post_new_episode(int(i), allow_restart=bool(dead[i]))
        for i in ended:
            self._finish_new_episode(int(i), allow_restart=bool(dead[i]))
        return self.obs.copy(), rewards, terminated, truncated, final_obs, finished

    def _make_game(self, i):
        return ShxGame(self.instance_dirs[i], seed=1, headless=True)

    def _batch_step(self):
        return batch_lib.shxBatchStep(
            self.num_envs, self._shm, self._keys.ctypes.data, self.frame_skip, self.obs.ctypes.data,
            self._death.ctypes.data, self._run_time.ctypes.data, self._stage.ctypes.data, BATCH_TIMEOUT)

    # there's no menu path out of the ending, so a fresh game is the clean way back
    def _relaunch(self, i):
        self.games[i].close()
        self.games[i] = self._make_game(i)
        self._shm[i] = self._handle(self.games[i])

    def _handle(self, game):
        return ctypes.addressof(game.shared)

    def close(self):
        for game in self.games:
            game.close()
        self.games = []


# The same vector env on the C simulator, all in this process, with the batch step spread over
# threads. Needs sim/initial_state.* from scripts/check_sim.py.
class ShxSimVecEnv(ShxVecEnv):
    def __init__(self, num_envs, run_dir=None, threads=1, **kwargs):
        from shxsim import ShxSim, sim_lib
        if sim_lib is None:
            raise GameError("simulator not built, run make in sim/")
        self._sim_class = ShxSim
        self._sim_lib = sim_lib
        self.threads = threads
        self._state = None
        super().__init__(num_envs, run_dir or PROJECT_DIR / "runs" / "sim", **kwargs)

    def _make_game(self, i):
        from shxsim import default_ranges_path, default_state_path, default_tick_path
        if self._state is None:
            self._state = (default_state_path().read_bytes(),
                           np.fromfile(default_ranges_path(), dtype=np.uint64).reshape(-1, 2),
                           int(default_tick_path().read_text()))
        state, ranges, tick = self._state
        return self._sim_class(state, ranges, tick)

    def _handle(self, game):
        return game.handle

    def _batch_step(self):
        return self._sim_lib.shxSimBatchStep(
            self.num_envs, self._shm, self._keys.ctypes.data, self.frame_skip, self.obs.ctypes.data,
            self._death.ctypes.data, self._run_time.ctypes.data, self._stage.ctypes.data, self.threads)
