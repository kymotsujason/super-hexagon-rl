CC = gcc
# The decompiled code type puns memory and relies on wrapping integer math, and the original
# binary never fuses multiplies into adds, so results only match with these flags
CFLAGS = -std=gnu17 -O2 -g -fPIC -fvisibility=hidden -fno-strict-aliasing -fwrapv -ffp-contract=off \
	-fno-fast-math -fopenmp -I. -Igen
LOGIC_CFLAGS = $(CFLAGS) -w -fpermissive
LDFLAGS = -shared -fopenmp -lm

all: shxsim.so

gen/logic.c gen/logic.h gen/stubs.c: tools/ghidra2c.py $(wildcard ../decomp/*.c)
	python3 -I tools/ghidra2c.py

gen/logic.o: gen/logic.c gen/logic.h prelude.h
	$(CC) $(LOGIC_CFLAGS) -c -o $@ gen/logic.c

gen/stubs.o: gen/stubs.c gen/logic.h prelude.h
	$(CC) $(LOGIC_CFLAGS) -c -o $@ gen/stubs.c

runtime.o: runtime.c shxsim.h gen/logic.h prelude.h ../hook/shxcore.h ../hook/shxproto.h
	$(CC) $(CFLAGS) -Wall -Wextra -Wno-unused-function -c -o $@ runtime.c

shxsim.o: shxsim.c shxsim.h gen/logic.h ../hook/shxcore.h ../hook/shxproto.h
	$(CC) $(CFLAGS) -Wall -Wextra -Wno-unused-function -c -o $@ shxsim.c

gen/songdata.c: tools/extract_songdata.py
	python3 -I tools/extract_songdata.py

gen/songdata.o: gen/songdata.c
	$(CC) $(CFLAGS) -c -o $@ gen/songdata.c

shxsim.so: gen/logic.o gen/stubs.o gen/songdata.o runtime.o shxsim.o
	$(CC) -o $@ $^ $(LDFLAGS)

clean:
	rm -f shxsim.so gen/logic.o gen/stubs.o gen/songdata.o runtime.o shxsim.o

.PHONY: all clean
