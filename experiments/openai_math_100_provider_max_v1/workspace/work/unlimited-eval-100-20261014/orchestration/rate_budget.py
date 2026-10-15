"""Conservative admission from provider response headers; no API access."""
from datetime import datetime
import re
import threading
import time


def reset_at(value, now):
    if value is None:
        return now + 60
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except ValueError:
        pass
    parts = re.findall(r"([0-9.]+)(ms|s|m|h)", str(value))
    if parts:
        seconds = sum(float(n) * {"ms": .001, "s": 1, "m": 60, "h": 3600}[unit] for n, unit in parts)
        return now + seconds
    try:
        return now + float(value)
    except ValueError:
        return now + 60


class RateBudget:
    def __init__(self):
        self.lock = threading.Lock()
        self.models = {}

    def headers(self, model, provider, headers, sequence, reservation_tokens):
        headers = {k.lower(): v for k, v in headers.items()}
        prefix = "x-ratelimit-" if provider == "openai" else "anthropic-ratelimit-"
        now = time.time()
        with self.lock:
            state = self.models.setdefault(model, {"started": 0, "last_sequence": -1, "resources": {}})
            if sequence < state["last_sequence"]:
                return
            after = max(0, state["started"] - sequence - 1)
            for resource in ("requests", "tokens") if provider == "openai" else ("requests",):
                # OpenAI prefix ordering differs from Anthropic's headers.
                def name(part):
                    return prefix + (part + "-" + resource if provider == "openai" else resource + "-" + part)
                if name("remaining") not in headers:
                    continue
                try:
                    remaining = int(headers[name("remaining")])
                    maximum = int(headers[name("limit")])
                except (ValueError, KeyError):
                    continue
                cost = reservation_tokens if resource == "tokens" else 1
                state["resources"][resource] = {"remaining": max(0, remaining - after * cost), "limit": maximum,
                    "reset_at": reset_at(headers.get(name("reset")), now), "observed_at": now}
            state["last_sequence"] = sequence

    def reserve(self, model, provider, reservation_tokens):
        now = time.time()
        with self.lock:
            state = self.models.setdefault(model, {"started": 0, "last_sequence": -1, "resources": {}})
            resources = state["resources"]
            for kind, item in resources.items():
                if now >= item["reset_at"]:
                    item["remaining"] = item["limit"]
                    item["reset_at"] = now + 60
                need = reservation_tokens if kind == "tokens" else 1
                if item["remaining"] < need:
                    return None
            sequence = state["started"]
            for kind, item in resources.items():
                item["remaining"] -= reservation_tokens if kind == "tokens" else 1
            state["started"] += 1
            return sequence

    def snapshot(self):
        import copy
        with self.lock:
            return copy.deepcopy(self.models)
