#!/bin/sh
set -eu
cd /opt/lce-om100
while [ ! -f build.exit ]; do sleep 5; done
if [ "$(cat build.exit)" != 0 ]; then
    echo 'Private builder failed; refusing final runtime build.'
    exit 1
fi
docker image inspect lce-om100-build --format '{{.Id}}' > builder-image-id.txt
docker run --rm --network=none --read-only \
  -v /opt/lce-om100/verify_builder.py:/verify_builder.py:ro \
  lce-om100-build python3 /verify_builder.py > builder-verification.json
set +e
docker build --progress=plain -f Dockerfile.final \
  --output type=image,name=lce-om100-runtime,compression=uncompressed \
  --build-arg BASE_BUILD_IMAGE=lce-om100-build final-context > final-build.log 2>&1
code=$?
printf '%s\n' "$code" > final-build.exit
if [ "$code" -eq 0 ]; then
  docker image inspect lce-om100-runtime --format '{{.Id}}' > runtime-image-id.txt
fi
exit "$code"
