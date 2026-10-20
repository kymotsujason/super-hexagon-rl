#ifndef SHXCORE_H
#define SHXCORE_H

// Game logic driving shared by the hook (the real game) and the simulator (sim/), so both walk
// the menus, step decisions, and build observations the same way. Only the tick differs.

#include <stdbool.h>
#include <stdint.h>

#include "shxproto.h"

// superhex field offsets, see decomp/NOTES-core.md and decomp/NOTES-input.md
#define OFF_LATCH 0x50
#define OFF_DEATH_TIMER 0x104
#define OFF_CAMERA_DISTANCE 0x10c
#define OFF_SIDES 0x114
#define OFF_WALLS 0x180
#define OFF_WALL_COUNT 0x2890
#define OFF_RUN_TIME 0x2894
#define OFF_WALL_SPEED 0x28d8
#define OFF_PREVIOUS_ANGLE 0x28ec
#define OFF_PLAYER_ANGLE 0x28f0
#define OFF_HIT_FLAG 0x28f8
#define OFF_ROTATION_MODE 0x2900
#define OFF_STAGE 0x5424
#define OFF_TITLE_SUBMENU 0x5460
#define OFF_LEVEL_CURSOR 0x5464
#define OFF_HYPER 0x5478
#define OFF_TUTORIAL_ACTIVE 0x5575
#define OFF_LEVEL_MESSAGE 0x5584
// set while a run that changed stage partway holds its stage, cleared after the death sequence
#define OFF_STAGE_CHANGE_A 0x55dc
#define OFF_STAGE_CHANGE_B 0x55e4
#define OFF_TITLE_CURSOR 0xbfc0
#define OFF_KEYS 0x3c07c
#define OFF_ELAPSED_MILLIS 0x3c710

#define WALL_RECORD_SIZE 0x14
#define WALL_CAPACITY 500
#define LEVEL_MESSAGE_ENDING 3
#define GAME_OVER_CAMERA 320
#define MAX_FRAME_SKIP 600
#define RESET_TICK_LIMIT 2000
#define MAX_HOLD_TICKS 3600

#define STAGE_TITLE -1
#define STAGE_LEVEL_SELECT -2
#define STAGE_ENDING 4

// key codes after the window layer's translation
#define KEY_ENTER 0x0d
#define KEY_ESCAPE 0x1b
#define KEY_LEFT 0x164
#define KEY_UP 0x165
#define KEY_RIGHT 0x166

#define FIELD(type, app, offset) (*(type *)((app) + (offset)))

typedef struct ShxCore ShxCore;
struct ShxCore {
	uint8_t *app;
	int level;
	int lastAction;
	// ticks the current action has been held, counted the same way during a replay
	int holdTicks;
	// runs one 1/60 s tick of superhex::update with the keys already set
	void (*tick)(ShxCore *core);
	void (*reseed)(ShxCore *core, uint32_t seed);
	void (*log)(const char *fmt, ...);
	void *user;
};

static const int shxActionKeys[3] = { 0, SHX_KEY_LEFT, SHX_KEY_RIGHT };

// level select cursor for each SHX_LEVEL value, from gameinput lines 156-206
static const int shxLevelCursor[SHX_LEVEL_COUNT] = { 0, 5, 4, 3, 2, 1 };

static void shxRunTick(ShxCore *c, int keys, bool escape)
{
	uint8_t *keyState = c->app + OFF_KEYS;
	keyState[KEY_LEFT] = (keys & SHX_KEY_LEFT) != 0;
	keyState[KEY_RIGHT] = (keys & SHX_KEY_RIGHT) != 0;
	keyState[KEY_UP] = (keys & SHX_KEY_UP) != 0;
	keyState[KEY_ENTER] = (keys & SHX_KEY_SELECT) != 0;
	keyState[KEY_ESCAPE] = escape;
	c->tick(c);
}

static int shxKeysToAction(int keys)
{
	if (keys == SHX_KEY_LEFT)
		return 1;
	if (keys == SHX_KEY_RIGHT)
		return 2;
	return 0;
}

static void shxTrackHold(ShxCore *c, int action, int ticksRun)
{
	if (action == c->lastAction && c->holdTicks < MAX_HOLD_TICKS)
		c->holdTicks += ticksRun;
	else if (action != c->lastAction)
		c->holdTicks = ticksRun;
	c->lastAction = action;
}

static int shxClampFrameSkip(int frameSkip)
{
	if (frameSkip < 1)
		return 1;
	if (frameSkip > MAX_FRAME_SKIP)
		return MAX_FRAME_SKIP;
	return frameSkip;
}

// one decision, which stops early on the tick a death starts
static int shxRunDecision(ShxCore *c, int keys, int frameSkip)
{
	int ticksRun = 0;
	for (int i = 0; i < frameSkip; i++) {
		float deathBefore = FIELD(float, c->app, OFF_DEATH_TIMER);
		shxRunTick(c, keys, false);
		ticksRun++;
		if (deathBefore == 0.0f && FIELD(float, c->app, OFF_DEATH_TIMER) > 0.0f)
			break;
	}
	return ticksRun;
}

// Walks the game's own menus to level select, then reseeds rand and starts the level. Escape is
// only sent during a run, since on the title screen it quits the game. Every press is followed
// by a tick with no keys, because most menu presses set a latch that ignores input until then.
static int shxRunReset(ShxCore *c, int level, uint32_t seed, int flags, int *ticksOut)
{
	uint8_t *app = c->app;
	*ticksOut = 0;
	if (level < 0 || level >= SHX_LEVEL_COUNT)
		return SHX_ERR_BAD_COMMAND;

	bool releaseNext = false;
	for (int n = 0; n < RESET_TICK_LIMIT; n++) {
		*ticksOut = n + 1;
		int stage = FIELD(int32_t, app, OFF_STAGE);
		// passing 60 s on Hyper Hexagonest marks the ending (level message 3), which starts on the
		// game over screen after the next death. The player can't die there and Escape does
		// nothing, so the client relaunches the game instead
		if (stage == STAGE_ENDING || FIELD(int32_t, app, OFF_LEVEL_MESSAGE) == LEVEL_MESSAGE_ENDING) {
			c->log("reset to level %d refused: the game is in its ending", level);
			return SHX_ERR_IN_ENDING;
		}
		if (releaseNext || FIELD(uint8_t, app, OFF_LATCH)) {
			shxRunTick(c, 0, false);
			releaseNext = false;
			continue;
		}

		if (stage == STAGE_LEVEL_SELECT) {
			if (flags & SHX_RESET_STOP_AT_SELECT)
				return SHX_ERR_NONE;
			FIELD(int32_t, app, OFF_LEVEL_CURSOR) = shxLevelCursor[level];
			// a run keeps the angle the menu left the player at, which depends on the last level
			// played, so start every run from the angle gameclass::start uses, unless asked to
			// start the way a live game does
			if (!(flags & SHX_RESET_KEEP_ANGLE)) {
				int sides = FIELD(int32_t, app, OFF_SIDES);
				int startAngle = sides > 0 ? 180 / sides : 30;
				FIELD(int32_t, app, OFF_PLAYER_ANGLE) = startAngle;
				FIELD(int32_t, app, OFF_PREVIOUS_ANGLE) = startAngle;
			}
			if (!(flags & SHX_RESET_KEEP_RANDOM))
				c->reseed(c, seed);
			shxRunTick(c, SHX_KEY_SELECT, false);
			bool started = FIELD(int32_t, app, OFF_STAGE) >= 0 && FIELD(float, app, OFF_DEATH_TIMER) == 0.0f;
			if (!started) {
				c->log("level %d didn't start from level select, is it locked?", level);
				return SHX_ERR_RESET_REFUSED;
			}
			return SHX_ERR_NONE;
		}

		if (stage == STAGE_TITLE) {
			if (FIELD(int32_t, app, OFF_TITLE_SUBMENU) != 0) {
				c->log("title submenu %d is open, refusing to press select there",
					FIELD(int32_t, app, OFF_TITLE_SUBMENU));
				return SHX_ERR_RESET_REFUSED;
			}
			FIELD(int32_t, app, OFF_TITLE_CURSOR) = 0;
			shxRunTick(c, SHX_KEY_SELECT, false);
			releaseNext = true;
			continue;
		}

		if (stage >= 0) {
			bool dead = FIELD(float, app, OFF_DEATH_TIMER) > 0.0f;
			// a run that finishes a level shows a message on the game over screen, and gameinput
			// ignores every key but select until it's dismissed (gameinput lines 314-360)
			if (dead && FIELD(int32_t, app, OFF_LEVEL_MESSAGE) > 0) {
				if (FIELD(int32_t, app, OFF_CAMERA_DISTANCE) != GAME_OVER_CAMERA) {
					shxRunTick(c, 0, false);
					continue;
				}
				shxRunTick(c, SHX_KEY_SELECT, false);
				releaseNext = true;
				continue;
			}
			// Escape ends a live run. After a death, wait for the game over screen (camera at 320)
			// before leaving, since a run that changed stage partway puts the stage back every
			// tick until the death sequence finishes (gamelogic lines 277 and 1838)
			bool stageHeld = FIELD(int32_t, app, OFF_STAGE_CHANGE_A) == 2
				|| FIELD(int32_t, app, OFF_STAGE_CHANGE_B) == 1;
			if (dead && (stageHeld || FIELD(int32_t, app, OFF_CAMERA_DISTANCE) != GAME_OVER_CAMERA)) {
				shxRunTick(c, 0, false);
				continue;
			}
			shxRunTick(c, 0, true);
			releaseNext = true;
			continue;
		}

		shxRunTick(c, 0, false);
	}
	c->log("reset to level %d didn't reach the level within %d ticks: stage %d death %.1f camera %d "
		"latch %d submenu %d run time %.0f change flags %d %d level message %d", level, RESET_TICK_LIMIT,
		FIELD(int32_t, app, OFF_STAGE), FIELD(float, app, OFF_DEATH_TIMER),
		FIELD(int32_t, app, OFF_CAMERA_DISTANCE), FIELD(uint8_t, app, OFF_LATCH),
		FIELD(int32_t, app, OFF_TITLE_SUBMENU), FIELD(float, app, OFF_RUN_TIME),
		FIELD(int32_t, app, OFF_STAGE_CHANGE_A), FIELD(int32_t, app, OFF_STAGE_CHANGE_B),
		FIELD(int32_t, app, OFF_LEVEL_MESSAGE));
	return SHX_ERR_RESET_TIMEOUT;
}

// Mirrors build_observation in env/shxenv.py, which stays as the reference the two are checked
// against. Python works in doubles and stores float32, so this does the same.
#define OBS_PLAYER_RADIUS 145.0
#define OBS_PASSED_MARGIN 5.0
#define OBS_MAX_TIME 3.0
#define OBS_MAX_HOLD 1.0

static int shxFloorMod(int a, int b)
{
	int r = a % b;
	return r < 0 ? r + b : r;
}

static double shxClamp(double v, double lo, double hi)
{
	return v < lo ? lo : (v > hi ? hi : v);
}

static void shxBuildObservation(ShxShared *sh, int level, int lastActionIndex, int holdTicks)
{
	float *out = sh->obs;
	for (int i = 0; i < SHX_OBS_SIZE; i++)
		out[i] = 0.0f;

	int sides = (sh->sides >= 1 && sh->sides <= SHX_OBS_MAX_SIDES) ? sh->sides : SHX_OBS_MAX_SIDES;
	int slotWidth = 360 / sides;
	int angle = shxFloorMod(sh->playerAngle, 360);
	int playerSlot = (angle / slotWidth) % sides;
	double speedPerSecond = ((double)sh->wallSpeed > 1.0 ? (double)sh->wallSpeed : 1.0) * 60.0;

	// the two smallest (start, length) pairs per slot, ordered like Python's tuple sort
	double nearStart[SHX_OBS_MAX_SIDES][SHX_OBS_WALLS_PER_SLOT];
	double nearLength[SHX_OBS_MAX_SIDES][SHX_OBS_WALLS_PER_SLOT];
	int nearCount[SHX_OBS_MAX_SIDES] = { 0 };
	for (int i = 0; i < sh->wallCount; i++) {
		ShxWall *w = &sh->walls[i];
		if (w->slot < 0 || w->slot >= sides || w->width <= 0)
			continue;
		if ((double)(w->distance + w->width) < OBS_PLAYER_RADIUS - OBS_PASSED_MARGIN)
			continue;
		double start = ((double)w->distance - OBS_PLAYER_RADIUS) / speedPerSecond;
		double length = (double)w->width / speedPerSecond;
		int r = shxFloorMod(w->slot - playerSlot, sides);
		int n = nearCount[r];
		int pos = n;
		while (pos > 0 && (start < nearStart[r][pos - 1]
			|| (start == nearStart[r][pos - 1] && length < nearLength[r][pos - 1])))
			pos--;
		if (pos >= SHX_OBS_WALLS_PER_SLOT)
			continue;
		int last = n < SHX_OBS_WALLS_PER_SLOT ? n : SHX_OBS_WALLS_PER_SLOT - 1;
		for (int k = last; k > pos; k--) {
			nearStart[r][k] = nearStart[r][k - 1];
			nearLength[r][k] = nearLength[r][k - 1];
		}
		nearStart[r][pos] = start;
		nearLength[r][pos] = length;
		if (n < SHX_OBS_WALLS_PER_SLOT)
			nearCount[r] = n + 1;
	}

	for (int r = 0; r < SHX_OBS_MAX_SIDES; r++) {
		int base = r * SHX_OBS_SLOT_FEATURES;
		if (r >= sides) {
			for (int k = 0; k < SHX_OBS_WALLS_PER_SLOT; k++)
				out[base + 1 + 2 * k] = (float)OBS_MAX_TIME;
			continue;
		}
		out[base] = 1.0f;
		for (int k = 0; k < SHX_OBS_WALLS_PER_SLOT; k++) {
			double start = k < nearCount[r] ? nearStart[r][k] : OBS_MAX_TIME;
			double length = k < nearCount[r] ? nearLength[r][k] : 0.0;
			out[base + 1 + 2 * k] = (float)shxClamp(start, -1.0, OBS_MAX_TIME);
			out[base + 2 + 2 * k] = (float)shxClamp(length, 0.0, OBS_MAX_TIME);
		}
	}

	int i = SHX_OBS_MAX_SIDES * SHX_OBS_SLOT_FEATURES;
	out[i] = (float)((double)(angle % slotWidth) / slotWidth - 0.5);
	i += 1;
	if (sides >= 4 && sides <= 6)
		out[i + sides - 4] = 1.0f;
	i += 3;
	out[i] = (float)((double)sh->wallSpeed / 40.0);
	i += 1;
	if (level >= 0 && level < SHX_LEVEL_COUNT)
		out[i + level] = 1.0f;
	i += SHX_LEVEL_COUNT;
	if (lastActionIndex >= 0 && lastActionIndex < 3)
		out[i + lastActionIndex] = 1.0f;
	i += 3;
	out[i] = (float)shxClamp(holdTicks / 60.0, 0.0, OBS_MAX_HOLD);
}

static void shxWriteResult(ShxCore *c, ShxShared *sh, uint64_t tick)
{
	uint8_t *app = c->app;
	sh->tick = tick;
	sh->stage = FIELD(int32_t, app, OFF_STAGE);
	sh->playerAngle = FIELD(int32_t, app, OFF_PLAYER_ANGLE);
	sh->sides = FIELD(int32_t, app, OFF_SIDES);
	sh->deathTimer = FIELD(float, app, OFF_DEATH_TIMER);
	sh->runTime = FIELD(float, app, OFF_RUN_TIME);
	sh->wallSpeed = FIELD(float, app, OFF_WALL_SPEED);
	sh->hitFlag = FIELD(float, app, OFF_HIT_FLAG);
	sh->cameraDistance = FIELD(int32_t, app, OFF_CAMERA_DISTANCE);
	sh->rotationMode = FIELD(int32_t, app, OFF_ROTATION_MODE);
	sh->hyper = FIELD(uint8_t, app, OFF_HYPER);
	sh->tutorialActive = FIELD(uint8_t, app, OFF_TUTORIAL_ACTIVE);

	int used = FIELD(int32_t, app, OFF_WALL_COUNT);
	if (used < 0)
		used = 0;
	if (used > WALL_CAPACITY)
		used = WALL_CAPACITY;
	int count = 0;
	for (int i = 0; i < used && count < SHX_MAX_WALLS; i++) {
		uint8_t *record = app + OFF_WALLS + i * WALL_RECORD_SIZE;
		if (!record[0x10])
			continue;
		ShxWall *wall = &sh->walls[count++];
		wall->slot = FIELD(int32_t, record, 0x0);
		wall->distance = FIELD(int32_t, record, 0x4);
		wall->width = FIELD(int32_t, record, 0x8);
		wall->type = FIELD(int32_t, record, 0xc);
	}
	sh->wallCount = count;
	sh->lastAction = c->lastAction;
	shxBuildObservation(sh, c->level, c->lastAction, c->holdTicks);
}

// Runs a step, reset, or replay command from sh and fills in the error and tick counts. Returns
// false for any other command, which the caller handles itself.
static bool shxHandleCommand(ShxCore *c, ShxShared *sh, int *error, int *ticksRun, int *resetTicks)
{
	int command = sh->command;
	*error = SHX_ERR_NONE;
	*ticksRun = 0;
	*resetTicks = 0;
	if (command == SHX_CMD_STEP) {
		int keys = sh->keys;
		*ticksRun = shxRunDecision(c, keys, shxClampFrameSkip(sh->frameSkip));
		shxTrackHold(c, shxKeysToAction(keys), *ticksRun);
		return true;
	}
	if (command != SHX_CMD_RESET && command != SHX_CMD_REPLAY)
		return false;

	*error = shxRunReset(c, sh->level, sh->seed, sh->resetFlags, resetTicks);
	if (*error == SHX_ERR_NONE) {
		c->level = sh->level;
		c->lastAction = 0;
		c->holdTicks = 0;
	}
	// replay a recorded prefix from the reseeded start, which lands on the same state
	if (*error == SHX_ERR_NONE && command == SHX_CMD_REPLAY) {
		int count = sh->replayCount;
		if (count < 0 || count > SHX_MAX_REPLAY)
			count = 0;
		int frameSkip = shxClampFrameSkip(sh->frameSkip);
		for (int i = 0; i < count; i++) {
			int action = sh->replayActions[i];
			if (action < 0 || action > 2)
				action = 0;
			int ticks = shxRunDecision(c, shxActionKeys[action], frameSkip);
			shxTrackHold(c, action, ticks);
			if (FIELD(float, c->app, OFF_DEATH_TIMER) > 0.0f) {
				*error = SHX_ERR_REPLAY_DIED;
				break;
			}
		}
	}
	return true;
}

#endif
