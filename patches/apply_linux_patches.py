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
for base in (os.path.join(ROOT, "tgcalls"), os.path.join(ROOT, "tools")):
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

# GNU ld resolves static archives left-to-right and Bazel does not duplicate
# an archive on the link line, so the only fix is to order the archives by
# dependency: avformat -> avcodec -> swresample -> avutil (as pkg-config does).
# Upstream's mac-friendlier order (avutil first) leaves avutil symbols
# referenced from libavcodec.a unresolved.
old_libs = (
    'ffmpeg_libs = [\n'
    '    "libavutil.a",\n'
    '    "libavcodec.a",\n'
    '    "libavformat.a",\n'
    '    "libswresample.a",\n'
    ']'
)
new_libs = (
    '# Linux (GNU ld): archives must be ordered by dependency:\n'
    '# avformat -> avcodec -> swresample -> avutil.\n'
    'ffmpeg_libs = [\n'
    '    "libavformat.a",\n'
    '    "libavcodec.a",\n'
    '    "libswresample.a",\n'
    '    "libavutil.a",\n'
    ']'
)
assert old_libs in s, "ffmpeg_libs list not found"
s = s.replace(old_libs, new_libs, 1)
with open(p, "w", encoding="utf-8") as f:
    f.write(s)
# --- 4. x86_64 hosts: swap arm64 wasi-sdk pin for the x86_64 SDK ---------
if os.environ.get("WASI_X86") == "1":
    import re as _re
    p = "MODULE.bazel"
    with open(p, "r", encoding="utf-8") as f:
        s = f.read()
    s = s.replace("wasi-sdk-33.0-arm64-linux", "wasi-sdk-33.0-x86_64-linux")  # url + strip_prefix
    # sha256 of wasi-sdk-33.0-x86_64-linux.tar.gz (verified download)
    x86_sha = "0ba8b5bfaeb2adf3f29bab5841d76cf5318ab8e1642ea195f88baba1abd47bce"
    s, n = _re.subn(
        r'(name = "wasi_sdk_linux",\s*urls = \[[^\]]*\],\s*sha256 = ")[0-9a-f]{64}(")',
        r"\g<1>" + x86_sha + r"\g<2>",
        s,
        count=1,
    )
    assert n == 1, "wasi_sdk_linux sha256 not found/updated"
    with open(p, "w", encoding="utf-8") as f:
        f.write(s)
    print("MODULE.bazel: wasi_sdk_linux switched to x86_64 variant")

print("ffmpeg BUILD: reordered ffmpeg_libs for GNU ld archive resolution")

# --- 5. openh264: don't compile arm64 NEON asm on x86_64 hosts -----------
if os.environ.get("WASI_X86") == "1":
    p = "third-party/openh264/BUILD"
    with open(p, "r", encoding="utf-8") as f:
        s = f.read()
    # The //conditions:default branch falls back to arm64 asm sources; make
    # all three selects use the (empty) ios_x86_64 branch as default.
    s2 = s.replace(
        '    "@build_bazel_rules_apple//apple:ios_x86_64": [],\n    "//conditions:default": arm64_specific_sources,',
        '    "@build_bazel_rules_apple//apple:ios_x86_64": [],\n    "//conditions:default": [],',
    )
    s2 = s2.replace(
        '    "@build_bazel_rules_apple//apple:ios_x86_64": [],\n    "//conditions:default": arm64_specific_copts,',
        '    "@build_bazel_rules_apple//apple:ios_x86_64": [],\n    "//conditions:default": [],',
    )
    s2 = s2.replace(
        '    "@build_bazel_rules_apple//apple:ios_x86_64": [],\n    "//conditions:default": arm64_specific_textual_hdrs,',
        '    "@build_bazel_rules_apple//apple:ios_x86_64": [],\n    "//conditions:default": [],',
    )
    assert s2 != s, "openh264 BUILD patterns not found"
    with open(p, "w", encoding="utf-8") as f:
        f.write(s2)
    print("openh264 BUILD: arm64 asm excluded on default (x86_64) platform")

    # wamr: swap the aarch64 invoke-native asm and target defines for x86_64
    p = "third-party/wamr/BUILD"
    with open(p, "r", encoding="utf-8") as f:
        s = f.read()
    s2 = s.replace(
        '"core/iwasm/common/arch/invokeNative_aarch64.s",',
        '"core/iwasm/common/arch/invokeNative_em64.s",',
    )
    assert s2 != s, "wamr invokeNative_aarch64.s pattern not found"
    s2 = s2.replace('-DBUILD_TARGET_AARCH64=1', '-DBUILD_TARGET_X86_64=1')
    s2 = s2.replace('-DBUILD_TARGET=\\\\\\"AARCH64\\\\\\"', '-DBUILD_TARGET=\\\\\\"X86_64\\\\\\"')
    assert 'BUILD_TARGET_X86_64=1' in s2, "wamr BUILD_TARGET swap failed"
    with open(p, "w", encoding="utf-8") as f:
        f.write(s2)
    print("wamr BUILD: switched to x86_64 invoke-native asm and target defines")

    # webrtc: on x86_64, exclude the arm NEON sources and arch flags
    p = "third-party/webrtc/BUILD"
    with open(p, "r", encoding="utf-8") as f:
        s = f.read()
    s2 = s.replace(
        'arch_specific_sources = select({\n'
        '    "@build_bazel_rules_apple//apple:ios_arm64": common_arm_specific_sources + arm64_specific_sources,\n'
        '    "//build-system:ios_sim_arm64": common_arm_specific_sources + arm64_specific_sources,\n'
        '    "//conditions:default": common_arm_specific_sources + arm64_specific_sources,\n'
        '})',
        'arch_specific_sources = select({\n'
        '    "@build_bazel_rules_apple//apple:ios_arm64": common_arm_specific_sources + arm64_specific_sources,\n'
        '    "//build-system:ios_sim_arm64": common_arm_specific_sources + arm64_specific_sources,\n'
        '    "//conditions:default": [],  # x86_64: no arm NEON sources\n'
        '})',
    )
    assert s2 != s, "webrtc arch_specific_sources pattern not found"
    s2 = s2.replace(
        '    "@platforms//os:linux": platform_shared_flags + linux_specific_flags + arm64_specific_flags,\n'
        '    "//conditions:default": platform_shared_flags + apple_specific_flags + arm64_specific_flags,',
        '    "@platforms//os:linux": platform_shared_flags + linux_specific_flags,\n'
        '    "//conditions:default": platform_shared_flags + apple_specific_flags,',
    )
    assert 'linux_specific_flags,\n    "//conditions:default": platform_shared_flags + apple_specific_flags,' in s2, "webrtc cflags pattern not found"
    with open(p, "w", encoding="utf-8") as f:
        f.write(s2)
    print("webrtc BUILD: arm NEON sources/flags excluded on x86_64")

    # webrtc: on x86_64, wire in the SSE/AVX2 fir-filter sources that
    # fir_filter_factory.cc unconditionally includes (headers at minimum).
    p = "third-party/webrtc/BUILD"
    with open(p, "r", encoding="utf-8") as f:
        s = f.read()
    s2 = s.replace(
        '    "//conditions:default": [],  # x86_64: no arm NEON sources\n'
        '})',
        '    "//conditions:default": [\n'
        '        "webrtc/common_audio/fir_filter_sse.cc",\n'
        '        "webrtc/common_audio/fir_filter_avx2.cc",\n'
        '        "webrtc/common_audio/resampler/sinc_resampler_sse.cc",\n'
        '        "webrtc/common_audio/resampler/sinc_resampler_avx2.cc",\n'
        '    ],  # x86_64 SIMD sources\n'
        '})',
    )
    assert s2 != s, "webrtc x86_64 SIMD sources patch failed"
    # add the x86 arch flags (SSE2/AVX2) to the linux branch
    s2 = s2.replace(
        '    "@platforms//os:linux": platform_shared_flags + linux_specific_flags,\n'
        '    "//conditions:default": platform_shared_flags + apple_specific_flags,',
        '    "@platforms//os:linux": platform_shared_flags + linux_specific_flags + x86_specific_flags,\n'
        '    "//conditions:default": platform_shared_flags + apple_specific_flags + x86_specific_flags,',
    )
    s2 = s2.replace(
        'arm64_specific_flags = [\n'
        '    "-DWEBRTC_ARCH_ARM64",\n'
        '    "-DWEBRTC_HAS_NEON",\n'
        '    "-DLIBYUV_NEON",\n'
        ']\n',
        'arm64_specific_flags = [\n'
        '    "-DWEBRTC_ARCH_ARM64",\n'
        '    "-DWEBRTC_HAS_NEON",\n'
        '    "-DLIBYUV_NEON",\n'
        ']\n'
        '\n'
        'x86_specific_flags = [\n'
        '    "-msse2",\n'
        '    "-mavx2",\n'
        '    "-mfma",\n'
        ']\n',
    )
    assert 'x86_specific_flags = [' in s2, "x86_specific_flags insertion failed"
    with open(p, "w", encoding="utf-8") as f:
        f.write(s2)
    print("webrtc BUILD: x86_64 SIMD sources and flags wired in")
