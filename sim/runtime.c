#define _GNU_SOURCE
#include <stdlib.h>
#include <string.h>

#include "gen/logic.h"
#include "shxsim.h"

__thread ShxSim *simCurrent;
__thread float *GameTimer__instance;
char std__string___Rep___S_empty_rep_storage[64];

// little endian copies of the music start positions at 0x596340 and 0x596320
const unsigned char CSWTCH_168[12] = {
	0x55, 0x6b, 0x00, 0x00, 0x1a, 0x3a, 0x01, 0x00, 0xb0, 0xad, 0x01, 0x00,
};
const unsigned char CSWTCH_169[32] = {
	0x48, 0x71, 0x00, 0x00, 0xe0, 0xab, 0x00, 0x00, 0xe0, 0xab, 0x00, 0x00, 0x9c, 0xe0, 0x00, 0x00,
	0x48, 0x71, 0x00, 0x00, 0xe0, 0xab, 0x00, 0x00, 0xe0, 0xab, 0x00, 0x00, 0x9c, 0xe0, 0x00, 0x00,
};

// The helpers below follow the machine code at the addresses noted, since Ghidra's types for
// them are wrong in ways that change results.

// 0x47c2f0: float multiply, then a double add, truncate, and a float divide
float oneRound(float value)
{
	float scaled = value * 10.0f;
	double rounded = (double)scaled + 0.5;
	int whole = (int)rounded;
	return (float)whole / 10.0f;
}

static int simRand(void)
{
	int32_t result;
	random_r(&simCurrent->randData, &result);
	return result;
}

// 0x486720: two float multiplies, the second by 2^-31
float ofRandom(float range)
{
	float value = (float)simRand();
	value = value * range;
	return value * 0x1p-31f;
}

// 0x47c140 returns expectedFrameDelta, which setGameFrameRate leaves at 1.0f for 60 fps
float getExpectedFrameDelta(void)
{
	return 1.0f;
}

// 0x47c130 returns lastFrameTimeMS, which superhex::update stores before calculateTime
double getLastFrameTimeMillis(void)
{
	return simCurrent->lastFrameTime;
}

int superRemainder(double value, int divisor)
{
	return (int)floor(fmod(value, (double)divisor));
}

// The hook starts its lockstep clock on a whole millisecond, so after T ticks the game reads
// base + floor(T * 1000 / 60) ms. millisBase is the value at the dump, taken at hook tick
// tickOffset, so this reproduces the hook's numbers exactly.
int ofGetElapsedTimeMillis(void)
{
	uint64_t now = simCurrent->tickOffset + simCurrent->ticks;
	return (int)(simCurrent->millisBase + now * 1000 / 60 - simCurrent->tickOffset * 1000 / 60);
}

static void makeEmptyString(void *target)
{
	if (target)
		*(uint64_t *)target = SHX_EMPTY_STRING_DATA;
}

long std__string__string(void *target)
{
	makeEmptyString(target);
	return (long)target;
}

long std__string__assign(void *target)
{
	makeEmptyString(target);
	return (long)target;
}

long std__string__append(void *target)
{
	makeEmptyString(target);
	return (long)target;
}

long std__string__insert(void *target)
{
	makeEmptyString(target);
	return (long)target;
}

long std__string__push_back(void *target)
{
	makeEmptyString(target);
	return (long)target;
}

long std__string__substr(void *target)
{
	makeEmptyString(target);
	return (long)target;
}

long ofToString(void *target)
{
	makeEmptyString(target);
	return (long)target;
}

// a std::vector is three pointers, and all three null is an empty vector
long ofSplitString(void *target)
{
	if (target)
		memset(target, 0, 3 * sizeof(void *));
	return (long)target;
}

// musicclass::loadsongdata (0x476c10) splits one of six comma separated strings from the binary
// (copied into gen/songdata.c) and reads a fixed count of entries with ofToInt
extern const char *const shxSongData[7];
static const int songCounts[7] = { 0, 0x2cb1, 0x227e, 0x2606, 0x2606, 0x2674, 0x2bcc };
static int *songValues[7];

// istringstream >> int, which ofToInt uses: skip spaces, read a signed number, 0 if there's none
static int parseField(const char *text, const char **next)
{
	const char *p = text;
	while (*p == ' ' || *p == '\t' || *p == '\n' || *p == '\r')
		p++;
	char *end;
	long value = strtol(p, &end, 10);
	const char *comma = strchr(end, ',');
	*next = comma ? comma + 1 : end + strlen(end);
	return end == p ? 0 : (int)value;
}

__attribute__((constructor))
static void parseSongs(void)
{
	for (int track = 1; track <= 6; track++) {
		songValues[track] = calloc((size_t)songCounts[track], sizeof(int));
		const char *p = shxSongData[track];
		for (int i = 0; i < songCounts[track] && *p; i++)
			songValues[track][i] = parseField(p, &p);
	}
}

void musicclass__loadsongdata(musicclass *self, int track)
{
	if (*(int *)(self + 0x1d4dc) == track || track == 0)
		return;
	*(int *)(self + 0x1d4dc) = track;
	int index = track >= 1 && track <= 5 ? track : 6;
	int count = songCounts[index];
	const int *values = songValues[index];
	int *beats = (int *)(self + 0x18);
	for (int i = 0; i < count; i++) {
		if (track == 2)
			beats[i] = (int)((double)values[i] * 0.6);
		else if (track == 5) {
			// track 5 reads backwards and leaves its first two entries alone
			if (i > 1)
				beats[i] = values[count - i];
		} else
			beats[i] = values[i];
	}
}

// graphicsclass::setpal (0x464ef0) fills colour tables and stores the palette at +0x274c, and
// gamelogic only reads the palette number
void graphicsclass__setpal(graphicsclass *self, int palette, bool redraw)
{
	(void)redraw;
	*(int *)(self + 0x274c) = palette;
}

// 0x466ae0
void graphicsclass__changepal(graphicsclass *self, int palette)
{
	*(int *)(self + 0x2750) = palette;
	*(int *)(self + 0x2748) = 1;
}

// 0x4644f0 only blends colours
void graphicsclass__updatepal(graphicsclass *self, int fade)
{
	(void)self;
	(void)fade;
}

// 0x46a8f0
void graphicsclass__attach(graphicsclass *self)
{
	self[0x2760] = 0;
	self[0x25dc] = 1;
}

// 0x46a920
void graphicsclass__detach(graphicsclass *self)
{
	self[0x25dc] = 0;
}

// 0x46a930
void graphicsclass__fulldetach(graphicsclass *self)
{
	self[0x2760] = 1;
}

// only calculateTime and processmusicfade call this, for the frame timer that the simulator
// already provides, so it never runs
void *operator_new(unsigned long size)
{
	return calloc(1, size);
}
