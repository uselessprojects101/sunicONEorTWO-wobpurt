#!/usr/bin/env python3
"""Applies the web-port changes to the engine/ folder (safe to run repeatedly).

Run from the repo root:  python3 tools/setup_web.py
Each change is an exact text replacement. If the engine source has changed so
that a spot can't be found, the script stops and says which one.
"""
import pathlib, shutil, sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
ENGINE = ROOT / "engine"
MARK = "WEB-PORT"

def patch(relpath, old, new, label):
    p = ENGINE / relpath
    text = p.read_text(encoding="utf-8")
    if MARK + ":" + label in text:
        print(f"  already applied: {label}")
        return
    n = text.count(old)
    if n != 1:
        sys.exit(f"ERROR: patch '{label}' needs exactly 1 match in {relpath}, found {n}.\n"
                 f"The engine code probably changed; send me this message.")
    p.write_text(text.replace(old, new, 1), encoding="utf-8")
    print(f"  applied: {label}")

if not (ENGINE / "CMakeLists.txt").exists():
    sys.exit("ERROR: engine/CMakeLists.txt not found. Run this from the repo root.")

print("Copying platforms/Emscripten.cmake")
shutil.copyfile(ROOT / "web" / "Emscripten.cmake", ENGINE / "platforms" / "Emscripten.cmake")
print("Copying RSDKv4/NetworkingWeb.cpp")
shutil.copyfile(ROOT / "web" / "NetworkingWeb.cpp", ENGINE / "RSDKv4" / "NetworkingWeb.cpp")

# 1. OpenGL headers: Emscripten has no GLEW, use its own GL headers
patch("RSDKv4/RetroEngine.hpp",
"""#else
#include <GL/glew.h>
#endif
#endif
""",
"""#elif defined(__EMSCRIPTEN__) // WEB-PORT:gl-headers
#define GL_GLEXT_PROTOTYPES
#include <GL/gl.h>
#include <GL/glext.h>
#else
#include <GL/glew.h>
#endif
#endif
""", "gl-headers")

# 2. SDL init: only ask for the subsystems the web supports
patch("RSDKv4/Drawing.cpp",
"""    SDL_Init(SDL_INIT_EVERYTHING);

    SDL_DisableScreenSaver();""",
"""#ifdef __EMSCRIPTEN__ // WEB-PORT:sdl-init
    SDL_Init(SDL_INIT_VIDEO | SDL_INIT_AUDIO | SDL_INIT_TIMER | SDL_INIT_EVENTS | SDL_INIT_JOYSTICK | SDL_INIT_GAMECONTROLLER);
#else
    SDL_Init(SDL_INIT_EVERYTHING);
#endif

    SDL_DisableScreenSaver();""", "sdl-init")

# 3. No "core profile" request in the browser
patch("RSDKv4/Drawing.cpp",
"""    SDL_GL_SetAttribute(SDL_GL_CONTEXT_PROFILE_MASK, SDL_GL_CONTEXT_PROFILE_CORE);
""",
"""#ifndef __EMSCRIPTEN__ // WEB-PORT:gl-profile
    SDL_GL_SetAttribute(SDL_GL_CONTEXT_PROFILE_MASK, SDL_GL_CONTEXT_PROFILE_CORE);
#endif
""", "gl-profile")

# 4. No GLEW in the browser
patch("RSDKv4/Drawing.cpp",
"""#if RETRO_PLATFORM != RETRO_ANDROID && RETRO_PLATFORM != RETRO_OSX
    GLenum err = glewInit();""",
"""#if RETRO_PLATFORM != RETRO_ANDROID && RETRO_PLATFORM != RETRO_OSX && !defined(__EMSCRIPTEN__) // WEB-PORT:glew
    GLenum err = glewInit();""", "glew")

# 5. Browser-friendly main loop (a browser can't run a blocking while-loop)
patch("RSDKv4/RetroEngine.cpp",
"""void RetroEngine::Run()
{""",
"""#ifdef __EMSCRIPTEN__ // WEB-PORT:frame-fn
#include <emscripten.h>

static double emLastMs  = 0.0;
static double emAccumMs = 0.0;

// One engine frame, called by the browser's requestAnimationFrame.
// The game is built around a fixed 60 updates per second, so we accumulate
// real time and only step the game when a full 1/60th of a second is due.
static void EmscriptenFrame()
{
    double now = emscripten_get_now();
    double dt  = now - emLastMs;
    emLastMs   = now;
    if (dt > 250.0)
        dt = 250.0; // tab was in the background: don't fast-forward
    emAccumMs += dt;

    const double stepMs = 1000.0 / (Engine.refreshRate > 0 ? Engine.refreshRate : 60);
    if (emAccumMs < stepMs - 1.0)
        return;
    emAccumMs -= stepMs;
    if (emAccumMs > stepMs)
        emAccumMs = stepMs;

    Engine.deltaTime = 1.0 / 60;
    Engine.running   = ProcessEvents();
    if (!Engine.running) {
        emscripten_cancel_main_loop();
        WriteSettings();
        return;
    }

    // Focus Checks
    if (!(disableFocusPause & 2)) {
        if (!Engine.hasFocus) {
            if (!(Engine.focusState & 1))
                Engine.focusState = PauseSound() ? 3 : 1;
        }
        else if (Engine.focusState) {
            if ((Engine.focusState & 2))
                ResumeSound();
            Engine.focusState = 0;
        }
    }

    if (!(Engine.focusState & 1) || vsPlaying) {
        for (int s = 0; s < Engine.gameSpeed; ++s) {
            ProcessInput();
            if (!Engine.masterPaused || Engine.frameStep) {
                ProcessNativeObjects();
            }
        }

        if (!Engine.masterPaused || Engine.frameStep) {
            FlipScreen();
            SDL_GL_SwapWindow(Engine.window);
            Engine.frameStep = false;
        }

#if RETRO_REV00
        Engine.message = MESSAGE_NONE;
#endif
    }
}
#endif

void RetroEngine::Run()
{""", "frame-fn")

patch("RSDKv4/RetroEngine.cpp",
"""    while (running) {
#if !RETRO_USE_ORIGINAL_CODE
        if (!vsync) {""",
"""#ifdef __EMSCRIPTEN__ // WEB-PORT:run-branch
    if (running) {
        emLastMs  = emscripten_get_now();
        emAccumMs = 0.0;
        emscripten_set_main_loop(EmscriptenFrame, 0, 1);
        return;
    }
#endif

    while (running) {
#if !RETRO_USE_ORIGINAL_CODE
        if (!vsync) {""", "run-branch")

# 6. If the browser can't give us a WebGL context, say why (on the page) and stop cleanly
patch("RSDKv4/Drawing.cpp",
"""#include "RetroEngine.hpp"
""",
"""#include "RetroEngine.hpp"
#ifdef __EMSCRIPTEN__ // WEB-PORT:em-include
#include <emscripten.h>
#endif
""", "em-include")

patch("RSDKv4/Drawing.cpp",
"""    Engine.glContext = SDL_GL_CreateContext(Engine.window);
""",
"""    Engine.glContext = SDL_GL_CreateContext(Engine.window);
#ifdef __EMSCRIPTEN__ // WEB-PORT:gl-context-check
    if (!Engine.glContext) {
        EM_ASM({ if (window.diagLog) diagLog("WebGL context could not be created: " + UTF8ToString($0)); }, SDL_GetError());
        return 0;
    }
#endif
""", "gl-context-check")

# 7. Ask for an OpenGL ES 2.0 context. Browsers only offer WebGL (= ES 2.0); asking for
#    version 1.0 made the EGL layer fail with EGL_BAD_CONFIG. Emscripten's legacy GL
#    emulation still provides the old fixed-function calls on top of it.
patch("RSDKv4/Drawing.cpp",
"""    SDL_GL_SetAttribute(SDL_GL_CONTEXT_MAJOR_VERSION, 1);
    SDL_GL_SetAttribute(SDL_GL_CONTEXT_MINOR_VERSION, 0);
""",
"""#ifdef __EMSCRIPTEN__ // WEB-PORT:gl-version
    SDL_GL_SetAttribute(SDL_GL_CONTEXT_PROFILE_MASK, SDL_GL_CONTEXT_PROFILE_ES);
    SDL_GL_SetAttribute(SDL_GL_CONTEXT_MAJOR_VERSION, 2);
    SDL_GL_SetAttribute(SDL_GL_CONTEXT_MINOR_VERSION, 0);
#else
    SDL_GL_SetAttribute(SDL_GL_CONTEXT_MAJOR_VERSION, 1);
    SDL_GL_SetAttribute(SDL_GL_CONTEXT_MINOR_VERSION, 0);
#endif
""", "gl-version")

# 8. Multiplayer: the desktop networking code (UDP + threads) can't run in a browser.
#    Switch it off for the web build and use web/NetworkingWeb.cpp (WebSockets) instead.
patch("RSDKv4/Networking.cpp",
"""#include "RetroEngine.hpp"
#if RETRO_USE_NETWORKING
""",
"""#include "RetroEngine.hpp"
#if RETRO_USE_NETWORKING && !defined(__EMSCRIPTEN__) // WEB-PORT:net-desktop-off
""", "net-desktop-off")

patch("RSDKv4/Networking.hpp",
"""#include <thread>
#include <memory>
""",
"""#ifndef __EMSCRIPTEN__ // WEB-PORT:net-header
#include <thread>
#endif
#include <memory>
""", "net-header")

# the browser hands us packets once per frame
patch("RSDKv4/RetroEngine.cpp",
"""static void EmscriptenFrame()
{
    double now = emscripten_get_now();""",
"""#if RETRO_USE_NETWORKING
void NetworkWebPoll(); // WEB-PORT:net-poll
#endif

static void EmscriptenFrame()
{
#if RETRO_USE_NETWORKING
    NetworkWebPoll();
#endif
    double now = emscripten_get_now();""", "net-poll")

# 9. Multiplayer: load the transport scripts in the page (web/net.js + web/peerjs.min.js).
shell_path = ROOT / "web" / "shell.html"
shell = shell_path.read_text(encoding="utf-8")
if 'src="net.js"' in shell:
    print("  already applied: page-net-scripts")
else:
    if shell.count("{{{ SCRIPT }}}") != 1:
        sys.exit("ERROR: could not find {{{ SCRIPT }}} exactly once in web/shell.html.")
    shell = shell.replace("{{{ SCRIPT }}}",
        '<script src="peerjs.min.js"></script>\n<script src="net.js"></script>\n{{{ SCRIPT }}}', 1)
    shell_path.write_text(shell, encoding="utf-8")
    print("  applied: page-net-scripts")

# 10. Menu/3D shading. The engine puts its light at the camera as a point light (0,0,0,1).
#     Emscripten's GL emulation treats the position as a DIRECTION, so that light pointed nowhere
#     and every surface came out flat and dim. This engine's camera looks down +z (see
#     SetPerspectiveMatrix), so a light shining from the viewer has direction (0,0,-1).
#     ?lightz=1 on the page address flips it, for testing without rebuilding.
LIGHT_OLD = """    float lightPos[4]     = { 0.0, 0.0, 0.0, 1.0 };
"""
LIGHT_V1 = """#ifdef __EMSCRIPTEN__ // WEB-PORT:light-dir
    float lightPos[4]     = { 0.0, 0.0, 1.0, 0.0 };
#else
    float lightPos[4]     = { 0.0, 0.0, 0.0, 1.0 };
#endif
"""
LIGHT_V2 = """#ifdef __EMSCRIPTEN__ // WEB-PORT:light-dir
    // direction the light shines FROM, in eye space. This engine looks down +z, so -z is "from the viewer".
    // Try ?lightz=1 on the page address to test the other way.
    float lightZ          = (float)EM_ASM_DOUBLE({ var v = parseFloat(new URLSearchParams(location.search).get("lightz")); return isNaN(v) ? -1 : v; });
    float lightPos[4]     = { 0.0, 0.0, lightZ, 0.0 };
#else
    float lightPos[4]     = { 0.0, 0.0, 0.0, 1.0 };
#endif
"""
_dp = ENGINE / "RSDKv4" / "Drawing.cpp"
_dt = _dp.read_text(encoding="utf-8")
if LIGHT_V1 in _dt:   # first version of this fix (wrong direction): upgrade it
    _dp.write_text(_dt.replace(LIGHT_V1, LIGHT_V2, 1), encoding="utf-8")
    print("  applied: light-dir (upgraded)")
else:
    patch("RSDKv4/Drawing.cpp", LIGHT_OLD, LIGHT_V2, "light-dir")

print("Done.")
