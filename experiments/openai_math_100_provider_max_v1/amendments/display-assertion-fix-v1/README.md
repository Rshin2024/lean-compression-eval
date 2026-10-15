# Disclosure assertion correction

The combined-report builder counted a disclosure phrase that could already
appear elsewhere in its HTML or embedded data. The correction identifies the
injected script and checks that exact script and id once. It changes no metric,
record or score. `original/build_suite.py` preserves the previous source before
the main source snapshot refresh; `current/build_suite.py` is the fixed source.
`source.patch` and `incident.json` bind the source-only correction. No private
execution logs or report payloads are included.
