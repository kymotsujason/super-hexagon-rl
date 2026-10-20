#define _GNU_SOURCE
#include <errno.h>
#include <semaphore.h>
#include <stdint.h>
#include <string.h>
#include <time.h>

#include "shxproto.h"

#define EXPORT __attribute__((visibility("default")))

// Steps every game in one call: posts all the commands first so the games run in parallel,
// then collects each result. Returns -1 when all succeeded, otherwise the index of the first
// game that timed out or reported an error, so the client can find out what went wrong.
EXPORT int shxBatchStep(int count, ShxShared **games, const int32_t *keys, int frameSkip,
	float *obsOut, float *deathOut, float *runTimeOut, int32_t *stageOut, double timeoutSeconds)
{
	for (int i = 0; i < count; i++) {
		ShxShared *sh = games[i];
		sh->command = SHX_CMD_STEP;
		sh->keys = keys[i];
		sh->frameSkip = frameSkip;
		sh->render = 0;
		sem_post(&sh->commandReady);
	}

	struct timespec deadline;
	clock_gettime(CLOCK_REALTIME, &deadline);
	long extraNs = (long)(timeoutSeconds * 1e9);
	deadline.tv_sec += extraNs / 1000000000L;
	deadline.tv_nsec += extraNs % 1000000000L;
	if (deadline.tv_nsec >= 1000000000L) {
		deadline.tv_sec++;
		deadline.tv_nsec -= 1000000000L;
	}

	int failed = -1;
	for (int i = 0; i < count; i++) {
		ShxShared *sh = games[i];
		int rc;
		do {
			rc = sem_timedwait(&sh->resultReady, &deadline);
		} while (rc != 0 && errno == EINTR);
		if (rc != 0 || sh->error != SHX_ERR_NONE) {
			// keep collecting so every game that did answer stays in step with its semaphore
			if (failed < 0)
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
