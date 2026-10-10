#!/usr/bin/env python3
"""Post-clone patches so the tgcalls Bazel build works with GCC on Linux:

1. Apple libc++ includes standard headers transitively; GCC/libstdc++ does not.
   Prepend threading/utility headers to every tgcalls source using those symbols.
2. GCC requires designated initializers in declaration order (Clang does not).
   Fix tools/cli/main.cpp where .logPath/.statsLogPath are swapped, and add the
   missing .logPath designator in the callee descriptor.
3. GNU ld resolves static archives left-to-right; upstream ffmpeg BUILD lists
   libavutil.a before libavcodec.a, leaving avutil symbols unresolved at link
   time (Apple ld is tolerant). Append a trailing cc_library carrying
   libavutil.a/libswresample.a after the main ffmpeg libs.
"""
import os
import re

ROOT = "submodules/TgVoipWebrtc/tgcalls"

# --- 1. missing standard includes -------------------------------------------
sym_re = re.compile(
    r"std::(mutex|lock_guard|unique_lock|scoped_lock|condition_variable|"
    r"thread|atomic|call_once|once_flag|shared_mutex)"
)
prefix = "#include <mutex>\n#include <condition_variable>\n#include <atomic>\n#include <thread>\n"
patched = []
for base in ("tgcalls", "tools"):
    for dirpath, _, filenames in os.walk(base):
        for name in filenames:
            if not name.endswith((".cpp", ".h", ".hpp")):
                continue
            path = os.path.join(dirpath, name)
            with open(path, "r", encoding="utf-8", errors="surrogateescape") as f:
                src = f.read()
            if sym_re.search(src) and "#include <mutex>" not in src:
                with open(path, "w", encoding="utf-8", errors="surrogateescape") as f:
                    f.write(prefix + src)
                patched.append(path)
print(f"include-patched {len(patched)} files")

# --- 2. designated initializer order in tools/cli/main.cpp -------------------
p = os.path.join(ROOT, "tools/cli/main.cpp")
with open(p, "r", encoding="utf-8") as f:
    s = f.read()

caller_old = (
    "            .statsLogPath = {callerStatsPath},\n"
    "            // rtc log sinks are process-global, so wiring logPath on just the caller\n"
    "            // captures RTC_LOG output for both sides of the call.\n"
    "            .logPath = {logFile},"
)
caller_new = (
    "            .logPath = {logFile},\n"
    "            // rtc log sinks are process-global, so wiring logPath on just the caller\n"
    "            // captures RTC_LOG output for both sides of the call.\n"
    "            .statsLogPath = {callerStatsPath},"
)
assert caller_old in s, "caller descriptor pattern not found"
s = s.replace(caller_old, caller_new, 1)

callee_old = "            .statsLogPath = {calleeStatsPath},"
callee_new = "            .logPath = {},\n            .statsLogPath = {calleeStatsPath},"
assert callee_old in s, "callee descriptor pattern not found"
s = s.replace(callee_old, callee_new, 1)

with open(p, "w", encoding="utf-8") as f:
    f.write(s)
print("main.cpp designated initializer order fixed")

# --- 3. GNU ld archive order for ffmpeg --------------------------------------
p = "submodules/ffmpeg/BUILD"
with open(p, "r", encoding="utf-8") as f:
    s = f.read()

if "ffmpeg_lib_tail" not in s:
    tail = (
        'cc_library(\n'
        '    name = "ffmpeg_lib_tail",\n'
        '    srcs = ["libavutil.a", "libswresample.a"],\n'
        '    visibility = ["//visibility:public"],\n'
        ")\n"
        "\n"
        'cc_library(\n'
        '    name = "ffmpeg",'
    )
    assert 'cc_library(\n    name = "ffmpeg",' in s, "ffmpeg cc_library pattern not found"
    s = s.replace('cc_library(\n    name = "ffmpeg",', tail, 1)
    old_deps = 'deps = [\n        ":ffmpeg_lib",'
    assert old_deps in s, "ffmpeg deps pattern not found"
    s = s.replace(old_deps, 'deps = [\n        ":ffmpeg_lib",\n        ":ffmpeg_lib_tail",', 1)
    with open(p, "w", encoding="utf-8") as f:
        f.write(s)
    print("ffmpeg BUILD: appended ffmpeg_lib_tail for GNU ld archive order")
else:
    print("ffmpeg BUILD: ffmpeg_lib_tail already present")
