#define _GNU_SOURCE
#include <dlfcn.h>
#include <errno.h>
#include <fcntl.h>
#include <pthread.h>
#include <signal.h>
#include <stdarg.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <sys/mman.h>
#include <sys/stat.h>
#include <time.h>
#include <unistd.h>

#include "shxcore.h"
#include "shxproto.h"

#define EXPORT __attribute__((visibility("default")))

// the game's loop divides this by 60, so it has to be a multiple of 60 for an exact period
#define VIRTUAL_FREQ 60000000ULL
#define COUNTS_PER_TICK (VIRTUAL_FREQ / 60)

// fixed addresses in the non-PIE executable (buildid 8838351), see decomp/NOTES-core.md
#define ADDR_SUPERHEXAGON_APP 0x7f5808UL
#define ADDR_WINDOW 0x7f5908UL
#define ADDR_SUPERHEX_VPTR 0x596710UL
#define ADDR_SUPERHEX_UPDATE 0x47a440UL
#define ADDR_SUPERHEX_DRAW 0x47a540UL
#define ADDR_WINDOW_DISPLAY 0x485ff0UL
#define ADDR_OF_ELAPSED_MILLIS 0x4863f0UL

// the executable is non-PIE and maps its code between these addresses
#define EXE_TEXT_START 0x400000UL
#define EXE_TEXT_END 0x800000UL




#define SDL_QUIT_EVENT 0x100
#define PUMP_EVERY_STEPS 64
#define WAIT_SLICE_MS 100


static FILE *logFile;
static double speed = 1.0;
static struct timespec clockStart;
static pid_t mainThread;

static ShxShared *shared;

// lockstep is set once the hook owns the loop, after which time only moves when update() runs
static volatile int lockstep;
static uint64_t lockBaseCounter;
static volatile uint64_t lockTicks;

static pthread_mutex_t randLock = PTHREAD_MUTEX_INITIALIZER;
static struct random_data randData;
static char randState[128];

static void logLine(const char *fmt, ...)
{
	if (!logFile)
		return;
	va_list args;
	va_start(args, fmt);
	vfprintf(logFile, fmt, args);
	va_end(args);
	fputc('\n', logFile);
	fflush(logFile);
}

// logs the first call of each hook, so the log shows which ones the game reached
#define LOG_ONCE(name) do { \
	static bool seen; \
	if (!seen) { seen = true; logLine("first call: %s", name); } \
} while (0)

static void fail(const char *msg)
{
	fprintf(stderr, "shxhook: %s\n", msg);
	logLine("fatal: %s", msg);
	_exit(1);
}

static int makeDirs(const char *path)
{
	char buf[4096];
	if (snprintf(buf, sizeof(buf), "%s", path) >= (int)sizeof(buf))
		return -1;
	for (char *p = buf + 1; *p; p++) {
		if (*p != '/')
			continue;
		*p = '\0';
		if (mkdir(buf, 0755) != 0 && errno != EEXIST)
			return -1;
		*p = '/';
	}
	if (mkdir(buf, 0755) != 0 && errno != EEXIST)
		return -1;
	return 0;
}

static void openShared(const char *name)
{
	int fd = shm_open(name, O_RDWR, 0);
	if (fd < 0)
		fail("couldn't open SHX_SHM");
	void *mem = mmap(NULL, sizeof(ShxShared), PROT_READ | PROT_WRITE, MAP_SHARED, fd, 0);
	close(fd);
	if (mem == MAP_FAILED)
		fail("couldn't map SHX_SHM");
	ShxShared *sh = mem;
	if (sh->magic != SHX_MAGIC || sh->version != SHX_PROTO_VERSION)
		fail("SHX_SHM has the wrong magic or version");
	if (sh->structSize != sizeof(ShxShared))
		fail("SHX_SHM layout size doesn't match the hook");
	shared = sh;
}

__attribute__((constructor))
static void shxInit(void)
{
	mainThread = gettid();

	const char *logPath = getenv("SHX_LOG");
	if (logPath)
		logFile = fopen(logPath, "w");

	// refuse to run without an instance folder, so a stray launch can't touch the real save
	const char *dataDir = getenv("SHX_DATA_DIR");
	if (!dataDir || dataDir[0] != '/')
		fail("SHX_DATA_DIR must be set to an absolute path");
	if (makeDirs(dataDir) != 0)
		fail("couldn't create SHX_DATA_DIR");
	setenv("XDG_DATA_HOME", dataDir, 1);

	const char *speedText = getenv("SHX_SPEED");
	if (speedText) {
		speed = strtod(speedText, NULL);
		if (!(speed > 0.0 && speed <= 1000.0))
			fail("SHX_SPEED must be between 0 and 1000");
	}

	unsigned int seed = 1;
	const char *seedText = getenv("SHX_SEED");
	if (seedText)
		seed = (unsigned int)strtoul(seedText, NULL, 10);
	if (initstate_r(seed, randState, sizeof(randState), &randData) != 0)
		fail("initstate_r failed");

	const char *shmName = getenv("SHX_SHM");
	if (shmName)
		openShared(shmName);

	clock_gettime(CLOCK_MONOTONIC, &clockStart);
	logLine("shxhook loaded: data=%s speed=%g seed=%u shm=%s pid=%d",
		dataDir, speed, seed, shmName ? shmName : "(none)", getpid());
}

// Virtual time

static uint64_t freeRunningCounter(void)
{
	struct timespec now;
	clock_gettime(CLOCK_MONOTONIC, &now);
	int64_t realNs = (int64_t)(now.tv_sec - clockStart.tv_sec) * 1000000000LL
		+ (now.tv_nsec - clockStart.tv_nsec);
	return (uint64_t)((double)realNs * speed * ((double)VIRTUAL_FREQ / 1e9));
}

static uint64_t virtualCounter(void)
{
	if (lockstep)
		return lockBaseCounter + lockTicks * COUNTS_PER_TICK;
	return freeRunningCounter();
}

EXPORT uint64_t SDL_GetPerformanceFrequency(void)
{
	LOG_ONCE("SDL_GetPerformanceFrequency");
	return VIRTUAL_FREQ;
}

EXPORT uint64_t SDL_GetPerformanceCounter(void)
{
	LOG_ONCE("SDL_GetPerformanceCounter");
	return virtualCounter();
}

EXPORT uint32_t SDL_GetTicks(void)
{
	LOG_ONCE("SDL_GetTicks");
	return (uint32_t)(virtualCounter() / (VIRTUAL_FREQ / 1000));
}

EXPORT uint64_t SDL_GetTicks64(void)
{
	LOG_ONCE("SDL_GetTicks64");
	return virtualCounter() / (VIRTUAL_FREQ / 1000);
}

EXPORT void SDL_Delay(uint32_t ms)
{
	LOG_ONCE("SDL_Delay");
	// other threads (audio) still need real sleeps or they spin a core
	bool mainInLockstep = lockstep && gettid() == mainThread;
	if (mainInLockstep)
		return;
	double realNs = (double)ms * 1e6 / (lockstep ? 1.0 : speed);
	struct timespec req = {
		.tv_sec = (time_t)(realNs / 1e9),
		.tv_nsec = (long)((int64_t)realNs % 1000000000LL),
	};
	while (nanosleep(&req, &req) != 0 && errno == EINTR)
		;
}

// Random numbers

EXPORT int rand(void)
{
	LOG_ONCE("rand");
	// another library drawing from the same stream would make runs depend on more than the seed
	uintptr_t caller = (uintptr_t)__builtin_return_address(0);
	if (caller < EXE_TEXT_START || caller >= EXE_TEXT_END) {
		static int foreignCalls;
		if (foreignCalls++ < 5)
			logLine("rand called from outside the game: %p", (void *)caller);
	}
	int32_t result;
	pthread_mutex_lock(&randLock);
	random_r(&randData, &result);
	pthread_mutex_unlock(&randLock);
	return result;
}

static void reseed(uint32_t seed)
{
	pthread_mutex_lock(&randLock);
	srandom_r(seed, &randData);
	pthread_mutex_unlock(&randLock);
}

EXPORT void srand(unsigned int seed)
{
	LOG_ONCE("srand");
	reseed(seed);
}

// Loop takeover

typedef int (*PollEventFn)(void *event);
typedef void (*AppFn)(void *app);
typedef void (*DisplayFn)(void *window, void *app);
typedef int (*ElapsedMillisFn)(void);

static PollEventFn realPollEvent;
static bool takeoverStarted;
static bool quitRequested;

static void pumpEvents(void)
{
	// SDL_Event is a 56 byte union, the type is the first 32 bits
	_Alignas(8) uint8_t event[64];
	while (realPollEvent(event) == 1) {
		if (*(uint32_t *)event == SDL_QUIT_EVENT)
			quitRequested = true;
	}
}

static bool clientAlive(void)
{
	pid_t client = (pid_t)shared->clientPid;
	return client > 0 && (kill(client, 0) == 0 || errno == EPERM);
}

static bool waitForCommand(void)
{
	for (;;) {
		struct timespec deadline;
		clock_gettime(CLOCK_REALTIME, &deadline);
		deadline.tv_nsec += WAIT_SLICE_MS * 1000000L;
		if (deadline.tv_nsec >= 1000000000L) {
			deadline.tv_sec++;
			deadline.tv_nsec -= 1000000000L;
		}
		if (sem_timedwait(&shared->commandReady, &deadline) == 0)
			return true;
		if (errno != ETIMEDOUT && errno != EINTR)
			return false;
		pumpEvents();
		if (quitRequested) {
			logLine("window closed, leaving lockstep");
			return false;
		}
		if (!clientAlive()) {
			logLine("client %u is gone, leaving lockstep", shared->clientPid);
			return false;
		}
	}
}

static uint8_t *gameApp;
static uint8_t *gameWindow;
static double frameTime;

// one 1/60 s tick, the same work the game's own loop does for each elapsed period
static void hookTick(ShxCore *core)
{
	AppFn update = (AppFn)ADDR_SUPERHEX_UPDATE;
	ElapsedMillisFn elapsedMillis = (ElapsedMillisFn)ADDR_OF_ELAPSED_MILLIS;
	lockTicks++;
	FIELD(double, gameWindow, 0xc8) = frameTime;
	update(core->app);
	// draw() normally stores this, and gamelogic reads it, so keep it moving without drawing
	FIELD(uint64_t, core->app, OFF_ELAPSED_MILLIS) = (uint32_t)elapsedMillis();
}

static void hookReseed(ShxCore *core, uint32_t seed)
{
	(void)core;
	reseed(seed);
}

static ShxCore core = { .tick = hookTick, .reseed = hookReseed, .log = logLine };

static bool checkAddresses(uint8_t *app, void *window)
{
	if (!app || !window) {
		logLine("app or window pointer is null");
		return false;
	}
	void **vtable = *(void ***)app;
	if ((uintptr_t)vtable != ADDR_SUPERHEX_VPTR
		|| (uintptr_t)vtable[3] != ADDR_SUPERHEX_UPDATE
		|| (uintptr_t)vtable[4] != ADDR_SUPERHEX_DRAW) {
		logLine("superhex vtable doesn't match this build: %p", (void *)vtable);
		return false;
	}
	return true;
}

// writes the whole superhex object next to the shared memory block, for checking the simulator
static int dumpState(void)
{
	const char *shmName = getenv("SHX_SHM");
	char path[512];
	if (!shmName || snprintf(path, sizeof(path), "/dev/shm%s.dump", shmName) >= (int)sizeof(path))
		return SHX_ERR_DUMP_FAILED;
	FILE *f = fopen(path, "wb");
	if (!f)
		return SHX_ERR_DUMP_FAILED;
	size_t written = fwrite(gameApp, 1, SHX_SUPERHEX_SIZE, f);
	fclose(f);
	return written == SHX_SUPERHEX_SIZE ? SHX_ERR_NONE : SHX_ERR_DUMP_FAILED;
}

static void runLockstep(void)
{
	gameApp = *(uint8_t **)ADDR_SUPERHEXAGON_APP;
	gameWindow = *(uint8_t **)ADDR_WINDOW;
	if (!checkAddresses(gameApp, gameWindow)) {
		shared->error = SHX_ERR_BAD_BUILD;
		sem_post(&shared->resultReady);
		return;
	}

	DisplayFn display = (DisplayFn)ADDR_WINDOW_DISPLAY;
	frameTime = (double)COUNTS_PER_TICK / (double)VIRTUAL_FREQ;

	// a whole millisecond, so the simulator can reproduce SDL_GetTicks from the tick count alone
	uint64_t millisecond = VIRTUAL_FREQ / 1000;
	lockBaseCounter = (freeRunningCounter() / millisecond + 1) * millisecond;
	lockTicks = 0;
	lockstep = 1;
	logLine("lockstep started");

	core.app = gameApp;
	shxWriteResult(&core, shared, lockTicks);
	shared->hookReady = 1;
	sem_post(&shared->resultReady);

	uint64_t steps = 0;
	while (waitForCommand()) {
		int command = shared->command;
		if (command == SHX_CMD_QUIT) {
			logLine("quit command after %llu steps", (unsigned long long)steps);
			break;
		}

		int error = SHX_ERR_NONE;
		int ticksRun = 0;
		int resetTicks = 0;
		if (shxHandleCommand(&core, shared, &error, &ticksRun, &resetTicks)) {
			bool isReset = command == SHX_CMD_RESET || command == SHX_CMD_REPLAY;
			if (isReset && (error != SHX_ERR_NONE || getenv("SHX_LOG_RESETS")))
				logLine("reset to level %d seed %u: error %d after %d ticks",
					shared->level, shared->seed, error, resetTicks);
		} else if (command == SHX_CMD_DUMP) {
			error = dumpState();
		} else {
			error = SHX_ERR_BAD_COMMAND;
		}

		steps++;
		if (shared->render)
			display(gameWindow, gameApp);
		if (shared->render || steps % PUMP_EVERY_STEPS == 0)
			pumpEvents();

		shared->error = error;
		shared->ticksRun = ticksRun;
		shared->resetTicks = resetTicks;
		shxWriteResult(&core, shared, lockTicks);
		sem_post(&shared->resultReady);

		if (quitRequested) {
			logLine("window closed, leaving lockstep");
			break;
		}
	}

	// clearing the window's running flag lets the game's own loop shut down normally
	FIELD(uint8_t, gameWindow, 0) = 0;
}

EXPORT int SDL_PollEvent(void *event)
{
	if (!realPollEvent) {
		realPollEvent = (PollEventFn)dlsym(RTLD_NEXT, "SDL_PollEvent");
		if (!realPollEvent)
			fail("couldn't find the real SDL_PollEvent");
	}
	if (!shared || takeoverStarted || gettid() != mainThread)
		return realPollEvent(event);

	takeoverStarted = true;
	runLockstep();
	return 0;
}

// Steam fakes
//
// The game calls interface methods through vtables, so each fake object is a pointer to a table
// of functions. Every slot returns 0 unless it needs to say something else.

#define FAKE_SLOTS 128

typedef struct {
	void **vtable;
} FakeInterface;

static long fakeZero(void)
{
	return 0;
}

static const char *fakePersonaName(void *self)
{
	(void)self;
	return "SHXBOT";
}

static void *zeroTable[FAKE_SLOTS];
static void *friendsTable[FAKE_SLOTS];

static FakeInterface fakeGeneric = { zeroTable };
static FakeInterface fakeFriends = { friendsTable };

__attribute__((constructor))
static void fillTables(void)
{
	for (int i = 0; i < FAKE_SLOTS; i++) {
		zeroTable[i] = (void *)fakeZero;
		friendsTable[i] = (void *)fakeZero;
	}
	// ISteamFriends slot 0 is GetPersonaName, which processleaderboards passes to strlen
	friendsTable[0] = (void *)fakePersonaName;
}

static int initCalls;

// main needs true to start, and updatescoreapi retries this every tick and only turns on score
// uploads and achievements when it gets true, so every call after the first says false
EXPORT bool SteamAPI_Init(void)
{
	initCalls++;
	if (initCalls == 1)
		logLine("SteamAPI_Init: first call, returning true");
	else if (initCalls == 2)
		logLine("SteamAPI_Init: later calls return false (score uploads stay off)");
	return initCalls == 1;
}

EXPORT bool SteamAPI_RestartAppIfNecessary(uint32_t appId)
{
	logLine("SteamAPI_RestartAppIfNecessary(%u): returning false", appId);
	return false;
}

EXPORT void SteamAPI_RunCallbacks(void)
{
	LOG_ONCE("SteamAPI_RunCallbacks");
}

EXPORT void SteamAPI_RegisterCallResult(void *callback, uint64_t call)
{
	(void)callback;
	(void)call;
	LOG_ONCE("SteamAPI_RegisterCallResult");
}

EXPORT void SteamAPI_UnregisterCallResult(void *callback, uint64_t call)
{
	(void)callback;
	(void)call;
	LOG_ONCE("SteamAPI_UnregisterCallResult");
}

// slots 0x90 and 0x98 are the two cloud checks, and zero sends saves to SDL_GetPrefPath
EXPORT void *SteamRemoteStorage(void)
{
	LOG_ONCE("SteamRemoteStorage");
	return &fakeGeneric;
}

EXPORT void *SteamFriends(void)
{
	LOG_ONCE("SteamFriends");
	return &fakeFriends;
}

EXPORT void *SteamUser(void)
{
	LOG_ONCE("SteamUser");
	return &fakeGeneric;
}

EXPORT void *SteamUserStats(void)
{
	LOG_ONCE("SteamUserStats");
	return &fakeGeneric;
}
