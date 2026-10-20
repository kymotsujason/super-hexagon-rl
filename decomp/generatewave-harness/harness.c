#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>

typedef unsigned char gameclass;
void generatewave(gameclass *self, int param_1);

#define MAXCALLS 64
static int choice[MAXCALLS];
static int range[MAXCALLS];
static int ncall;
static int nfixed;
static int overflow;

float ofRandom(float x)
{
    int r = (int)x;
    if (ncall >= MAXCALLS) {
        overflow = 1;
        return 0.5f;
    }
    if (ncall >= nfixed) {
        choice[ncall] = 0;
    }
    range[ncall] = r;
    return (float)choice[ncall++] + 0.5f;
}

typedef struct { int slot, dist, width, type, viaCall; } wall;
static wall walls[600];
static int nwalls;

void addenemy(gameclass *self, int slot, int dist, int width, int type)
{
    int n = *(int *)(self + 0x2878);
    int idx = n;
    for (int i = 0; i < n; i++) {
        if (self[i * 0x14 + 0x178] == 0) { idx = i; break; }
    }
    if (idx == n) *(int *)(self + 0x2878) = n + 1;
    *(int *)(self + idx * 0x14 + 0x168) = slot;
    *(int *)(self + idx * 0x14 + 0x16c) = dist + 4000;
    *(int *)(self + idx * 0x14 + 0x170) = width;
    *(int *)(self + idx * 0x14 + 0x174) = type;
    self[idx * 0x14 + 0x178] = 1;
}

static gameclass *g;
#define GSIZE 0x60000

static void runOnce(int id, float speed)
{
    memset(g, 0, GSIZE);
    *(float *)(g + 0x28c0) = speed;
    *(float *)(g + 0x28b4) = -12345.0f;
    *(int *)(g + 0x28c4) = 0x7777;
    *(float *)(g + 0x28c8) = -1.0f;
    *(int *)(g + 0x2930) = -99;
    ncall = 0;
    overflow = 0;
    generatewave(g, id);
    nwalls = *(int *)(g + 0x2878);
    for (int i = 0; i < nwalls; i++) {
        walls[i].slot = *(int *)(g + i * 0x14 + 0x168);
        walls[i].dist = *(int *)(g + i * 0x14 + 0x16c);
        walls[i].width = *(int *)(g + i * 0x14 + 0x170);
        walls[i].type = *(int *)(g + i * 0x14 + 0x174);
    }
}

static void printRun(int id)
{
    printf("id=%d rand=[", id);
    for (int i = 0; i < ncall; i++) printf("%s%d/%d", i ? "," : "", choice[i], range[i]);
    printf("] delay=%g c4=%d c8=%g e4=%d 5464=%d b2930=%d%s walls=%d:",
        *(float *)(g + 0x28b4), *(int *)(g + 0x28c4), *(float *)(g + 0x28c8),
        *(int *)(g + 0x53e4), *(int *)(g + 0x5464), *(int *)(g + 0x2930),
        overflow ? " OVERFLOW" : "", nwalls);
    for (int i = 0; i < nwalls; i++)
        printf(" (%d,%d,%d,%d)", walls[i].slot, walls[i].dist, walls[i].width, walls[i].type);
    printf("\n");
}

int main(int argc, char **argv)
{
    g = calloc(1, GSIZE);
    if (argc >= 2 && strcmp(argv[1], "scan") == 0) {
        for (int id = 0; id <= 2000; id++) {
            nfixed = 0;
            runOnce(id, 20.0f);
            int changed = nwalls > 0 || *(float *)(g + 0x28b4) != -12345.0f * 1.0f ||
                *(int *)(g + 0x53e4) || *(int *)(g + 0x5464) || *(int *)(g + 0x2930) != -99;
            float d = *(float *)(g + 0x28b4);
            if (changed) printf("%d walls=%d delay=%g ncall=%d\n", id, nwalls, d, ncall);
        }
        return 0;
    }
    if (argc < 2) return 1;
    int id = atoi(argv[1]);
    float speed = argc >= 3 ? atof(argv[2]) : 20.0f;
    long cap = argc >= 4 ? atol(argv[3]) : 100000;
    long leaves = 0;
    nfixed = 0;
    for (;;) {
        runOnce(id, speed);
        printRun(id);
        leaves++;
        if (leaves >= cap) { printf("CAP\n"); break; }
        int k = ncall - 1;
        while (k >= 0 && choice[k] + 1 >= range[k]) k--;
        if (k < 0) break;
        choice[k]++;
        nfixed = k + 1;
    }
    fprintf(stderr, "id=%d leaves=%ld\n", id, leaves);
    return 0;
}
