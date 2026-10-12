# The built image ID must be frozen; this tag is only a build input.
FROM ubuntu:24.04
ARG LEAN_TOOLCHAIN=leanprover/lean4:v4.34.0-rc1
ARG MATHLIB_COMMIT=274ed6d67d9b226c557813b9ce437574cccdce11
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates curl git build-essential zstd python3 \
    && rm -rf /var/lib/apt/lists/*
ENV ELAN_HOME=/opt/elan
ENV PATH=/opt/elan/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
RUN curl -sSfL https://raw.githubusercontent.com/leanprover/elan/v4.2.0/elan-init.sh -o /tmp/elan-init.sh \
    && sh /tmp/elan-init.sh -y --default-toolchain ${LEAN_TOOLCHAIN} \
    && rm /tmp/elan-init.sh
WORKDIR /opt/eval/package
RUN printf '%s\n' "${LEAN_TOOLCHAIN}" > lean-toolchain \
    && printf 'import Lake\nopen Lake DSL\npackage leanCompressionRuntime\nrequire mathlib from git "https://github.com/leanprover-community/mathlib4.git" @ "%s"\n' "${MATHLIB_COMMIT}" > lakefile.lean \
    && lake update && lake exe cache get
RUN mkdir -p /opt/eval && lake env printenv LEAN_PATH > /opt/eval/lean-path \
    && elan which lean > /opt/eval/lean-binary
COPY runtime/run-lean /opt/eval/run-lean
COPY src /opt/eval/source
RUN PYTHONPATH=/opt/eval/source python3 -m lean_compression prepare-environment \
      --package-root /opt/eval/package --output /opt/eval/build-environment.json \
    && find /opt/eval/package -type f -name '*.lean' -delete \
    && find /opt/eval/package -type d -name .git -prune -exec rm -rf '{}' + \
    && test -z "$(find /opt/eval/package -type f -name '*.lean' -print -quit)" \
    && test -z "$(find /opt/eval/package -type d -name .git -print -quit)"
RUN chmod 755 /opt/eval/run-lean
LABEL org.lean-compression.toolchain="leanprover/lean4:v4.34.0-rc1"
LABEL org.lean-compression.mathlib="274ed6d67d9b226c557813b9ce437574cccdce11"
LABEL org.lean-compression.source-exclusion="mathlib-and-package-Lean-sources-and-git-removed-after-hashing"
WORKDIR /job
