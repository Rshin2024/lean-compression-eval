#!/bin/sh
set -eu
cd /opt/lce-om100
while [ ! -f final-build.exit ]; do sleep 3; done
test "$(cat final-build.exit)" = 0
image=$(cat runtime-image-id.txt)
docker run --rm --network=none --read-only --cpus=4 --memory=16g \
  -v /opt/lce-om100/verify_runtime.py:/verify_runtime.py:ro \
  "$image" python3 /verify_runtime.py > runtime-verification.json
docker run --rm --network=none --read-only --cpus=4 --memory=16g \
  --tmpfs /job:rw,size=128m \
  -v /opt/lce-om100/Readiness.lean:/Readiness.lean:ro \
  "$image" sh -c 'export LEAN_PATH="$(cat /opt/eval/lean-path)"; exec "$(cat /opt/eval/lean-binary)" --run /Readiness.lean' \
  > runtime-readiness.log 2>&1
grep -q 'OM100 imported environment ready' runtime-readiness.log
docker run --rm --network=none --read-only "$image" \
  cat /opt/eval/build-environment.json > environment.native.json
printf '0\n' > runtime-verification.exit
