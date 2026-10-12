"""Build a Linux runtime and collect environment hashes inside that exact image.

Run from the project root: python3 runtime/prepare_image.py --output configs/environment.docker.json
Requires a running Docker daemon. No candidate proofs or model calls are executed.
"""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from lean_compression.integrity import is_content_digest
from lean_compression.io import digest, read_json, write_json

p = argparse.ArgumentParser()
p.add_argument("--output", type=Path, required=True)
p.add_argument("--image-id", help="Reuse an already built content-addressed image")
args = p.parse_args()
if not args.image_id:
    with tempfile.TemporaryDirectory(prefix="compression-build-") as temp:
        idfile = Path(temp) / "image.id"
        subprocess.run(["docker", "build", "--iidfile", str(idfile), "-f", str(ROOT / "runtime/Dockerfile"), str(ROOT)], check=True)
        image = idfile.read_text().strip()
else:
    image = args.image_id
if not is_content_digest(image):
    raise SystemExit("Use an immutable content digest: sha256: and 64 lowercase hex digits, optionally after name@")
raw = subprocess.check_output(["docker", "run", "--rm", "--network", "none", "--read-only", image,
                               "cat", "/opt/eval/build-environment.json"], text=True)
env = json.loads(raw)
audit = subprocess.check_output([
    "docker", "run", "--rm", "--network", "none", "--read-only", image, "python3", "-c",
    "import json,pathlib; p=pathlib.Path('/opt/eval/package'); print(json.dumps({'lean_sources':len(list(p.rglob('*.lean'))),'git_directories':len(list(p.rglob('.git')))}))"
], text=True)
audit = json.loads(audit)
if audit != {"lean_sources": 0, "git_directories": 0}:
    raise SystemExit("Runtime source-exclusion audit failed")
env.update(runtime_backend="docker", docker_image=image, source_exclusion_audit=audit)
env.pop("environment_hash", None)
env["environment_hash"] = digest(env)
write_json(args.output, env)
print(json.dumps({"docker_image": image, "environment_hash": env["environment_hash"], "output": str(args.output)}, indent=2))
