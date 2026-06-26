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
ARG PR_REF
# git + ca-certificates to clone; build-essential (g++/make) + cmake to compile
# the bundled HiGHS C++ solver; clang/libclang for bindgen's FFI generation.
RUN apt-get update && apt-get install -y --no-install-recommends \
    git ca-certificates build-essential cmake clang libclang-dev pkg-config \
    && rm -rf /var/lib/apt/lists/*
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
ARG PR_REF
LABEL org.opencontainers.image.title="problem-reductions-benchmark" \
      org.opencontainers.image.description="pred (problem-reductions ${PR_REF}) + zero-trust certificate verifier" \
      org.opencontainers.image.licenses="MIT" \
      problem_reductions_ref="${PR_REF}"

# Runtime libs pred may dynamically link (HiGHS solver uses OpenMP / libstdc++).
RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 libstdc++6 \
    && rm -rf /var/lib/apt/lists/*

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
