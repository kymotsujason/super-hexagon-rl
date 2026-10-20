import random
from pathlib import Path

import gymnasium as gym
import numpy as np

from shxgame import KEY_LEFT, KEY_RIGHT, STAGE_ENDING, GameError, ShxGame

MAX_SIDES = 6
WALLS_PER_SLOT = 2
LEVEL_COUNT = 6
ACTION_KEYS = (0, KEY_LEFT, KEY_RIGHT)
TICKS_PER_SECOND = 60.0

# gamelogic hits the player when a wall's inner edge is between 145 - speed and 150
PLAYER_RADIUS = 145.0
# walls whose outer edge is this far inside the player radius have fully passed
PASSED_MARGIN = 5.0
MAX_TIME = 3.0
MAX_HOLD = 1.0
MISSING_WALL = (MAX_TIME, 0.0)

SLOT_FEATURES = 1 + 2 * WALLS_PER_SLOT
OBS_SIZE = MAX_SIDES * SLOT_FEATURES + 1 + 3 + 1 + LEVEL_COUNT + len(ACTION_KEYS) + 1


# Egocentric view: slot 0 is the player's slot, and slot r is r slots to the left (the direction
# the angle grows with the left key). Each slot gives a mask bit plus the two nearest walls as
# (seconds until the inner edge reaches the player, seconds the wall takes to pass), so the
# features don't change scale when the wall speed does. The last value is how long the last
# action has been held, so the policy can plan holds.
def build_observation(obs, level, last_action, hold_ticks):
    out = np.zeros(OBS_SIZE, dtype=np.float32)
    sides = obs.sides if 1 <= obs.sides <= MAX_SIDES else MAX_SIDES
    slot_width = 360 // sides
    angle = obs.player_angle % 360
    player_slot = (angle // slot_width) % sides
    speed_per_second = max(obs.wall_speed, 1.0) * TICKS_PER_SECOND

    per_slot = [[] for _ in range(MAX_SIDES)]
    for w in obs.walls[:obs.wall_count]:
        # slots 20 and up are event markers, not walls
        if not 0 <= w.slot < sides or w.width <= 0:
            continue
        if w.distance + w.width < PLAYER_RADIUS - PASSED_MARGIN:
            continue
        start = (w.distance - PLAYER_RADIUS) / speed_per_second
        length = w.width / speed_per_second
        per_slot[(w.slot - player_slot) % sides].append((start, length))

    for r in range(MAX_SIDES):
        base = r * SLOT_FEATURES
        if r >= sides:
            for k in range(WALLS_PER_SLOT):
                out[base + 1 + 2 * k] = MISSING_WALL[0]
            continue
        out[base] = 1.0
        walls = sorted(per_slot[r])
        for k in range(WALLS_PER_SLOT):
            start, length = walls[k] if k < len(walls) else MISSING_WALL
            out[base + 1 + 2 * k] = min(max(start, -1.0), MAX_TIME)
            out[base + 2 + 2 * k] = min(max(length, 0.0), MAX_TIME)

    i = MAX_SIDES * SLOT_FEATURES
    out[i] = (angle % slot_width) / slot_width - 0.5
    i += 1
    if 4 <= sides <= 6:
        out[i + sides - 4] = 1.0
    i += 3
    out[i] = obs.wall_speed / 40.0
    i += 1
    out[i + level] = 1.0
    i += LEVEL_COUNT
    out[i + last_action] = 1.0
    i += len(ACTION_KEYS)
    out[i] = min(max(hold_ticks / TICKS_PER_SECOND, 0.0), MAX_HOLD)
    return out


class ShxEnv(gym.Env):
    metadata = {"render_modes": []}

    def __init__(self, instance_dir, levels=(0,), frame_skip=2, headless=True, seed=None,
                 max_episode_seconds=120.0, render=False):
        self.instance_dir = Path(instance_dir)
        self.levels = tuple(levels)
        self.frame_skip = frame_skip
        self.headless = headless
        self.max_ticks = int(max_episode_seconds * TICKS_PER_SECOND)
        self.render_frames = render
        self.rng = random.Random(seed)
        self.game = None
        self.level = self.levels[0]
        self.last_action = 0
        self.hold_ticks = 0
        self.observation_space = gym.spaces.Box(-np.inf, np.inf, shape=(OBS_SIZE,), dtype=np.float32)
        self.action_space = gym.spaces.Discrete(len(ACTION_KEYS))

    def reset(self, seed=None, options=None):
        super().reset(seed=seed)
        if seed is not None:
            self.rng = random.Random(seed)
        # the game process starts on first use, so vector env workers each launch their own. Its
        # launch seed is fixed and doesn't touch self.rng, since every reset reseeds the game anyway
        # there's no menu path out of the ending, so a fresh process is the clean way back
        if self.game is not None and self.game.shared.stage == STAGE_ENDING:
            self.game.close()
            self.game = None
        if self.game is None:
            self.game = ShxGame(self.instance_dir, seed=1, headless=self.headless)
        options = options or {}
        self.level = options.get("level", self.rng.choice(self.levels))
        game_seed = options.get("game_seed", self.rng.randrange(1 << 30))
        try:
            obs = self.game.reset(level=self.level, seed=game_seed, render=self.render_frames)
        except GameError:
            # Hyper Hexagonest's ending has no menu path out, so start a fresh process
            if self.game.shared.error != 6:
                raise
            self.game.close()
            self.game = ShxGame(self.instance_dir, seed=1, headless=self.headless)
            obs = self.game.reset(level=self.level, seed=game_seed, render=self.render_frames)
        self.last_action = 0
        self.hold_ticks = 0
        return build_observation(obs, self.level, 0, 0), {"level": self.level, "game_seed": game_seed}

    def step(self, action):
        if self.game is None:
            raise RuntimeError("reset() has to be called before step()")
        action = int(action)
        obs = self.game.step(ACTION_KEYS[action], frame_skip=self.frame_skip, render=self.render_frames)
        self.hold_ticks = self.hold_ticks + obs.ticks_run if action == self.last_action else obs.ticks_run
        self.last_action = action
        dead = obs.death_timer > 0
        # reaching the ending means Hyper Hexagonest was beaten, which ends the episode with no penalty
        beaten = obs.stage == STAGE_ENDING
        truncated = not dead and not beaten and obs.run_time >= self.max_ticks
        reward = -1.0 if dead else 0.0
        info = {}
        if dead or truncated or beaten:
            info = {"run_seconds": obs.run_time / TICKS_PER_SECOND, "level": self.level, "beaten": beaten}
        return build_observation(obs, self.level, action, self.hold_ticks), reward, dead or beaten, truncated, info

    def close(self):
        if self.game is not None:
            self.game.close()
            self.game = None
