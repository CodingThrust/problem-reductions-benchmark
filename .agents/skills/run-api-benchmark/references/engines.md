# Getting a container engine, per platform

Read this when `detect-engine.sh` reports `ENGINE=none` (install one). The benchmark image
compiles a Rust CLI, so the recurring failure everywhere is **build OOM (exit 137)** when the
engine's Linux VM/host has < ~8GB RAM.

## macOS (Apple Silicon or Intel)

Any docker-compatible engine works; pick by situation:

- **Colima** — free, open source, scriptable. Best default when you don't know the licensing
  situation. **Its VM defaults to 2 CPU / 2GB RAM, which OOMs the Rust link** — always start it
  with more:
  ```bash
  brew install colima docker
  colima start --cpu 4 --memory 8 --disk 100
  docker info    # should now succeed
  ```
- **OrbStack** — fastest, lowest-friction "just works" on macOS; drop-in `docker` CLI. Free tier
  is **personal use only** (commercial needs a paid seat). Good on a personal machine.
- **Docker Desktop** — most polished, but free only for personal / education / non-commercial /
  small business (**< 250 employees AND < $10M revenue**); a larger org needs a paid plan. Don't
  assume it's license-free.

Arch: on Apple Silicon `docker build` produces a native **arm64** image and pred compiles arm64
fine — **no `--platform` needed** as long as you build and run on the same Mac. Only force
`--platform=linux/amd64` if you must run the image on an amd64 host, and expect the emulated Rust
build to be extremely slow — prefer building on an amd64 machine instead.

Bind mounts + `--env-file` work natively and files come back owned by you (the VM maps your UID).

## Linux

- **Docker (rootful)** — frictionless when you have it / have root. `make` targets work verbatim.
  Caveat: the container runs as root, so `out/submission.json` is **root-owned** on the host.
- **Podman (rootless)** — the drop-in when you lack root or a daemon. Two rootless gotchas the
  detect script already encodes into `RUN_FLAGS`:
  - **`--userns=keep-id`** so container-root maps to *your* UID and `out/` is writable + owned by
    you (without it, files land as a high subuid you can't delete).
  - **`:z`** on the bind mount **only when SELinux is enforcing** (RHEL/Fedora) — relabels it so
    the container can write; omit on non-SELinux hosts.
  The Makefile hardcodes `docker`, so either `alias docker=podman` for the session or run the raw
  `podman build` / `podman run` commands.

Build OOM: ensure the host (or its cgroup limit) has ≥8GB + swap; `CARGO_BUILD_JOBS=1` is already
set in the Dockerfile.

## Windows

Not supported by this skill. Docker Desktop on Windows itself runs on WSL2, so if Windows is a
guest VM without nested virtualization it won't start at all. Use one of:
- **WSL2** with a Linux distro — then follow the Linux section *inside* WSL (keep the repo on the
  Linux filesystem, not `/mnt/c`).
- A **remote engine**: point `DOCKER_HOST` / `docker context` at a real Linux box that does the
  build+run.
- A **Linux host** directly (any workstation or a small cloud VM — the benchmark is network-bound,
  not compute-heavy, so nothing fancy is needed).
