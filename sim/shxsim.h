#ifndef SHXSIM_H
#define SHXSIM_H

#include <stdint.h>
#include <stdlib.h>

#include "../hook/shxcore.h"
#include "../hook/shxproto.h"

typedef struct ShxSim {
	// same layout as the hook's shared memory, so clients read both the same way
	ShxShared shared;
	uint8_t app[SHX_SUPERHEX_SIZE];
	ShxCore core;
	struct random_data randData;
	char randState[128];
	uint64_t ticks;
	uint64_t millisBase;
	uint64_t tickOffset;
	float gameTimer;
	double lastFrameTime;
	// offsets of the pointers replaced when the dump was loaded
	int pointerCount;
	uint32_t pointerOffsets[8192];
} ShxSim;

// the simulator a thread is stepping, which the hand written runtime helpers read
extern __thread ShxSim *simCurrent;

ShxSim *shxSimCreate(const uint8_t *state, size_t size, uint64_t tickAtDump, uint32_t seed, const uint64_t *ranges,
	int rangeCount);
void shxSimDestroy(ShxSim *sim);
ShxShared *shxSimShared(ShxSim *sim);
const uint8_t *shxSimState(ShxSim *sim);
int shxSimPointerOffsets(ShxSim *sim, uint32_t *out, int max);
int shxSimCommand(ShxSim *sim);
int shxSimBatchStep(int count, ShxSim **sims, const int32_t *keys, int frameSkip, float *obsOut,
	float *deathOut, float *runTimeOut, int32_t *stageOut, int threads);

#endif
