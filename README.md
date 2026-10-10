# tgcalls-building

Builds the [tgcalls](https://github.com/TelegramMessenger/tgcalls) library (Telegram's VoIP engine,
as pinned by [Telegram-iOS](https://github.com/TelegramMessenger/Telegram-iOS)) entirely on
**GitHub Actions** and publishes the executable + its libraries as a downloadable artifact,
so no local compilation is needed.

## What the workflow builds

- Target: `tgcalls_cli` — the tgcalls command-line test/verification executable
  (source: `Telegram-iOS` → submodule `submodules/TgVoipWebrtc/tgcalls` → `tools/cli`).
- Two platforms, one workflow each:
  - `ubuntu-24.04-arm` → artifact `tgcalls-cli-linux-arm64`
  - `ubuntu-24.04` → artifact `tgcalls-cli-linux-x86_64`
- Toolchain: Bazel 8.4.2 + system GCC, exactly as documented in the tgcalls repo (`CLAUDE.md` / `Dockerfile`).

## How to use

1. Go to the **Actions** tab → pick **Build tgcalls (Linux x86_64)** (or the ARM64 one) → **Run workflow**.
2. Wait for the job to finish (~10–40 min; the full build is done remotely).
3. Download the artifact from the run page.
4. Unpack locally:
   ```bash
   tar -xzf tgcalls-cli-linux-x86_64.tar.gz
   chmod +x tgcalls_cli
   ./tgcalls_cli --mode p2p --duration 10
   ```
   Verified locally: `Caller state: Established / Callee state: Established / Errors: none`.
   `tgcalls_cli-ldd.txt` lists the shared libraries (standard Ubuntu libs only:
   `libm`, `libz`, `libbz2`, `libstdc++`, `libgcc_s`, `libc`).

## Files

- `.github/workflows/build-tgcalls.yml` — ARM64 build pipeline.
- `.github/workflows/build-tgcalls-x86.yml` — x86_64 build pipeline.
- `patches/apply_linux_patches.py` — post-clone patches that make the upstream
  tree build with GCC on Linux (see below).

## Patches applied by the workflows (and why)

Upstream targets macOS/Clang; these are the minimal fixes for a Linux/GCC build:

1. **Missing standard includes** — Apple libc++ includes `<mutex>`, `<atomic>`, etc.
   transitively; GCC does not. Headers are prepended to the affected sources.
2. **Designated initializer order** — GCC requires declaration order; Clang does not
   (`tools/cli/main.cpp` `.logPath`/`.statsLogPath`).
3. **GNU ld archive order** — `ffmpeg_libs` reordered to
   `avformat → avcodec → swresample → avutil` (left-to-right resolution).
4. **Arch-specific fixes for x86_64** — wasi-sdk pin swapped to the x86_64 archive;
   openh264/wamr/webrtc arm-NEON asm excluded; webrtc SSE/AVX2/FMA sources and flags
   wired in; `crc32c_sse42.cc` enabled (with `-msse4.2`).

## Notes

- The upstream repo does not publish `build-input/`, so the workflows recreate it by
  downloading the Bazel 8.4.2 binary plus a stub configuration repository, mirroring
  `build-system/Make/Make.py`.
- Artifacts are retained 30 days per run.
