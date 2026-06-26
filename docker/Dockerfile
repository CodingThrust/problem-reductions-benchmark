# syntax=docker/dockerfile:1
#
# Reproduction = verification environment for the problem-reductions bug-finding
# benchmark: builds `pred` from problem-reductions at a pinned tag and bundles the
# independent certificate verifier. The same image both reproduces a run and
# re-verifies submitted certificates (zero trust — see benchmark/verify.py).
#
# Build (from repo root):
#   docker build -f docker/Dockerfile -t problem-reductions-benchmark:v0.6.0 .
# Smoke test (runs the verifier calibration against fixtures, needs pred):
#   docker run --rm problem-reductions-benchmark:v0.6.0
# Verify a certificate:
#   docker run --rm -i problem-reductions-benchmark:v0.6.0 - < cert.json   # (see entrypoint)

ARG PR_REF=v0.6.0

# ---- Stage 1: compile pred from the pinned problem-reductions tag ----
FROM rust:1-slim-bookworm AS pred-build
# Toolchain layer is independent of the ref — keep it before ARG so a ref bump
# reuses the cached apt install. git + ca-certificates to clone; build-essential
# (g++/make) + cmake for the bundled HiGHS C++ solver; clang/libclang for bindgen.
RUN apt-get update && apt-get install -y --no-install-recommends \
    git ca-certificates build-essential cmake clang libclang-dev pkg-config \
    && rm -rf /var/lib/apt/lists/*
ARG PR_REF
WORKDIR /src
RUN git clone --depth 1 --branch "${PR_REF}" \
    https://github.com/CodingThrust/problem-reductions.git .
# The upstream release profile uses LTO + codegen-units=1, which OOMs in a
# constrained Docker VM. Relax it for the build (pred's solve perf is ample
# without LTO) and cap parallel rustc jobs to keep peak memory low.
RUN CARGO_PROFILE_RELEASE_LTO=off \
    CARGO_PROFILE_RELEASE_CODEGEN_UNITS=16 \
    CARGO_PROFILE_RELEASE_OPT_LEVEL=2 \
    CARGO_BUILD_JOBS=1 \
    cargo build --release -p problemreductions-cli
# → /src/target/release/pred

# ---- Stage 2: runtime — pred + the stdlib-only verifier ----
FROM python:3.12-slim-bookworm AS runtime
# Runtime libs pred may dynamically link (HiGHS uses OpenMP / libstdc++) — ref-independent,
# so keep before ARG to preserve the cache across ref bumps.
RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 libstdc++6 \
    && rm -rf /var/lib/apt/lists/*
ARG PR_REF
LABEL org.opencontainers.image.title="problem-reductions-benchmark" \
      org.opencontainers.image.description="pred (problem-reductions ${PR_REF}) + zero-trust certificate verifier" \
      org.opencontainers.image.licenses="MIT" \
      problem_reductions_ref="${PR_REF}"

COPY --from=pred-build /src/target/release/pred /usr/local/bin/pred
ENV PRED_BINARY=pred PYTHONUNBUFFERED=1

WORKDIR /app
COPY benchmark/ ./benchmark/

# Fail the build if pred can't run (catches a missing runtime lib early).
RUN pred --version

# Default: run the verifier calibration (3 fixtures) as a self-test.
# Override CMD to verify a certificate, e.g.:
#   docker run --rm -v "$PWD:/w" problem-reductions-benchmark:v0.6.0 /w/cert.json
ENTRYPOINT ["python", "-m", "benchmark.verify"]
CMD ["--calibrate"]

# ---- Stage 3: runner — the budgeted bug-finding agent → submission.json ----
# Adds the agent stack (mini-swe-agent + litellm) and the problem-reductions *source*
# the agent reads (rule files), on top of the runtime image (pred + benchmark code).
#
# Build:   docker build -f docker/Dockerfile --target runner -t problem-reductions-runner:v0.6.0 .
# Run (API key passed at run time, never baked in):
#   docker run --rm \
#     -e MODEL_NAME=anthropic/claude-sonnet-4-6 \
#     -e ANTHROPIC_API_KEY=$ANTHROPIC_API_KEY \
#     -e BUDGET_USD=20 \
#     -v "$PWD/out:/out" \
#     problem-reductions-runner:v0.6.0
#   # → ./out/submission.json
FROM runtime AS runner
ARG PR_REF
# Agent stack. requirements.txt pins mini-swe-agent (which pulls litellm) + anthropic.
RUN pip install --no-cache-dir -r /app/benchmark/requirements.txt
# The agent reads rule sources from REPO_DIR/src/rules — copy just the source tree
# (not target/) from the build stage. pred itself is a self-contained binary on PATH.
COPY --from=pred-build /src/src /app/pr-src/src
COPY --from=pred-build /src/Cargo.toml /app/pr-src/Cargo.toml
RUN mkdir -p /out
ENV REPO_DIR=/app/pr-src \
    BUDGET_USD=20 \
    PER_RULE_BUDGET=0.5 \
    OUTPUT=/out/submission.json
VOLUME ["/out"]
# Smoke-check the runner wiring at build time (no API/pred calls).
RUN python -m benchmark.run_submission --fake --model fake/smoke --max-rules 1 \
    --output /tmp/smoke.json && rm -f /tmp/smoke.json
ENTRYPOINT ["python", "-m", "benchmark.run_submission"]
CMD []
