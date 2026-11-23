#include "RetroEngine.hpp"

#include <cstdio>
#include <cstdlib>
#include <cstring>

#ifndef WEBPATCH_PATH
#define WEBPATCH_PATH "/webport.patch"
#endif

#define PATCH_CODE_BASE 0x30000
#define PATCH_JUMP_BASE 0x3E00
#define PATCH_FUNC_BASE 400

static unsigned int PatchHash(const int *words, int count)
{
    unsigned int h = 2166136261u;
    for (int i = 0; i < count; ++i) {
        unsigned int w = (unsigned int)words[i];
        for (int b = 0; b < 4; ++b) {
            h ^= (w >> (8 * b)) & 0xFF;
            h *= 16777619u;
        }
    }
    return h;
}

static bool NextToken(char **save, long *out, int base)
{
    char *t = strtok_r(NULL, " \t\r\n", save);
    if (!t)
        return false;
    *out = strtol(t, NULL, base);
    return true;
}

static bool ReadWords(char **save, int *dest, int count)
{
    for (int i = 0; i < count; ++i) {
        long v;
        if (!NextToken(save, &v, 10))
            return false;
        dest[i] = (int)v;
    }
    return true;
}

void ApplyScriptPatches(const char *scriptPath, int codeStart, int codeLength)
{
    FILE *f = fopen(WEBPATCH_PATH, "rb");
    if (!f)
        return;

    const char *name = strrchr(scriptPath, '/');
    name             = name ? name + 1 : scriptPath;

    static char line[32768];
    static int words[4096];
    int applied = 0, skipped = 0;

    while (fgets(line, sizeof(line), f)) {
        char *save = NULL;
        char *op   = strtok_r(line, " \t\r\n", &save);
        if (!op)
            continue;

        if (strcmp(op, "set") == 0) {
            char *file  = strtok_r(NULL, " \t\r\n", &save);
            char *posS  = strtok_r(NULL, " \t\r\n", &save);
            char *hashS = strtok_r(NULL, " \t\r\n", &save);
            char *cntS  = strtok_r(NULL, " \t\r\n", &save);
            if (!file || !posS || !hashS || !cntS || strcmp(file, name) != 0)
                continue;

            int pos           = (int)strtol(posS, NULL, 10);
            unsigned int hash = (unsigned int)strtoul(hashS, NULL, 16);
            int count         = (int)strtol(cntS, NULL, 10);
            bool valid        = count > 0 && count <= 256 && ReadWords(&save, words, count);

            if (!valid || pos < 0 || pos + count > codeLength || codeStart + pos + count > SCRIPTCODE_COUNT) {
                printf("webport patch: skipped a change in %s at %d (does not fit)\n", name, pos);
                ++skipped;
                continue;
            }
            if (PatchHash(&scriptCode[codeStart + pos], count) != hash) {
                printf("webport patch: skipped a change in %s at %d (your file is different from the one it was made for)\n", name, pos);
                ++skipped;
                continue;
            }
            memcpy(&scriptCode[codeStart + pos], words, count * sizeof(int));
            ++applied;
        }
        else if (strcmp(op, "code") == 0) {
            long abs, count;
            if (!NextToken(&save, &abs, 10) || !NextToken(&save, &count, 10) || count <= 0 || count > 4096 || abs < PATCH_CODE_BASE
                || abs + count > SCRIPTCODE_COUNT || !ReadWords(&save, words, (int)count)) {
                printf("webport patch: skipped a block of new code (does not fit)\n");
                ++skipped;
                continue;
            }
            memcpy(&scriptCode[abs], words, count * sizeof(int));
        }
        else if (strcmp(op, "jumps") == 0) {
            long abs, count;
            if (!NextToken(&save, &abs, 10) || !NextToken(&save, &count, 10) || count <= 0 || count > 4096 || abs < PATCH_JUMP_BASE
                || abs + count > JUMPTABLE_COUNT || !ReadWords(&save, words, (int)count)) {
                printf("webport patch: skipped a block of new jumps (does not fit)\n");
                ++skipped;
                continue;
            }
            memcpy(&jumpTable[abs], words, count * sizeof(int));
        }
        else if (strcmp(op, "hook") == 0) {
            long fn, wcode, wjump, alias, count;
            char *hashS = NULL;
            bool ok = NextToken(&save, &fn, 10) && NextToken(&save, &wcode, 10) && NextToken(&save, &wjump, 10) && NextToken(&save, &alias, 10)
                      && NextToken(&save, &count, 10);
            if (ok)
                hashS = strtok_r(NULL, " \t\r\n", &save);
            if (!ok || !hashS || fn < 0 || fn >= PATCH_FUNC_BASE || alias < PATCH_FUNC_BASE || alias >= FUNCTION_COUNT || wcode < PATCH_CODE_BASE
                || wcode >= SCRIPTCODE_COUNT || wjump < PATCH_JUMP_BASE || wjump >= JUMPTABLE_COUNT || count <= 0 || count > 64) {
                printf("webport patch: skipped a hook (invalid)\n");
                ++skipped;
                continue;
            }
            unsigned int hash = (unsigned int)strtoul(hashS, NULL, 16);
            int current       = scriptFunctionList[fn].ptr.scriptCodePtr;
            if (current == (int)wcode)
                continue;
            if (current < 0 || current + count > SCRIPTCODE_COUNT || PatchHash(&scriptCode[current], (int)count) != hash) {
                printf("webport patch: skipped a hook on function %ld (your file is different from the one it was made for)\n", fn);
                ++skipped;
                continue;
            }
            scriptFunctionList[alias].ptr                = scriptFunctionList[fn].ptr;
            scriptFunctionList[fn].ptr.scriptCodePtr     = (int)wcode;
            scriptFunctionList[fn].ptr.jumpTablePtr      = (int)wjump;
            ++applied;
        }
    }
    fclose(f);
    if (applied || skipped)
        printf("webport patch: %s: %d applied, %d skipped\n", name, applied, skipped);
}
