"""Exact, price-based token estimates; these are not provider billing records.

No network access or implicit rates. Cache categories follow each provider's
reported usage semantics. Missing evidence never becomes a zero-dollar charge.
"""
from __future__ import annotations

from collections import Counter
from datetime import date
from decimal import Decimal, Inexact, InvalidOperation, localcontext
import re
from urllib.parse import urlsplit

from .io import digest


BASIS = "reported token usage at the supplied frozen USD price schedule"
EXCLUSIONS = ["taxes", "credits and discounts", "local verification compute",
              "unreported or unresolved retry charges"]
_TIERS = {"openai": "default", "anthropic": "standard"}
_RATE_KEYS = {"input", "cache_read", "cache_write", "cache_write_1h", "output"}


def _decimal(value, field: str, *, positive=False) -> Decimal:
    if (not isinstance(value, str)
            or not re.fullmatch(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?", value)):
        raise ValueError(f"{field} must be a decimal string")
    try:
        result = Decimal(value)
    except InvalidOperation as exc:
        raise ValueError(f"{field} must be a finite decimal string") from exc
    if not result.is_finite() or result < 0 or (positive and result == 0):
        raise ValueError(f"{field} must be finite and {'positive' if positive else 'nonnegative'}")
    return result


def _count(value, field: str) -> int:
    if type(value) is not int or value < 0:
        raise ValueError(f"invalid_token_count:{field}")
    return value


def validate_pricing(pricing, provider, model) -> None:
    """Validate one pinned provider/model price entry, without fetching prices."""
    if not isinstance(pricing, dict):
        raise ValueError("pricing must be an object")
    if type(pricing.get("schema_version")) is not int or pricing["schema_version"] != 1:
        raise ValueError("pricing schema_version must be 1")
    if provider not in _TIERS or pricing.get("provider") != provider:
        raise ValueError("pricing provider mismatch or unsupported provider")
    if not isinstance(model, str) or not model or pricing.get("model") != model:
        raise ValueError("pricing model mismatch")
    if pricing.get("currency") != "USD":
        raise ValueError("pricing currency must be USD")
    as_of = pricing.get("as_of")
    if not isinstance(as_of, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", as_of):
        raise ValueError("pricing as_of must be YYYY-MM-DD")
    try:
        date.fromisoformat(as_of)
    except ValueError as exc:
        raise ValueError("pricing as_of must be a valid calendar date") from exc
    if pricing.get("service_tier") != _TIERS[provider] or pricing.get("region") != "global":
        raise ValueError("only explicit standard/default global pricing is supported")
    sources = pricing.get("sources")
    if not isinstance(sources, list) or not sources:
        raise ValueError("pricing requires official HTTPS sources")
    roots = ("openai.com",) if provider == "openai" else ("anthropic.com", "claude.com")
    for source in sources:
        if not isinstance(source, str):
            raise ValueError("pricing sources must be official HTTPS URLs")
        url = urlsplit(source)
        host = url.hostname or ""
        if (url.scheme != "https" or url.username or url.password
                or not any(host == root or host.endswith("." + root) for root in roots)):
            raise ValueError("pricing sources must be official HTTPS URLs")
    rates = pricing.get("rates_per_million")
    if (not isinstance(rates, dict) or not {"input", "cache_read", "cache_write", "output"} <= set(rates)
            or set(rates) - _RATE_KEYS):
        raise ValueError("pricing rates must specify input, cache_read, cache_write and output")
    if "cache_write_1h" in rates and provider != "anthropic":
        raise ValueError("cache_write_1h is supported only for Anthropic")
    for key, value in rates.items():
        _decimal(value, f"rates_per_million.{key}")
    long = pricing.get("long_context")
    if long is not None:
        if not isinstance(long, dict) or set(long) != {"threshold_input_tokens", "input_multiplier", "output_multiplier"}:
            raise ValueError("invalid long_context price schedule")
        _count(long["threshold_input_tokens"], "long_context.threshold_input_tokens")
        _decimal(long["input_multiplier"], "long_context.input_multiplier", positive=True)
        _decimal(long["output_multiplier"], "long_context.output_multiplier", positive=True)
    try:
        digest(pricing)
    except (ValueError, TypeError, OverflowError, RecursionError) as exc:
        raise ValueError("pricing must be finite JSON data") from exc


def _money(value: Decimal) -> str:
    if value == 0:
        return "0"
    text = format(value, "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


def _cost(tokens: int, rate: Decimal, multiplier: Decimal) -> Decimal:
    # Precision depends on input digits, not the ambient Decimal context.
    with localcontext() as ctx:
        ctx.prec = len(str(tokens)) + len(rate.as_tuple().digits) + len(multiplier.as_tuple().digits) + 8
        ctx.traps[Inexact] = True
        return Decimal(tokens) * rate * multiplier / Decimal(1_000_000)


def _sum(values: list[Decimal]) -> Decimal:
    if not values:
        return Decimal(0)
    with localcontext() as ctx:
        ctx.prec = max(28, max(v.adjusted() for v in values) - min(v.as_tuple().exponent for v in values)
                       + len(str(len(values))) + 4)
        ctx.traps[Inexact] = True
        return sum(values, Decimal(0))


def _required(usage: dict, key: str, prefix="usage") -> int:
    if key not in usage:
        raise ValueError(f"missing_usage:{prefix}.{key}")
    return _count(usage[key], f"{prefix}.{key}")


def estimate_cost(generation, pricing, *, provider=None, model=None) -> dict:
    """Price recorded usage only; invalid or incomplete evidence is unavailable."""
    result = {"status": "unavailable", "currency": "USD", "total_usd": None,
              "components": {}, "pricing_hash": None, "usage_hash": None,
              "pricing_as_of": None, "basis": BASIS, "exclusions": EXCLUSIONS.copy(),
              "assumptions": [], "reasons": [], "reason": None}

    def unavailable(reason):
        result["reasons"].append(reason)
        result["reason"] = reason
        return result

    if isinstance(generation, dict) and isinstance(generation.get("usage"), dict):
        try:
            result["usage_hash"] = digest(generation["usage"])
        except (ValueError, TypeError, OverflowError, RecursionError):
            return unavailable("invalid_usage_json")
    if pricing is None:
        return unavailable("pricing_not_configured")
    if not isinstance(pricing, dict):
        return unavailable("invalid_pricing:pricing must be an object")
    provider = provider if provider is not None else pricing.get("provider")
    model = model if model is not None else pricing.get("model")
    try:
        validate_pricing(pricing, provider, model)
    except ValueError as exc:
        return unavailable(f"invalid_pricing:{exc}")
    result.update(pricing_hash=digest(pricing), pricing_as_of=pricing["as_of"], provider=provider, model=model,
                  service_tier=pricing["service_tier"], region=pricing["region"])
    if generation is None:
        return unavailable("generation_not_recorded")
    if not isinstance(generation, dict):
        return unavailable("invalid_generation")
    if generation.get("provider") is not None and generation["provider"] != provider:
        return unavailable("generation_provider_mismatch")
    response = generation.get("provider_response")
    response = response if isinstance(response, dict) else {}
    for label, reported in (("returned_model", generation.get("returned_model")),
                            ("model_snapshot", generation.get("model_snapshot")),
                            ("provider_response.model", response.get("model"))):
        if reported is not None and reported != model:
            return unavailable(f"model_mismatch:{label}")
    if generation.get("returned_model") is None and response.get("model") is None:
        result["assumptions"].append("returned_model_not_reported_using_pricing_model")
    usage = generation.get("usage")
    if not isinstance(usage, dict):
        return unavailable("missing_usage")
    tiers = [("provider_response.service_tier", response.get("service_tier")),
             ("usage.service_tier", usage.get("service_tier")),
             ("generation.service_tier", generation.get("service_tier"))]
    reported_tiers = [(label, value) for label, value in tiers if value is not None]
    for label, tier in reported_tiers:
        if tier != pricing["service_tier"]:
            return unavailable(f"unsupported_service_tier:{label}:{tier}")
    if not reported_tiers:
        result["assumptions"].append("service_tier_not_reported_using_explicit_pricing_tier")
    regions = [(f"{label}.{key}", obj.get(key)) for label, obj in
               (("generation", generation), ("provider_response", response), ("usage", usage))
               for key in ("region", "inference_geo") if obj.get(key) is not None]
    for label, region in regions:
        if region != "global":
            return unavailable(f"unsupported_region:{label}:{region}")
    if not regions:
        result["assumptions"].append("region_not_reported_using_explicit_global_pricing")
    try:
        input_tokens = _required(usage, "input_tokens")
        output_tokens = _required(usage, "output_tokens")
        if provider == "openai":
            details = usage.get("input_tokens_details")
            if not isinstance(details, dict):
                raise ValueError("missing_usage:usage.input_tokens_details")
            read = _required(details, "cached_tokens", "usage.input_tokens_details")
            write = _required(details, "cache_write_tokens", "usage.input_tokens_details")
            ordinary = input_tokens - read - write
            if ordinary < 0:
                raise ValueError("cache_tokens_exceed_total_input")
            tokens = {"input": ordinary, "cache_read": read, "cache_write": write, "output": output_tokens}
            total_input = input_tokens
        else:
            read = _required(usage, "cache_read_input_tokens")
            write = _required(usage, "cache_creation_input_tokens")
            ttl = usage.get("cache_creation")
            if ttl is None and write == 0:
                five, hour = 0, 0
            else:
                if not isinstance(ttl, dict):
                    raise ValueError("missing_usage:usage.cache_creation")
                five = _required(ttl, "ephemeral_5m_input_tokens", "usage.cache_creation")
                hour = _required(ttl, "ephemeral_1h_input_tokens", "usage.cache_creation")
                if five + hour != write:
                    raise ValueError("cache_creation_ttl_total_mismatch")
            if hour and "cache_write_1h" not in pricing["rates_per_million"]:
                raise ValueError("missing_pricing:cache_write_1h")
            tokens = {"input": input_tokens, "cache_read": read, "cache_write": five, "output": output_tokens}
            if "cache_write_1h" in pricing["rates_per_million"] or hour:
                tokens["cache_write_1h"] = hour
            total_input = input_tokens + read + write
    except ValueError as exc:
        return unavailable(str(exc))
    long = pricing.get("long_context")
    premium = long is not None and total_input > long["threshold_input_tokens"]
    components, values = {}, []
    for key, count in tokens.items():
        rate = _decimal(pricing["rates_per_million"][key], key)
        factor = long["output_multiplier" if key == "output" else "input_multiplier"] if premium else "1"
        cost = _cost(count, rate, Decimal(factor))
        components[key] = {"tokens": count, "rate_per_million": pricing["rates_per_million"][key],
                           "multiplier": factor, "cost_usd": _money(cost)}
        values.append(cost)
    result.update(status="estimated", total_usd=_money(_sum(values)), components=components,
                  input_total_tokens=total_input, long_context_applied=premium,
                  long_context_threshold_input_tokens=long["threshold_input_tokens"] if long else None)
    return result


def summarize_costs(records, pricing, *, provider=None, model=None) -> dict:
    """Aggregate scheduled attempts, including failed attempts with priced usage.

    A full scheduled total is unavailable while any generation is absent or any
    recorded generation cannot be priced. The observed sum is explicitly partial.
    """
    rows = list(records)
    generated = [r for r in rows if isinstance(r, dict) and r.get("generation") is not None]
    estimates = [estimate_cost(r["generation"], pricing,
                               provider=r.get("provider") if r.get("provider") is not None else provider,
                               model=r.get("model_snapshot") if r.get("model_snapshot") is not None
                               else r.get("model") if r.get("model") is not None else model)
                 for r in generated]
    pricing_metadata = estimate_cost(None, pricing, provider=provider, model=model)
    priced = [r for r in estimates if r["status"] == "estimated"]
    amounts = [Decimal(r["total_usd"]) for r in priced]
    observed = _money(_sum(amounts)) if priced else None
    not_generated = len(rows) - len(generated)
    unavailable = len(generated) - len(priced)
    complete = bool(rows) and not not_generated and not unavailable
    reasons = Counter(reason for r in estimates for reason in r["reasons"])
    if not_generated:
        reasons["generation_not_recorded"] += not_generated
    return {"status": "estimated" if complete else "unavailable", "currency": "USD", "basis": BASIS,
            "scheduled_attempts": len(rows), "generated_attempts": len(generated),
            "not_generated_attempts": not_generated, "priced_attempts": len(priced),
            "unavailable_generated_attempts": unavailable, "complete": complete,
            "total_usd": observed if complete else None,
            "generated_total_usd": observed if generated and not unavailable else None,
            "observed_sum_usd": observed, "observed_sum_scope": "priced_generation_records_only",
            "reason_counts": dict(sorted(reasons.items())),
            "assumption_counts": dict(sorted(Counter(a for r in estimates for a in r["assumptions"]).items())),
            "pricing_hash": pricing_metadata["pricing_hash"],
            "pricing_as_of": pricing_metadata["pricing_as_of"],
            "exclusions": EXCLUSIONS.copy()}
