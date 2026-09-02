// BrainAI launcher: loads the bundled CPython (libpython) in-process and runs Resources/brainai.py.
// Keeps Contents/MacOS/BrainAI as the real main executable (no exec → LaunchServices/NSBundle stay consistent).
#include <dlfcn.h>
#include <limits.h>
#include <mach-o/dyld.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <wchar.h>

typedef wchar_t *(*decode_fn)(const char *, size_t *);
typedef int (*pymain_fn)(int, wchar_t **);

static void die(const char *m) { fprintf(stderr, "BrainAI launcher: %s\n", m); exit(1); }

int main(int argc, char **argv) {
    char exe[PATH_MAX], real[PATH_MAX], tmp[PATH_MAX], res[PATH_MAX], home[PATH_MAX], lib[PATH_MAX], script[PATH_MAX];
    uint32_t n = sizeof exe;
    if (_NSGetExecutablePath(exe, &n) != 0 || !realpath(exe, real)) die("cannot resolve executable path");
    char *slash = strrchr(real, '/'); if (!slash) die("bad path"); *slash = 0;          // …/Contents/MacOS
    snprintf(tmp, sizeof tmp, "%s/../Resources", real);
    if (!realpath(tmp, res)) die("Resources not found");
    snprintf(home, sizeof home, "%s/python", res);
    snprintf(lib, sizeof lib, "%s/python/lib/libpython" PY_VER ".dylib", res);
    snprintf(script, sizeof script, "%s/brainai.py", res);

    setenv("PYTHONHOME", home, 1);
    setenv("PYTHONNOUSERSITE", "1", 1);
    setenv("PYTHONUNBUFFERED", "1", 1);
    setenv("PYTHONDONTWRITEBYTECODE", "1", 1);

    void *h = dlopen(lib, RTLD_NOW | RTLD_GLOBAL);
    if (!h) { fprintf(stderr, "dlopen: %s\n", dlerror()); die("libpython load failed"); }
    decode_fn decode = (decode_fn)dlsym(h, "Py_DecodeLocale");
    pymain_fn pymain = (pymain_fn)dlsym(h, "Py_Main");
    if (!decode || !pymain) die("Py_Main not found");

    // argv: [exe, script, any extra args except LaunchServices' -psn_*]
    wchar_t **wargv = calloc(argc + 2, sizeof(wchar_t *));
    int wc = 0;
    wargv[wc++] = decode(real, NULL);
    wargv[wc++] = decode(script, NULL);
    for (int i = 1; i < argc; i++) if (strncmp(argv[i], "-psn_", 5) != 0) wargv[wc++] = decode(argv[i], NULL);
    return pymain(wc, wargv);
}
