#!/usr/bin/env python3
"""Live smoke test for the discovery LLM providers (manual runs only).

Calls the real OpenRouter and NVIDIA direct APIs with the repo's own
NemotronClient and reports PASS/FAIL per check. This is the only script
that touches live provider keys; it never reads or writes repo state
(no sources.json, no feeds, no publication) and burns at most a
handful of tiny requests.

Checks (one per workflow step, so each gets its own env):
  nvidia-direct   Every mapped NVIDIA-direct model ID answers on
                  integrate.api.nvidia.com. Proves the IDs in
                  OPENROUTER_TO_NVIDIA_DIRECT_MODELS exist and the
                  NVIDIA key works. Runs with only NVIDIA_API_KEY set,
                  so a failure cannot hide behind the OpenRouter
                  fallback.
  cross-provider  The OpenRouter primary is forced to fail (the workflow
                  passes a deliberately invalid OPENROUTER_API_KEY, so
                  every OpenRouter attempt 401s) and the call must still
                  succeed via the NVIDIA-direct fallback with a mapped
                  model. This is the exact code path run 43696446561
                  needed. The script also asserts the client's own
                  "trying NVIDIA direct" log line fired, so the result
                  is proven to have come from the fallback, not the
                  primary.
  openrouter      (informational) The normal OpenRouter primary call
                  with the real key. Reported, but never affects the
                  exit code: if OpenRouter is down, the cross-provider
                  check passing is the good news.

Usage:
  python3 scripts/smoke_test_providers.py --check nvidia-direct
  python3 scripts/smoke_test_providers.py --check cross-provider
  python3 scripts/smoke_test_providers.py --check openrouter
Exit code 0 when every gating check passes; 1 otherwise. The
openrouter check always exits 0 (informational).
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.discovery.nemotron_client import (  # noqa: E402
    DEFAULT_NEMOTRON_MODEL,
    OPENROUTER_TO_NVIDIA_DIRECT_MODELS,
    NemotronClient,
)

PROMPT = "Reply with exactly the word OK and nothing else."
# The mapped Nemotron IDs the OpenRouter -> NVIDIA direct fallback
# actually uses (step-5-preview is the NVIDIA-primary default, not a
# fallback model, so it is not probed here).
NVIDIA_DIRECT_MODELS = [
    model for model in dict.fromkeys(OPENROUTER_TO_NVIDIA_DIRECT_MODELS.values())
    if model != DEFAULT_NEMOTRON_MODEL
]


def _answered_ok(client: NemotronClient) -> bool:
    """One tiny call; True only when the model answers with OK."""
    try:
        text = client.complete(PROMPT)
    except Exception as exc:  # noqa: BLE001 - report the type, never the value
        print(f"  error: {type(exc).__name__}")
        return False
    return bool(text) and "OK" in text.upper()


def _check_nvidia_direct() -> bool:
    """Every mapped NVIDIA-direct model ID answers on NVIDIA direct."""
    # Drop any OpenRouter key from this process so a failure cannot
    # hide behind the OpenRouter fallback: a pass here means NVIDIA
    # direct answered.
    os.environ.pop("OPENROUTER_API_KEY", None)
    if not os.environ.get("NVIDIA_API_KEY", "").strip():
        print("FAIL: NVIDIA_API_KEY is not set")
        return False
    ok = True
    for model in NVIDIA_DIRECT_MODELS:
        print(f"nvidia-direct {model} ... ", end="", flush=True)
        client = NemotronClient(provider="nvidia", model=model)
        client.configure_cycle(max_attempts=10, deadline=time.monotonic() + 300)
        passed = _answered_ok(client)
        print("PASS" if passed else "FAIL")
        ok = ok and passed
    return ok


def _check_cross_provider() -> bool:
    """An OpenRouter failure crosses to NVIDIA direct and succeeds."""
    messages: list[str] = []

    class _Capture(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            messages.append(record.getMessage())

    logger = logging.getLogger("src.discovery.nemotron_client")
    handler = _Capture()
    logger.addHandler(handler)
    try:
        print("cross-provider (invalid OpenRouter key) ... ", end="", flush=True)
        client = NemotronClient()
        client.configure_cycle(max_attempts=20, deadline=time.monotonic() + 300)
        passed = _answered_ok(client)
    finally:
        logger.removeHandler(handler)
    crossed = any("trying NVIDIA direct with mapped models" in m for m in messages)
    print("PASS" if passed and crossed else "FAIL")
    if passed and not crossed:
        print("  note: call succeeded but the NVIDIA-direct fallback log line never fired")
    return passed and crossed


def _check_openrouter() -> bool:
    """Informational: is the normal OpenRouter primary healthy?"""
    if not os.environ.get("OPENROUTER_API_KEY", "").strip():
        print("openrouter: SKIP (OPENROUTER_API_KEY is not set)")
        return True
    print("openrouter primary ... ", end="", flush=True)
    client = NemotronClient()
    client.configure_cycle(max_attempts=10, deadline=time.monotonic() + 300)
    passed = _answered_ok(client)
    print("PASS" if passed else "FAIL")
    return passed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", required=True,
                        choices=("nvidia-direct", "cross-provider", "openrouter"))
    args = parser.parse_args(argv)
    if args.check == "nvidia-direct":
        return 0 if _check_nvidia_direct() else 1
    if args.check == "cross-provider":
        return 0 if _check_cross_provider() else 1
    _check_openrouter()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
