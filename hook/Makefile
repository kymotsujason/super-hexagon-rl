CC = gcc
CFLAGS = -O2 -g -fPIC -fvisibility=hidden -Wall -Wextra -Werror
LDFLAGS = -shared -pthread

all: shxhook.so shxbatch.so

shxhook.so: shxhook.c shxcore.h shxproto.h
	$(CC) $(CFLAGS) $(LDFLAGS) -o $@ $<

shxbatch.so: shxbatch.c shxproto.h
	$(CC) $(CFLAGS) $(LDFLAGS) -o $@ $<

clean:
	rm -f shxhook.so shxbatch.so

.PHONY: all clean
