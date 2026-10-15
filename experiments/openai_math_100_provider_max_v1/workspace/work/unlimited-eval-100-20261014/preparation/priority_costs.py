"""Versioned pricing extension for delivered OpenAI tiers; frozen core stays unchanged.

Only disposable calculation copies normalize the core's standard-tier field. Raw
responses, generation records, and frozen price evidence are never altered.
"""
from __future__ import annotations
import copy
import threading
from lean_compression import costs
from lean_compression.io import digest

_ORIGINAL = getattr(costs.estimate_cost, "_provider_max_original", costs.estimate_cost)
_LOCK = threading.Lock()
VERSION = "delivered_service_tier_costs_v1"


def estimate_cost(generation, pricing, *, provider=None, model=None):
    provider = provider or (pricing or {}).get("provider")
    model = model or (pricing or {}).get("model")
    extension = pricing.get("pricing_by_delivered_tier") if isinstance(pricing, dict) else None
    if provider != "openai" or extension is None:
        return _ORIGINAL(generation, pricing, provider=provider, model=model)

    def unavailable(reason):
        result = _ORIGINAL(None, pricing, provider=provider, model=model)
        result.update(reason=reason, reasons=[reason], pricing_hash=digest(pricing),
                      cost_extension=VERSION, total_usd=None, status="unavailable")
        if isinstance(generation, dict) and isinstance(generation.get("usage"), dict):
            result["usage_hash"] = digest(generation["usage"])
        return result

    if not isinstance(generation, dict):
        return unavailable("generation_not_recorded")
    response = generation.get("provider_response") or {}
    usage = generation.get("usage") or {}
    observations = [v for v in (response.get("service_tier"), usage.get("service_tier"),
                                generation.get("service_tier")) if v is not None]
    canonical = lambda v: "priority" if v in ("priority", "fast") else v
    if not observations:
        return unavailable("delivered_service_tier_not_reported")
    if any(v not in ("priority", "fast", "default") for v in observations):
        return unavailable("unsupported_delivered_service_tier")
    if len({canonical(v) for v in observations}) != 1:
        return unavailable("conflicting_delivered_service_tiers")
    tier = canonical(observations[0])
    if (extension.get("version") != VERSION or extension.get("missing_delivered_tier") != "unavailable"
            or extension.get("region") != "global"):
        return unavailable("invalid_delivered_tier_extension")
    selected = extension.get("rates_per_million", {}).get(tier)
    if not isinstance(selected, dict):
        return unavailable("delivered_tier_rate_not_frozen")
    calculation_price = copy.deepcopy(pricing)
    calculation_price.pop("pricing_by_delivered_tier", None)
    calculation_price["rates_per_million"] = selected
    calculation_price["service_tier"] = "default"
    calculation_generation = copy.deepcopy(generation)
    for obj in (calculation_generation, calculation_generation.get("provider_response", {}),
                calculation_generation.get("usage", {})):
        if isinstance(obj, dict) and obj.get("service_tier") is not None:
            obj["service_tier"] = "default"
    result = _ORIGINAL(calculation_generation, calculation_price, provider=provider, model=model)
    result.update(pricing_hash=digest(pricing), service_tier=tier,
                  delivered_service_tier=observations[0], cost_extension=VERSION,
                  calculation_price_hash=digest(calculation_price),
                  delivered_rate_schedule_hash=digest(selected))
    if isinstance(generation.get("usage"), dict):
        result["usage_hash"] = digest(generation["usage"])
    return result


price_generation = estimate_cost


def install():
    """Install once before concurrent generation/reporting threads start."""
    with _LOCK:
        if costs.estimate_cost is estimate_cost:
            return
        if costs.estimate_cost is not _ORIGINAL:
            raise RuntimeError("Another cost extension is already installed")
        estimate_cost._provider_max_original = _ORIGINAL
        costs.estimate_cost = estimate_cost

