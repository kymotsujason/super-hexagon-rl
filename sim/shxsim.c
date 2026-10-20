#define _GNU_SOURCE
#include <stdarg.h>
#include <stdio.h>
#include <string.h>

#include "gen/logic.h"
#include "shxsim.h"

#define EXPORT __attribute__((visibility("default")))

// the hook's virtual clock, whose frame time superhex::update sees
#define SIM_FREQ 60000000ULL
#define SIM_COUNTS_PER_TICK (SIM_FREQ / 60)

static void simLog(const char *fmt, ...)
{
	if (!getenv("SHX_SIM_LOG"))
		return;
	va_list args;
	va_start(args, fmt);
	vfprintf(stderr, fmt, args);
	va_end(args);
	fputc('\n', stderr);
}

static void simUse(ShxSim *sim)
{
	simCurrent = sim;
	GameTimer__instance = &sim->gameTimer;
}

// superhex::update (0x47a440) for one tick
static void simTick(ShxCore *core)
{
	ShxSim *sim = core->user;
	uint8_t *app = core->app;
	sim->ticks++;

	double frame = (double)SIM_COUNTS_PER_TICK / (double)SIM_FREQ;
	FIELD(double, app, 0x3c718) = frame;
	double expected = FIELD(double, app, 0x3c728);
	double twice = expected + expected;
	if (twice <= frame) {
		FIELD(double, app, 0x3c718) = expected;
		frame = expected;
	}
	if (twice <= frame)
		FIELD(double, app, 0x3c738) = expected + FIELD(double, app, 0x3c738);
	else
		FIELD(double, app, 0x3c738) = frame + FIELD(double, app, 0x3c738);
	sim->lastFrameTime = FIELD(double, app, 0x3c718);

	superhex__calculateTime(app);
	helpclass__updateglow(app + 0x3bf30, FIELD(double, app, 0x28c0));
	superhex__gameinput(app);
	superhex__gamelogic(app);
	musicclass__processmusic(app + 0x1e4a8);
	if (app[0x3c466])
		app[0x3c464] = 0;
	// draw() stores this in the real game, and the hook stores it after each tick without drawing
	FIELD(uint64_t, app, OFF_ELAPSED_MILLIS) = (uint32_t)ofGetElapsedTimeMillis();
}

static void simReseed(ShxCore *core, uint32_t seed)
{
	ShxSim *sim = core->user;
	srandom_r(seed, &sim->randData);
}

// The dump holds pointers into the real game's memory (strings, vectors, sound players). Gameplay
// never follows them, but the string code checks reference counts through them, so each one
// becomes a pointer to our own empty string. ranges are [start, end) pairs from the game's
// /proc/<pid>/maps, and the game's own empty string at 0x7f56f8 counts too.
static void replacePointers(ShxSim *sim, const uint64_t *ranges, int rangeCount)
{
	sim->pointerCount = 0;
	for (size_t off = 0; off + 8 <= SHX_SUPERHEX_SIZE; off += 8) {
		uint64_t value = FIELD(uint64_t, sim->app, off);
		bool pointer = value == 0x7f56f8;
		for (int r = 0; r < rangeCount && !pointer; r++)
			pointer = value >= ranges[2 * r] && value < ranges[2 * r + 1];
		if (!pointer)
			continue;
		FIELD(uint64_t, sim->app, off) = SHX_EMPTY_STRING_DATA;
		if (sim->pointerCount < (int)(sizeof(sim->pointerOffsets) / sizeof(sim->pointerOffsets[0])))
			sim->pointerOffsets[sim->pointerCount++] = (uint32_t)off;
	}
}

EXPORT ShxSim *shxSimCreate(const uint8_t *state, size_t size, uint64_t tickAtDump, uint32_t seed,
	const uint64_t *ranges, int rangeCount)
{
	if (size != SHX_SUPERHEX_SIZE)
		return NULL;
	ShxSim *sim = calloc(1, sizeof(ShxSim));
	if (!sim)
		return NULL;
	memcpy(sim->app, state, SHX_SUPERHEX_SIZE);
	replacePointers(sim, ranges, rangeCount);
	if (initstate_r(seed, sim->randState, sizeof(sim->randState), &sim->randData) != 0) {
		free(sim);
		return NULL;
	}
	sim->millisBase = FIELD(uint64_t, sim->app, OFF_ELAPSED_MILLIS);
	sim->tickOffset = tickAtDump;
	sim->gameTimer = 1.0f;
	sim->core.app = sim->app;
	sim->core.tick = simTick;
	sim->core.reseed = simReseed;
	sim->core.log = simLog;
	sim->core.user = sim;
	sim->shared.magic = SHX_MAGIC;
	sim->shared.version = SHX_PROTO_VERSION;
	sim->shared.structSize = sizeof(ShxShared);
	sim->shared.hookReady = 1;
	shxWriteResult(&sim->core, &sim->shared, sim->ticks);
	return sim;
}

// Puts in a random number state taken from a live game (the hook's 128 byte state buffer and its
// two read positions), so a captured live game can continue in the simulator.
EXPORT int shxSimSetRandom(ShxSim *sim, const char *state, int size, int frontIndex, int rearIndex)
{
	int degree = sim->randData.rand_deg;
	if (size != (int)sizeof(sim->randState) || frontIndex < 0 || frontIndex >= degree
		|| rearIndex < 0 || rearIndex >= degree)
		return -1;
	memcpy(sim->randState, state, sizeof(sim->randState));
	sim->randData.fptr = sim->randData.state + frontIndex;
	sim->randData.rptr = sim->randData.state + rearIndex;
	return 0;
}

EXPORT void shxSimDestroy(ShxSim *sim)
{
	free(sim);
}

EXPORT ShxShared *shxSimShared(ShxSim *sim)
{
	return &sim->shared;
}

EXPORT const uint8_t *shxSimState(ShxSim *sim)
{
	return sim->app;
}

EXPORT int shxSimPointerOffsets(ShxSim *sim, uint32_t *out, int max)
{
	int n = sim->pointerCount < max ? sim->pointerCount : max;
	memcpy(out, sim->pointerOffsets, (size_t)n * sizeof(uint32_t));
	return sim->pointerCount;
}

// runs whatever command sits in the simulator's shared block, like the hook's lockstep loop
EXPORT int shxSimCommand(ShxSim *sim)
{
	simUse(sim);
	ShxShared *sh = &sim->shared;
	int error, ticksRun, resetTicks;
	if (!shxHandleCommand(&sim->core, sh, &error, &ticksRun, &resetTicks))
		error = sh->command == SHX_CMD_DUMP ? SHX_ERR_NONE : SHX_ERR_BAD_COMMAND;
	sh->error = error;
	sh->ticksRun = ticksRun;
	sh->resetTicks = resetTicks;
	shxWriteResult(&sim->core, sh, sim->ticks);
	return error;
}

// Steps every simulator once, across threads. Returns -1 when all succeeded, otherwise the index
// of the first one that failed.
EXPORT int shxSimBatchStep(int count, ShxSim **sims, const int32_t *keys, int frameSkip, float *obsOut,
	float *deathOut, float *runTimeOut, int32_t *stageOut, int threads)
{
	int failed = -1;
	#pragma omp parallel for schedule(static) num_threads(threads > 0 ? threads : 1)
	for (int i = 0; i < count; i++) {
		ShxSim *sim = sims[i];
		ShxShared *sh = &sim->shared;
		sh->command = SHX_CMD_STEP;
		sh->keys = keys[i];
		sh->frameSkip = frameSkip;
		if (shxSimCommand(sim) != SHX_ERR_NONE) {
			#pragma omp critical
			if (failed < 0 || i < failed)
				failed = i;
			continue;
		}
		memcpy(obsOut + (size_t)i * SHX_OBS_SIZE, sh->obs, sizeof(sh->obs));
		deathOut[i] = sh->deathTimer;
		runTimeOut[i] = sh->runTime;
		stageOut[i] = sh->stage;
	}
	return failed;
}
