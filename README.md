# tgcalls-building

Builds the [tgcalls](https://github.com/TelegramMessenger/tgcalls) library (Telegram's VoIP engine,
as pinned by [Telegram-iOS](https://github.com/TelegramMessenger/Telegram-iOS)) entirely on
**GitHub Actions** and publishes the executable + its libraries as a downloadable artifact,
so no local compilation is needed.

## What the workflow builds

- Target: `tgcalls_cli` — the tgcalls command-line test/verification executable
  (source: `Telegram-iOS` → submodule `submodules/TgVoipWebrtc/tgcalls` → `tools/cli`).
- Platform: `ubuntu-24.04-arm` (ARM64 Linux — the WASI SDK pinned by the repo is arm64-linux).
- Toolchain: Bazel 8.4.2 + system GCC, exactly as documented in the tgcalls repo (`CLAUDE.md` / `Dockerfile`).

## How to use

1. Go to the **Actions** tab → **Build tgcalls (Linux ARM64)** → **Run workflow**.
2. Wait for the job to finish (~40–90 min on first run; the full build is done remotely).
3. Download the artifact `tgcalls-cli-linux-arm64` from the run page.
4. Unpack locally:
   ```bash
   tar -xzf tgcalls-cli-linux-arm64.tar.gz
   chmod +x tgcalls_cli
   ./tgcalls_cli --help
   ./tgcalls_cli --mode p2p --duration 10
   ```
   `tgcalls_cli-ldd.txt` lists the shared libraries the binary needs (standard Ubuntu libs;
   install the matching runtime packages if your distro differs).

## Files

- `.github/workflows/build-tgcalls.yml` — the build pipeline.

## Notes

- The upstream repo does not publish `build-input/`, so the workflow recreates it by
  downloading the Bazel 8.4.2 binary, mirroring the official Dockerfile.
- Artifacts are retained 30 days per run.
