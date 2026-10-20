#ifndef SHX_PRELUDE_H
#define SHX_PRELUDE_H

#include <math.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>

// Ghidra's types. Classes are byte typed so `self + offset` addresses bytes, the way the
// decompiled code expects.
typedef unsigned char superhex;
typedef unsigned char gameclass;
typedef unsigned char musicclass;
typedef unsigned char graphicsclass;
typedef unsigned char scoreclass;
typedef unsigned char helpclass;
typedef unsigned char deviceclass;
typedef unsigned char ofSoundPlayer;
typedef unsigned char ofLog;
typedef unsigned char ofLogNotice;
typedef unsigned char ofxXmlSettings;
typedef unsigned char string;
typedef unsigned char allocator;
typedef unsigned char undefined;
typedef uint8_t undefined1;
typedef uint16_t undefined2;
typedef uint32_t undefined3;
typedef uint32_t undefined4;
typedef uint64_t undefined8;
typedef uint8_t byte;
typedef unsigned int uint;
typedef unsigned long ulong;
typedef unsigned short ushort;
typedef long long longlong;
typedef unsigned long long ulonglong;
typedef long code();

// Ghidra's markers around atomic reference counts, which don't matter in one thread
#define LOCK()
#define UNLOCK()

#define ABS(x) ((x) < 0 ? -(x) : (x))
#define CONCAT31(hi, lo) ((uint32_t)(((uint32_t)(hi) << 8) | (uint8_t)(lo)))
#define CONCAT44(hi, lo) ((uint64_t)(((uint64_t)(uint32_t)(hi) << 32) | (uint32_t)(lo)))

// hand written in runtime.c with the real signatures
float oneRound(float value);
float ofRandom(float range);
float getExpectedFrameDelta(void);
double getLastFrameTimeMillis(void);
int superRemainder(double value, int divisor);
int ofGetElapsedTimeMillis(void);
void *operator_new(unsigned long size);

// string and vector helpers that leave their results as valid empty values, since gameplay
// never reads text but the decompiled code checks string reference counts
long std__string__string();
long std__string__assign();
long std__string__append();
long std__string__insert();
long std__string__push_back();
long std__string__substr();
long ofToString();
long ofSplitString();

// fills the beat table from the game's song data, see runtime.c
void musicclass__loadsongdata(musicclass *self, int track);

// the palette state gamelogic reads, without the colour math, see runtime.c
void graphicsclass__setpal(graphicsclass *self, int palette, bool redraw);
void graphicsclass__changepal(graphicsclass *self, int palette);
void graphicsclass__updatepal(graphicsclass *self, int fade);
void graphicsclass__attach(graphicsclass *self);
void graphicsclass__detach(graphicsclass *self);
void graphicsclass__fulldetach(graphicsclass *self);

// globals the decompiled code reads
// thread local, since each simulator thread steps its own game
extern __thread float *GameTimer__instance;
extern char std__string___Rep___S_empty_rep_storage[64];
// 0x7f56f8 in the game is _S_empty_rep_storage + 0x18, the data every empty std::string points at
#define DAT_007f56f8 (std__string___Rep___S_empty_rep_storage[0x18])
#define SHX_EMPTY_STRING_DATA ((uint64_t)(uintptr_t)&std__string___Rep___S_empty_rep_storage[0x18])
// switch tables musicclass::play reads with byte offsets (0x596340 and 0x596320)
extern const unsigned char CSWTCH_168[12];
extern const unsigned char CSWTCH_169[32];

#endif
