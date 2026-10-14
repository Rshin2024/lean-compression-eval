"""Build a distinct immutable runtime; the only filesystem change is lean -j1."""
import copy
import difflib
import json
from pathlib import Path
import subprocess
import sys

HERE = Path(__file__).resolve().parent
WORK = HERE.parent
sys.path.insert(0, str(WORK))
import materialize as m


def docker_json(*args):
    return json.loads(subprocess.check_output(["docker", *args], text=True))


def main():
    environment = m.load_environment(WORK / "environment.azure.json")
    base_image = environment["docker_image"]
    old = subprocess.check_output(["docker", "run", "--rm", "--network", "none", "--read-only",
                                   "--cap-drop", "ALL", base_image, "cat", "/opt/eval/run-lean"], text=True)
    needle = '"$LEAN_SYSROOT/bin/lean" --run "$@"'
    replacement = '"$LEAN_SYSROOT/bin/lean" -j1 --run "$@"'
    if old.count(needle) != 1:
        raise ValueError("Pinned runtime wrapper does not match the expected single invocation")
    new = old.replace(needle, replacement)
    context = HERE / "image-context"
    context.mkdir(parents=True, exist_ok=True)
    (context / "run-lean").write_text(new)
    (context / "run-lean").chmod(0o755)
    base_tag = "lce-om100-v1-base-pinned:" + base_image.removeprefix("sha256:")
    subprocess.run(["docker", "tag", base_image, base_tag], check=True)
    (context / "Dockerfile").write_text(
        f"FROM {base_tag}\nCOPY --chmod=755 run-lean /opt/eval/run-lean\n"
        'LABEL org.lean-compression.verification-amendment="v2_threads1"\n')
    with (HERE / "build.log").open("w") as log:
        subprocess.run(["docker", "build", "--pull=false", "--progress=plain", "-t", "lce-om100-runtime-v2-threads1",
                        str(context)], stdout=log, stderr=subprocess.STDOUT, check=True)
    previous = docker_json("image", "inspect", base_image)[0]
    current = docker_json("image", "inspect", "lce-om100-runtime-v2-threads1")[0]
    image = current["Id"]
    if current["RootFS"]["Layers"][:-1] != previous["RootFS"]["Layers"]:
        raise ValueError("New image is not the exact old layer chain plus one launcher layer")
    found = subprocess.check_output(["docker", "run", "--rm", "--network", "none", "--read-only",
                                     "--cap-drop", "ALL", image, "cat", "/opt/eval/run-lean"], text=True)
    if found != new:
        raise ValueError("New image launcher bytes differ from the intended change")
    verify_script = WORK / "runtime/verify_runtime.py"
    verified = json.loads(subprocess.check_output([
        "docker", "run", "--rm", "--network", "none", "--read-only", "--cap-drop", "ALL",
        "--mount", f"type=bind,source={verify_script},target=/verify_runtime.py,readonly",
        image, "python3", "/verify_runtime.py"], text=True))
    if verified["status"] != "verified_source_stripped_runtime" or verified["evaluator_python_hash"] != m.evaluator_hash():
        raise ValueError("New runtime did not retain the frozen package/source exclusion")
    delta = {"version": "v2_threads1", "kind": "explicit_lean_thread_limit",
             "source_environment_hash": environment["environment_hash"], "source_docker_image": base_image,
             "source_wrapper_sha256": m.sha256_text(old), "wrapper_sha256": m.sha256_text(new),
             "lean_threads": 1, "validation_budgets_unchanged": True,
             "diff": "".join(difflib.unified_diff(old.splitlines(True), new.splitlines(True), fromfile="v1/run-lean", tofile="v2_threads1/run-lean"))}
    amended = copy.deepcopy(environment)
    amended["docker_image"] = image
    amended["verification_runtime_amendment"] = delta
    amended["environment_hash"] = m.digest({key: value for key, value in amended.items() if key != "environment_hash"})
    config = m.read_json(WORK / "config.reference-final.json")
    config["validation"]["docker_image"] = image
    for name, value in (("environment.json", amended), ("reference-config.json", config),
                        ("runtime-verification.json", verified),
                        ("runtime-build.json", {**delta, "docker_image": image,
                            "verification_environment_hash": amended["environment_hash"],
                            "base_layers": previous["RootFS"]["Layers"], "new_layers": current["RootFS"]["Layers"],
                            "core_hash": m.evaluator_hash(), "model_calls": 0,
                            "official_v2_checks_started": False})):
        path = HERE / name
        if path.exists() and m.read_json(path) != value:
            raise ValueError(f"Refusing to overwrite different prepared runtime metadata: {path}")
        m.write_private(path, m.json_text(value))
    print(m.json_text({"image": image, "environment_hash": amended["environment_hash"],
                       "verified_files": verified["compiled_files_verified"], "diff": delta["diff"]}), flush=True)


if __name__ == "__main__":
    main()
