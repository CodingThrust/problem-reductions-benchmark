#!/usr/bin/env bash
# Read-only host probe for the run-benchmark skill.
# Detects an available container engine and the flags the benchmark run needs on
# this host, then prints a machine-parsable report + a human summary. It NEVER
# mutates the system (no install, no `colima start`, no alias) — installing an
# engine or bumping VM RAM is done by the caller after reading this.
#
# Exit 0 = an engine was found (see ENGINE=...). Exit 1 = none; guidance printed.
#
# Report lines (KEY=VALUE, stable, parse these):
#   ENGINE     docker | podman | none
#   ENGINE_BIN absolute path to the engine binary (empty when none)
#   OS         linux | darwin | other
#   ROOTLESS   true | false | n/a
#   SELINUX    enforcing | permissive | disabled | n/a
#   ARCH       arm64 | amd64 | <raw>
#   RAM_HINT   ok | low | unknown       (build OOMs on <~8GB; Rust link)
#   RUN_FLAGS  the -v / --userns / --env-file flags to pass to `run` (docker/podman only)
#   PROBLEM    (optional) one-line reason the found engine may still not work
set -u

emit() { printf '%s=%s\n' "$1" "$2"; }

# ---- OS + arch ----------------------------------------------------------------
case "$(uname -s 2>/dev/null)" in
  Linux)  OS=linux ;;
  Darwin) OS=darwin ;;
  *)      OS=other ;;
esac
case "$(uname -m 2>/dev/null)" in
  arm64|aarch64) ARCH=arm64 ;;
  x86_64|amd64)  ARCH=amd64 ;;
  *)             ARCH="$(uname -m 2>/dev/null || echo unknown)" ;;
esac

# ---- RAM hint (best effort; the engine VM's RAM is what matters on macOS) ------
RAM_HINT=unknown
if [ "$OS" = linux ] && [ -r /proc/meminfo ]; then
  kb=$(awk '/^MemTotal:/{print $2}' /proc/meminfo 2>/dev/null || echo 0)
  if [ "${kb:-0}" -gt 0 ]; then
    [ "$kb" -ge 7500000 ] && RAM_HINT=ok || RAM_HINT=low
  fi
fi
# On macOS the host has plenty of RAM but the Linux VM (Colima defaults to 2GB!)
# is the real limit and isn't uniformly queryable — leave RAM_HINT=unknown and
# let references/engines.md tell the caller to provision >=8GB.

# ---- engine detection ---------------------------------------------------------
ENGINE=none; ENGINE_BIN=""; ROOTLESS=n/a; SELINUX=n/a; RUN_FLAGS=""; PROBLEM=""

selinux_state() {
  command -v getenforce >/dev/null 2>&1 || { echo disabled; return; }
  case "$(getenforce 2>/dev/null)" in
    Enforcing)  echo enforcing ;;
    Permissive) echo permissive ;;
    *)          echo disabled ;;
  esac
}

if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
  ENGINE=docker; ENGINE_BIN="$(command -v docker)"; ROOTLESS=false; SELINUX=n/a
  # Rootful docker: files in ./out come back root-owned (host may need sudo to move).
  RUN_FLAGS='-v "$PWD/out:/out" --env-file submission.env'
  [ "$OS" = darwin ] && PROBLEM="macOS: ensure the engine VM has >=8GB RAM or the Rust build OOMs (exit 137)"

elif command -v podman >/dev/null 2>&1; then
  ENGINE=podman; ENGINE_BIN="$(command -v podman)"
  if podman info 2>/dev/null | grep -qi 'rootless: *true'; then ROOTLESS=true; else ROOTLESS=false; fi
  SELINUX="$(selinux_state)"
  # rootless needs UID passthrough so ./out is writable + owned by you;
  # add :z only when SELinux is enforcing/permissive (relabels the bind mount).
  vol='-v "$PWD/out:/out"'
  [ "$SELINUX" = enforcing ] || [ "$SELINUX" = permissive ] && vol='-v "$PWD/out:/out:z"'
  if [ "$ROOTLESS" = true ]; then
    RUN_FLAGS="$vol --userns=keep-id --env-file submission.env"
  else
    RUN_FLAGS="$vol --env-file submission.env"
  fi
  PROBLEM="Makefile hardcodes 'docker'; run raw podman commands or 'alias docker=podman' for the session"
fi

# ---- report -------------------------------------------------------------------
emit ENGINE "$ENGINE"
emit ENGINE_BIN "$ENGINE_BIN"
emit OS "$OS"
emit ROOTLESS "$ROOTLESS"
emit SELINUX "$SELINUX"
emit ARCH "$ARCH"
emit RAM_HINT "$RAM_HINT"
emit RUN_FLAGS "$RUN_FLAGS"
[ -n "$PROBLEM" ] && emit PROBLEM "$PROBLEM"

echo
if [ "$ENGINE" = none ]; then
  echo "No container engine found (need docker or podman)."
  case "$OS" in
    darwin) echo "  macOS: install Colima (free) or OrbStack, then provision >=8GB RAM. See references/engines.md." ;;
    linux)  echo "  Linux: install docker, or rootless podman if you lack root. See references/engines.md." ;;
    other)  echo "  This looks like a non-Unix shell (e.g. Windows native). Windows is not supported:" ;
            echo "  use WSL2 (a Linux distro) or a remote/Linux host. See references/engines.md." ;;
  esac
  exit 1
fi

echo "Engine: $ENGINE ($ENGINE_BIN), arch=$ARCH, os=$OS"
[ -n "$PROBLEM" ] && echo "Note: $PROBLEM"
[ "$RAM_HINT" = low ] && echo "Warning: host RAM looks < ~8GB — the Rust build may OOM (exit 137)."
exit 0
