#ifndef SHXPROTO_H
#define SHXPROTO_H

#include <semaphore.h>
#include <stdint.h>

#define SHX_MAGIC 0x31584853u
#define SHX_PROTO_VERSION 5
#define SHX_MAX_WALLS 256
#define SHX_MAX_REPLAY 32768

// size of the superhex object the dump command writes
#define SHX_SUPERHEX_SIZE 0x3c758

// observation layout, env/shxenv.py documents each field
#define SHX_OBS_MAX_SIDES 6
#define SHX_OBS_WALLS_PER_SLOT 2
#define SHX_OBS_SLOT_FEATURES (1 + 2 * SHX_OBS_WALLS_PER_SLOT)
#define SHX_OBS_SIZE (SHX_OBS_MAX_SIDES * SHX_OBS_SLOT_FEATURES + 1 + 3 + 1 + 6 + 3 + 1)

enum {
	SHX_CMD_NONE = 0,
	SHX_CMD_STEP = 1,
	SHX_CMD_QUIT = 2,
	SHX_CMD_RESET = 3,
	SHX_CMD_REPLAY = 4,
	SHX_CMD_DUMP = 5,
};

// reset flags, for starting a level the way a live game does instead of from a clean state
enum {
	SHX_RESET_KEEP_RANDOM = 1,
	SHX_RESET_KEEP_ANGLE = 2,
	// walk to level select and stop there, so a client can browse the levels before starting
	SHX_RESET_STOP_AT_SELECT = 4,
};

enum {
	SHX_KEY_LEFT = 1,
	SHX_KEY_RIGHT = 2,
	SHX_KEY_UP = 4,
	SHX_KEY_SELECT = 8,
};

// levels in the order the client uses, hyper versions after the normal ones
enum {
	SHX_LEVEL_HEXAGON = 0,
	SHX_LEVEL_HEXAGONER = 1,
	SHX_LEVEL_HEXAGONEST = 2,
	SHX_LEVEL_HYPER_HEXAGON = 3,
	SHX_LEVEL_HYPER_HEXAGONER = 4,
	SHX_LEVEL_HYPER_HEXAGONEST = 5,
	SHX_LEVEL_COUNT = 6,
};

enum {
	SHX_ERR_NONE = 0,
	SHX_ERR_BAD_BUILD = 1,
	SHX_ERR_BAD_COMMAND = 2,
	SHX_ERR_RESET_TIMEOUT = 3,
	SHX_ERR_RESET_REFUSED = 4,
	SHX_ERR_REPLAY_DIED = 5,
	SHX_ERR_IN_ENDING = 6,
	SHX_ERR_DUMP_FAILED = 7,
};

typedef struct {
	int32_t slot;
	int32_t distance;
	int32_t width;
	int32_t type;
} ShxWall;

// env/shxgame.py mirrors this layout, and it creates the block and initializes both semaphores
// before starting the game
typedef struct {
	// filled by the client
	uint32_t magic;
	uint32_t version;
	uint32_t structSize;
	uint32_t clientPid;
	sem_t commandReady;
	sem_t resultReady;

	// command, written by the client before posting commandReady
	int32_t command;
	int32_t keys;
	int32_t frameSkip;
	int32_t render;
	int32_t level;
	uint32_t seed;
	int32_t replayCount;
	int32_t resetFlags;

	// result, written by the hook before posting resultReady
	uint64_t tick;
	int32_t hookReady;
	int32_t error;
	int32_t ticksRun;
	int32_t stage;
	int32_t playerAngle;
	int32_t sides;
	float deathTimer;
	float runTime;
	float wallSpeed;
	float hitFlag;
	int32_t cameraDistance;
	int32_t rotationMode;
	int32_t hyper;
	int32_t wallCount;
	int32_t resetTicks;
	int32_t tutorialActive;
	int32_t lastAction;
	int32_t pad2;
	float obs[SHX_OBS_SIZE];
	ShxWall walls[SHX_MAX_WALLS];

	// actions for SHX_CMD_REPLAY (0 none, 1 left, 2 right), one per decision
	uint8_t replayActions[SHX_MAX_REPLAY];
} ShxShared;

#endif
