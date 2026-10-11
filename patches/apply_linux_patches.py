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
        '        "webrtc/common_audio/signal_processing/complex_bit_reverse.c",\n'
        '        "webrtc/common_audio/signal_processing/filter_ar_fast_q12.c",\n'
        '        "webrtc/common_audio/third_party/ooura/fft_size_128/ooura_fft_sse2.cc",\n'
        '        "webrtc/modules/audio_processing/aec3/adaptive_fir_filter_avx2.cc",\n'
        '        "webrtc/modules/audio_processing/aec3/adaptive_fir_filter_erl_avx2.cc",\n'
        '        "webrtc/modules/audio_processing/aec3/fft_data_avx2.cc",\n'
        '        "webrtc/modules/audio_processing/aec3/matched_filter_avx2.cc",\n'
        '        "webrtc/modules/audio_processing/aec3/vector_math_avx2.cc",\n'
        '        "webrtc/modules/audio_processing/agc2/rnn_vad/vector_math_avx2.cc",\n'
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

    # webrtc: declare the x86 SIMD headers in the cc_library hdrs so the
    # include validator accepts them (they are included unconditionally by
    # fir_filter_factory.cc on all platforms).
    p = "third-party/webrtc/BUILD"
    with open(p, "r", encoding="utf-8") as f:
        s = f.read()
    s2 = s.replace(
        '    "common_audio/fir_filter_neon.h",\n',
        '    "common_audio/fir_filter_neon.h",\n'
        '    "common_audio/fir_filter_sse.h",\n'
        '    "common_audio/fir_filter_avx2.h",\n',
        1,
    )
    assert s2 != s, "webrtc hdrs x86 pattern not found"
    with open(p, "w", encoding="utf-8") as f:
        f.write(s2)
    print("webrtc BUILD: x86 SIMD headers declared")

    # crc32c: on x86_64 the default branch must compile crc32c_sse42.cc
    # (crc32c.cc dispatches to ExtendSse42 when __SSE4_2__ is defined).
    p = "third-party/webrtc/crc32c/BUILD"
    with open(p, "r", encoding="utf-8") as f:
        s = f.read()
    s2 = s.replace(
        'arch_specific_crc32c_sources = select({\n'
        '    "@build_bazel_rules_apple//apple:ios_arm64": [\n'
        '        "third_party/crc32c/src/crc32c_arm64.cc",\n'
        '    ],\n'
        '    "//build-system:ios_sim_arm64": [\n'
        '        "third_party/crc32c/src/crc32c_arm64.cc",\n'
        '    ],\n'
        '    "//conditions:default": [],\n'
        '})',
        'arch_specific_crc32c_sources = select({\n'
        '    "@build_bazel_rules_apple//apple:ios_arm64": [\n'
        '        "third_party/crc32c/src/crc32c_arm64.cc",\n'
        '    ],\n'
        '    "//build-system:ios_sim_arm64": [\n'
        '        "third_party/crc32c/src/crc32c_arm64.cc",\n'
        '    ],\n'
        '    "//conditions:default": [\n'
        '        "third_party/crc32c/src/crc32c_sse42.cc",\n'
        '    ],\n'
        '})',
    )
    assert s2 != s, "crc32c BUILD pattern not found"
    s2 = s2.replace(
        '    copts = [\n'
        '        "-Ithird-party/webrtc/crc32c/third_party/crc32c/src/include"\n'
        '    ],',
        '    copts = select({\n'
        '        "@platforms//os:linux": [\n'
        '            "-Ithird-party/webrtc/crc32c/third_party/crc32c/src/include",\n'
        '            "-msse4.2",\n'
        '        ],\n'
        '        "//conditions:default": [\n'
        '            "-Ithird-party/webrtc/crc32c/third_party/crc32c/src/include",\n'
        '        ],\n'
        '    }),',
    )
    assert "-msse4.2" in s2, "crc32c copts pattern not found"
    with open(p, "w", encoding="utf-8") as f:
        f.write(s2)
    print("crc32c BUILD: crc32c_sse42.cc enabled on x86_64 default")

# --- 4. H2 repro: video on the 1:1 (p2p/reflector) path + callee sink flap ---
# Adds to tools/cli/main.cpp:
#   a) --sink-flap-ms <ms> flag (default 100; 0 = off)
#   b) with --video on a 1:1 mode: a caller VideoCaptureInterface fed by the
#      FakeVideoTrackSource (via the weak platform hooks from section 5), so
#      real encoded video flows caller -> callee and the callee's decoder
#      thread runs VideoSinkImpl::OnFrame continuously;
#   c) a callee-side sink-flap thread that alternates
#      setIncomingVideoOutput(sinkA/sinkB) every flap interval -- the app-side
#      mutator that races the decoder thread (addSink push_back vs OnFrame
#      iteration over the unsynchronized _sinks vector).
# All anchors are the upstream (pre-section-2) text; section 2 of this script
# has already fixed the designated-initializer order by the time we run.
p = os.path.join(ROOT, "tools/cli/main.cpp")
with open(p, "r", encoding="utf-8") as f:
    s = f.read()

# 4a) includes
old = '#include "VideoCaptureInterface.h"\n'
new = (
    '#include "VideoCaptureInterface.h"\n'
    '#include "VideoCapturerInterface.h"\n'
    '#include "StaticThreads.h"\n'
    '#include "platform/PlatformInterface.h"\n'
    '#include "fake_video_source.h"\n'
    '#include "fake_video_sink.h"\n'
)
assert old in s, "main.cpp: VideoCaptureInterface include anchor not found"
s = s.replace(old, new, 1)

# 4b) synthetic-source platform hooks + capturer (file scope, after the ADM stub)
old = "} // namespace webrtc\n"
new = '''} // namespace webrtc

// ---------------------------------------------------------------------------
// H2 repro support: synthetic video into the 1:1 engine.
// platform/fake/FakeInterface.cpp declares weak hooks; these strong
// definitions route source/capturer creation to FakeVideoTrackSource.
// ---------------------------------------------------------------------------
namespace {

class CliVideoCapturer : public tgcalls::VideoCapturerInterface {
public:
    explicit CliVideoCapturer(rtc::scoped_refptr<webrtc::VideoTrackSourceInterface> source)
        : _source(std::move(source)) {}
    void setState(tgcalls::VideoState) override {}
    void setPreferredCaptureAspectRatio(float) override {}
    void setUncroppedOutput(std::shared_ptr<rtc::VideoSinkInterface<webrtc::VideoFrame>>) override {}
    int getRotation() override { return 0; }
private:
    rtc::scoped_refptr<webrtc::VideoTrackSourceInterface> _source;
};

} // namespace

webrtc::scoped_refptr<webrtc::VideoTrackSourceInterface> tgcalls_s1_makeVideoSource(
        rtc::Thread *, rtc::Thread *) {
    static const auto source = FakeVideoTrackSource::Create(0);
    return source;
}

std::unique_ptr<tgcalls::VideoCapturerInterface> tgcalls_s1_makeVideoCapturer(
        webrtc::scoped_refptr<webrtc::VideoTrackSourceInterface> source,
        std::string deviceId,
        std::function<void(tgcalls::VideoState)> stateUpdated,
        std::function<void(tgcalls::PlatformCaptureInfo)> captureInfoUpdated,
        std::shared_ptr<tgcalls::PlatformContext> platformContext,
        std::pair<int, int> &outResolution) {
    outResolution = {1280, 720};
    if (stateUpdated) {
        stateUpdated(tgcalls::VideoState::Active);
    }
    return std::make_unique<CliVideoCapturer>(source);
}
'''
assert old in s, "main.cpp: ADM stub anchor not found"
s = s.replace(old, new, 1)

# 4c) --sink-flap-ms flag
old = 'bool enableVideo = false;\n'
new = 'bool enableVideo = false;\n    int sinkFlapMs = 100;\n'
assert old in s, "main.cpp: enableVideo declaration anchor not found"
s = s.replace(old, new, 1)

old = '''        } else if (std::string(argv[i]) == "--video") {
            enableVideo = true;
        }'''
new = '''        } else if (std::string(argv[i]) == "--video") {
            enableVideo = true;
        } else if (std::string(argv[i]) == "--sink-flap-ms" && i + 1 < argc) {
            sinkFlapMs = std::atoi(argv[++i]);
        }'''
assert old in s, "main.cpp: --video arg anchor not found"
s = s.replace(old, new, 1)

# 4d) caller capture + callee sinks + flap state, before the caller descriptor
old = '    auto callerDesc = (tgcalls::Descriptor){\n'
new = '''    // H2 repro: enable video on the 1:1 path. The caller gets a synthetic
    // capture (FakeVideoTrackSource via the platform hooks); the callee gets
    // two FakeVideoSinks that the flap thread below alternates between with
    // setIncomingVideoOutput -- racing the callee decoder thread's
    // VideoSinkImpl::OnFrame over the unsynchronized _sinks vector.
    const bool videoCall = enableVideo && (mode == "p2p" || mode == "reflector");
    std::shared_ptr<tgcalls::VideoCaptureInterface> callerCapture;
    std::shared_ptr<FakeVideoSink> calleeSinkA;
    std::shared_ptr<FakeVideoSink> calleeSinkB;
    std::atomic<bool> sinkFlapStop{false};
    std::thread sinkFlapThread;
    uint64_t sinkFlaps = 0;
    if (videoCall) {
        callerCapture = tgcalls::VideoCaptureInterface::Create(
            tgcalls::StaticThreads::getThreads(), "cli", false, nullptr);
        calleeSinkA = std::make_shared<FakeVideoSink>();
        calleeSinkB = std::make_shared<FakeVideoSink>();
        logMsg("Main", "video call enabled (sink flap every %dms)", sinkFlapMs);
    }

    auto callerDesc = (tgcalls::Descriptor){
'''
assert old in s, "main.cpp: callerDesc anchor not found"
s = s.replace(old, new, 1)

# 4e) .videoCapture on the caller descriptor (after encryptionKey, decl order)
old = '        .encryptionKey = tgcalls::EncryptionKey(keyData, true),\n'
new = (
    '        .encryptionKey = tgcalls::EncryptionKey(keyData, true),\n'
    '        .videoCapture = callerCapture,\n'
)
assert old in s, "main.cpp: caller encryptionKey anchor not found"
s = s.replace(old, new, 1)

# 4f) sink-flap thread start (before the duration sleep)
old = '    logMsg("Main", "sleeping for %d seconds...", duration);\n'
new = '''    if (videoCall && sinkFlapMs > 0) {
        auto calleeWeak = calleeInstance;
        sinkFlapThread = std::thread([&, calleeWeak]() {
            // wait for the callee to be Established (renderer attached phase)
            for (int i = 0; i < 300 && !sinkFlapStop.load(); i++) {
                bool up = false;
                {
                    std::lock_guard<std::mutex> lock(callState->mutex);
                    up = (callState->calleeState == tgcalls::State::Established);
                }
                if (up) break;
                std::this_thread::sleep_for(std::chrono::milliseconds(100));
            }
            bool a = true;
            auto next = std::chrono::steady_clock::now();
            const auto period = std::chrono::milliseconds(sinkFlapMs);
            while (!sinkFlapStop.load()) {
                next += period;
                calleeWeak->setIncomingVideoOutput(
                    a ? std::weak_ptr<rtc::VideoSinkInterface<webrtc::VideoFrame>>(calleeSinkA)
                      : std::weak_ptr<rtc::VideoSinkInterface<webrtc::VideoFrame>>(calleeSinkB));
                sinkFlaps++;
                logMsg("SinkFlap", "n=%llu attach %s", (unsigned long long)sinkFlaps, a ? "A" : "B");
                a = !a;
                std::this_thread::sleep_until(next);
            }
        });
    }

    logMsg("Main", "sleeping for %d seconds...", duration);
'''
assert old in s, "main.cpp: duration sleep anchor not found"
s = s.replace(old, new, 1)

# 4g) join the flap thread before teardown
old = '    logMsg("Main", "stopping instances...");\n'
new = '''    sinkFlapStop = true;
    if (sinkFlapThread.joinable()) {
        sinkFlapThread.join();
    }

    logMsg("Main", "stopping instances...");
'''
assert old in s, "main.cpp: stopping anchor not found"
s = s.replace(old, new, 1)

# 4h) summary evidence: flap count + decoded frame counts per sink
old = '        printf("BWE non-zero:      %s\\n", bweNonZero ? "yes" : "no");\n'
new = '''        printf("BWE non-zero:      %s\\n", bweNonZero ? "yes" : "no");
        if (videoCall) {
            printf("Video sink flaps:  %llu\\n", (unsigned long long)sinkFlaps);
            printf("Callee sinkA frames decoded: %d\\n", calleeSinkA ? calleeSinkA->frameCount() : -1);
            printf("Callee sinkB frames decoded: %d\\n", calleeSinkB ? calleeSinkB->frameCount() : -1);
        }
'''
assert old in s, "main.cpp: BWE summary anchor not found"
s = s.replace(old, new, 1)

with open(p, "w", encoding="utf-8") as f:
    f.write(s)
print("main.cpp: 1:1 video path + sink-flap simulator patched in")

# --- 5. FakeInterface weak hooks (default-off synthetic source/capturer) -----
# tgcalls_s1_makeVideoSource / tgcalls_s1_makeVideoCapturer are declared weak
# here; tgcalls_cli provides the strong definitions (section 4b). Without the
# strong definitions the symbols resolve to null and behavior is unchanged.
p = os.path.join(ROOT, "tgcalls/platform/fake/FakeInterface.cpp")
with open(p, "r", encoding="utf-8") as f:
    s = f.read()

old = '''webrtc::scoped_refptr<webrtc::VideoTrackSourceInterface> FakeInterface::makeVideoSource(rtc::Thread *signalingThread,
                                                                                     rtc::Thread *workerThread) {
  return nullptr;
}'''
new = '''// H2 repro hooks (weak, default-off): tgcalls_cli provides strong
// definitions that create a synthetic video source/capturer. Signatures must
// match tools/cli/main.cpp verbatim.
webrtc::scoped_refptr<webrtc::VideoTrackSourceInterface> tgcalls_s1_makeVideoSource(
    rtc::Thread *signalingThread,
    rtc::Thread *workerThread) __attribute__((weak));
std::unique_ptr<VideoCapturerInterface> tgcalls_s1_makeVideoCapturer(
    webrtc::scoped_refptr<webrtc::VideoTrackSourceInterface> source,
    std::string deviceId,
    std::function<void(VideoState)> stateUpdated,
    std::function<void(PlatformCaptureInfo)> captureInfoUpdated,
    std::shared_ptr<PlatformContext> platformContext,
    std::pair<int, int> &outResolution) __attribute__((weak));

webrtc::scoped_refptr<webrtc::VideoTrackSourceInterface> FakeInterface::makeVideoSource(rtc::Thread *signalingThread,
                                                                                     rtc::Thread *workerThread) {
  if (tgcalls_s1_makeVideoSource) {
    return tgcalls_s1_makeVideoSource(signalingThread, workerThread);
  }
  return nullptr;
}'''
assert old in s, "FakeInterface: makeVideoSource anchor not found"
s = s.replace(old, new, 1)

old = '''    std::shared_ptr<PlatformContext> platformContext, std::pair<int, int> &outResolution) {
  return nullptr;
  //return std::make_unique<VideoCapturerInterfaceImpl>(source, deviceId, stateUpdated, outResolution);
}'''
new = '''    std::shared_ptr<PlatformContext> platformContext, std::pair<int, int> &outResolution) {
  if (tgcalls_s1_makeVideoCapturer) {
    return tgcalls_s1_makeVideoCapturer(source, deviceId, stateUpdated, captureInfoUpdated,
                                        platformContext, outResolution);
  }
  return nullptr;
  //return std::make_unique<VideoCapturerInterfaceImpl>(source, deviceId, stateUpdated, outResolution);
}'''
assert old in s, "FakeInterface: makeVideoCapturer anchor not found"
s = s.replace(old, new, 1)

with open(p, "w", encoding="utf-8") as f:
    f.write(s)
print("FakeInterface.cpp: weak video hooks patched in")
