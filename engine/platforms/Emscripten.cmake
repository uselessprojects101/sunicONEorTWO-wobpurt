# Emscripten (web) platform file for the RSDKv4 decompilation.
# Picked automatically by engine/CMakeLists.txt when building with emcmake,
# because CMAKE_SYSTEM_NAME is "Emscripten".

add_executable(RetroEngine ${RETRO_FILES})

set_target_properties(RetroEngine PROPERTIES
    CXX_STANDARD 17
    CXX_STANDARD_REQUIRED ON
    SUFFIX ".html"
)

# Multiplayer over WebSockets (replaces the desktop UDP code, which Networking.cpp skips on the web)
target_sources(RetroEngine PRIVATE ${CMAKE_SOURCE_DIR}/RSDKv4/NetworkingWeb.cpp)

# Our custom page (file picker, fullscreen canvas, save syncing)
set(RETRO_WEB_SHELL "${CMAKE_SOURCE_DIR}/../web/shell.html" CACHE FILEPATH "HTML shell for the web build")

# The engine looks for Data.rsdk, settings.ini and save files in BASE_PATH.
# /data is an IndexedDB-backed folder, so saves survive page reloads.
target_compile_definitions(RetroEngine PRIVATE BASE_PATH="/data/")

# SDL2 + ogg + vorbis come from Emscripten's built-in ports
set(WEB_PORT_FLAGS -sUSE_SDL=2 -sUSE_OGG=1 -sUSE_VORBIS=1)

target_compile_options(RetroEngine PRIVATE ${WEB_PORT_FLAGS})

target_link_options(RetroEngine PRIVATE
    ${WEB_PORT_FLAGS}
    -sLEGACY_GL_EMULATION=1
    -sALLOW_MEMORY_GROWTH=1
    -sINITIAL_MEMORY=268435456
    -sSTACK_SIZE=5242880
    -sFORCE_FILESYSTEM=1
    -sEXIT_RUNTIME=0
    -lidbfs.js
    -sEXPORTED_RUNTIME_METHODS=callMain,FS,IDBFS,addRunDependency,removeRunDependency
    "SHELL:--shell-file ${RETRO_WEB_SHELL}"
)

set_target_properties(RetroEngine PROPERTIES LINK_DEPENDS "${RETRO_WEB_SHELL}")
