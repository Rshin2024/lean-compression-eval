"""Review or apply only the six individually hash-bound terminal-error releases."""
import argparse
import fcntl
import json
from pathlib import Path
import release_exact_api_error as release

HERE = Path(__file__).resolve().parent


def execute(batch_sha, apply=False):
    path = HERE / "batch-proposal.json"
    if release.file_hash(path) != batch_sha:
        raise ValueError("Reviewed exact-six proposal hash differs")
    batch = release.read_json(path)
    if (batch.get("helper_sha256") != release.file_hash(Path(release.__file__)) or
            batch.get("batch_helper_sha256") != release.file_hash(Path(__file__))):
        raise ValueError("Reviewed release source differs")
    entries = batch.get("attempts", [])
    if len(entries) != 6 or {e.get("attempt_id") for e in entries} != set(release.ATTEMPTS):
        raise ValueError("The proposal must contain exactly the six reviewed Sonnet attempts")
    bound = []
    for entry in entries:
        proposal = (HERE / entry["proposal_path"]).resolve()
        if not proposal.is_relative_to(HERE):
            raise ValueError("Individual proposal path escaped the batch directory")
        if release.file_hash(proposal) != entry["proposal_sha256"]:
            raise ValueError("Individual proposal hash differs")
        if release.read_json(proposal)["attempt_id"] != entry["attempt_id"]:
            raise ValueError("Individual proposal names a different attempt")
        bound.append((proposal, entry["proposal_sha256"]))
    # Review every independent original claim/evidence before releasing any.
    reviewed = [release.execute(p, sha, False) for p, sha in bound]
    if not apply:
        return {"state": "reviewed_exact_six", "results": reviewed, "new_model_calls": 0}
    return {"state": "exact_six_release_processed", "results": [release.execute(p, sha, True) for p, sha in bound],
            "new_model_calls": 0, "controller_resume_requested": False}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--proposal-sha256", required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    with (HERE / "release.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        print(json.dumps(execute(args.proposal_sha256, args.apply), indent=2))
