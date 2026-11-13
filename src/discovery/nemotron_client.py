from __future__ import annotations

import base64
import hashlib
import json
import logging
import math
import os
import re
import time
from typing import Any, Optional

from src.discovery.state_manager import approved_public_url, bounded_image_results
from src.network import NetworkError, fetch_bytes
from src.models import SourceConfig
from src.parsers import extract_nodes_from_content

logger = logging.getLogger(__name__)

TRACE_MAX_STR = 4096
TRACE_MAX_RESPONSE_TEXT = 16384


def _write_trace_line(path: str, record: dict) -> None:
    """Append one JSONL trace record locally. Caller guarantees failures never escape."""
    payload = json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n"
    flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND
    fd = os.open(path, flags, 0o600)
    try:
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
        os.write(fd, payload.encode("utf-8"))
    finally:
        os.close(fd)


def _network_error_token(exc: NetworkError) -> str:
    """Fixed-token descriptor for logs: class names and errno ints only, never message text."""
    def part(e: BaseException) -> str:
        code = getattr(e, "errno", None)
        return f"{type(e).__name__}:{code}" if isinstance(code, int) else type(e).__name__
    token = part(exc)
    cause = exc.__cause__
    if cause is not None:
        token += ">" + part(cause)
    return token


def _redact_for_trace(value: Any) -> Any:
    """Bound every field so a trace line cannot carry unbounded payloads or credentials."""
    if isinstance(value, dict):
        return {key: _redact_for_trace(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_redact_for_trace(item) for item in value]
    if isinstance(value, str):
        if len(value) > TRACE_MAX_STR:
            digest = hashlib.sha256(value.encode("utf-8", "replace")).hexdigest()
            return f"<truncated {len(value)} chars sha256={digest}>"
        return value
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return f"<{type(value).__name__}>"

DEFAULT_NVIDIA_BASE_URL = "https://integrate.api.nvidia.com/v1"
DEFAULT_OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
DEFAULT_NEMOTRON_MODEL = "stepfun-ai/step-5-preview"
# OpenRouter is OpenAI-compatible at the same /chat/completions path.
# Default OpenRouter model, picked 2026-11-13 by the deterministic daily
# selector (workspace scripts/select_openrouter_model.py; code ranks, the
# AI only reviews the pick): NVIDIA Nemotron 3.5 Super VL (free) — $0 per
# token, 1M context, text+image+video input, structured outputs, and
# verified answering plain API calls 7/7 days (independent daily tracker).
# It covers all four discovery jobs (text extraction, judging, queries,
# vision/OCR) in one model. Free-tier limits: 20 req/min, 1,000 req/day
# on this account ($10+ credits bought). stepfun-ai/step-5-preview is
# NVIDIA-only and is never sent to OpenRouter.
DEFAULT_OPENROUTER_MODEL = "nvidia/nemotron-3.5-super-vl:free"
# OpenRouter's free router: picks an available free model for the request
# (and filters for features the request needs, e.g. image understanding).
# It is the backup model for both text and vision calls. It is NOT gated
# the way thinkingmachines/* :free models are (agentic harnesses only).
OPENROUTER_FREE_ROUTER = "openrouter/free"
APPROVED_BASE_URLS = {
    "nvidia": DEFAULT_NVIDIA_BASE_URL,
    "openrouter": DEFAULT_OPENROUTER_BASE_URL,
}
VISION_FALLBACK_MODELS = [
    OPENROUTER_FREE_ROUTER,
]
TEXT_FALLBACK_MODELS = [
    OPENROUTER_FREE_ROUTER,
]


def _download_image_as_data_uri(image_url: str, timeout: float = 12, deadline: float | None = None) -> str:
    """Only checked, complete image bytes may be sent to NIM, never a remote fallback."""
    try:
        image_url = approved_public_url(image_url)
        if not image_url.startswith(("http://", "https://")):
            return ""
        status, raw = fetch_bytes(image_url, timeout=timeout, max_bytes=2 * 1024 * 1024,
                                  deadline=deadline, headers={"User-Agent": "NavaRoute-Discovery"})
        if not 200 <= status < 300:
            return ""
        if raw.startswith(b"\x89PNG\r\n\x1a\n"):
            mime = "image/png"
        elif raw.startswith(b"\xff\xd8\xff"):
            mime = "image/jpeg"
        elif raw.startswith(b"RIFF") and b"WEBP" in raw[:16]:
            mime = "image/webp"
        else:
            return ""

        b64 = base64.b64encode(raw).decode("ascii")
        return f"data:{mime};base64,{b64}"
    except (NetworkError, ValueError):
        return ""


class DiscoveryBudgetExceeded(RuntimeError):
    """Normal cycle stop; callers checkpoint unfinished work, not a failed source."""


class DiscoveryAPIError(RuntimeError):
    """All permitted API attempts failed; CLI/workflow callers must report failure."""


class NemotronClient:
    """Client for NVIDIA NIM / OpenRouter LLM API (OpenAI-compatible).

    Provider selection, in order: explicit ``provider`` argument, the
    ``DISCOVERY_PROVIDER`` env var (``nvidia`` / ``openrouter`` / ``auto``),
    otherwise ``auto`` — OpenRouter is the default when its key is present,
    otherwise NVIDIA. When both keys are present the other provider is kept
    as an automatic fallback: if the primary provider fails (transport,
    503 BrokenPipe, or a 404 for a model), _call_api retries once on the
    fallback provider before giving up. No other origin is ever accepted.
    From OpenRouter there is no cross-provider fallback: its model list
    already ends at the free router, and no NVIDIA model currently works.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
        base_url: Optional[str] = None,
        timeout: int = 45,
        min_request_interval: float = 0.0,
        provider: Optional[str] = None,
    ) -> None:
        requested = (provider or os.environ.get("DISCOVERY_PROVIDER", "")).strip().lower()
        if requested not in ("", "auto", "nvidia", "openrouter"):
            raise ValueError("Invalid discovery provider")
        nvidia_key = os.environ.get("NVIDIA_API_KEY", "").strip()
        openrouter_key = os.environ.get("OPENROUTER_API_KEY", "").strip()
        if requested in ("nvidia", "openrouter"):
            self.provider = requested
        elif openrouter_key:
            self.provider = "openrouter"
        else:
            self.provider = "nvidia"
        self.fallback_provider = "openrouter" if self.provider == "nvidia" else "nvidia"
        self._provider_keys = {"nvidia": nvidia_key, "openrouter": openrouter_key}
        self.api_key = api_key if api_key is not None else self._provider_keys[self.provider]
        self.fallback_api_key = self._provider_keys[self.fallback_provider]
        if self.provider == "openrouter":
            default_model = os.environ.get("OPENROUTER_MODEL", DEFAULT_OPENROUTER_MODEL).strip() or DEFAULT_OPENROUTER_MODEL
            default_base = os.environ.get("OPENROUTER_BASE_URL", DEFAULT_OPENROUTER_BASE_URL).rstrip("/") or DEFAULT_OPENROUTER_BASE_URL
        else:
            default_model = os.environ.get("NVIDIA_MODEL", DEFAULT_NEMOTRON_MODEL).strip() or DEFAULT_NEMOTRON_MODEL
            default_base = os.environ.get("NVIDIA_BASE_URL", DEFAULT_NVIDIA_BASE_URL).rstrip("/") or DEFAULT_NVIDIA_BASE_URL
        # An explicit model stays explicit, even across a provider fallback;
        # the fallback then only runs if that model is in its fallback list.
        self.model = model if model is not None else default_model
        self.base_url = base_url.rstrip("/") if base_url is not None else default_base
        self.fallback_base_url = APPROVED_BASE_URLS[self.fallback_provider]
        if self.base_url not in APPROVED_BASE_URLS.values() or self.base_url != APPROVED_BASE_URLS[self.provider]:
            raise ValueError("Unapproved discovery API origin")
        self.timeout = timeout
        self.min_request_interval = min_request_interval
        if (isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or timeout <= 0
                or isinstance(min_request_interval, bool) or not isinstance(min_request_interval, (int, float)) or not math.isfinite(min_request_interval) or min_request_interval < 0):
            raise ValueError("Invalid NVIDIA request timing")
        self.api_requests_count = 0
        self._last_request_ts: float | None = None
        self._cycle_start_requests = 0
        self._max_attempts = 500
        self.deadline: float | None = None
        self.image_results: dict[str, str] = {}
        self.last_image_outcome = "retryable"

    def configure_cycle(self, *, max_attempts: int, deadline: float) -> None:
        if type(max_attempts) is not int or max_attempts <= 0 or not isinstance(deadline, (int, float)) or not math.isfinite(deadline):
            raise ValueError("Invalid discovery budget")
        self._max_attempts = max_attempts
        self._cycle_start_requests = self.api_requests_count
        self.deadline = deadline
        self._last_request_ts = None

    def budget_remaining(self) -> bool:
        return (self.api_requests_count - self._cycle_start_requests < self._max_attempts
                and (self.deadline is None or time.monotonic() < self.deadline))

    def _wait(self, seconds: float) -> None:
        if not self.budget_remaining() or (self.deadline is not None and seconds >= self.deadline - time.monotonic()):
            raise DiscoveryBudgetExceeded("Discovery budget exhausted")
        if seconds > 0:
            time.sleep(seconds)
        if not self.budget_remaining():
            raise DiscoveryBudgetExceeded("Discovery budget exhausted")

    def is_available(self) -> bool:
        """Returns True if an API key is configured for the primary provider."""
        return bool(self.api_key and self.api_key.strip())

    def _call_api_on_provider(
        self,
        provider: str,
        base_url: str,
        api_key: str,
        payload: dict[str, Any],
        max_retries: int = 3,
        fallback_models: Optional[list[str]] = None,
    ) -> Optional[dict[str, Any]]:
        """One provider's attempts. Returns a result, or raises DiscoveryAPIError.

        Sets ``self._last_failure_transport_only`` so _call_api knows whether
        a provider fallback (different host, different DNS) is worth trying.
        """
        if self.deadline is None:
            self.configure_cycle(max_attempts=500, deadline=time.monotonic() + self.timeout)
        url = f"{base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "User-Agent": "NavaRoute-Discovery/1.0",
        }
        if provider == "openrouter":
            headers["HTTP-Referer"] = "https://github.com/mani-moh/navaroute"
            headers["X-Title"] = "NavaRoute-Discovery"

        primary_model = payload.get("model", self.model)
        models_to_try = [primary_model]
        for fm in (fallback_models or []):
            if fm not in models_to_try:
                models_to_try.append(fm)
        # Only stepfun-ai/step-5-preview is NVIDIA-only; never send it to
        # OpenRouter. (An earlier version also dropped every nvidia/* id,
        # which wrongly excluded OpenRouter-hosted Nemotron :free models —
        # including the default model itself.)
        if provider == "openrouter":
            models_to_try = [m for m in models_to_try if m != DEFAULT_NEMOTRON_MODEL]
            if not models_to_try:
                models_to_try = [DEFAULT_OPENROUTER_MODEL]

        attempted = False
        # Fallback-worthy unless the request itself was malformed (400).
        # Real Nov 13 log: 6x HTTP 503 BrokenPipe + 1x HTTP 404, no DNS line
        # at all. A 404 for one model must not block trying the other
        # provider, whose catalogue differs; only a 400 is provider-independent.
        fallback_worthy = False
        transport_only = True
        for cur_model in models_to_try:
            cur_payload = dict(payload)
            cur_payload["model"] = cur_model
            # reasoning_effort=high is verified only for Step 5 Preview on NVIDIA;
            # never send it to other models or to OpenRouter.
            if provider == "nvidia" and cur_model == DEFAULT_NEMOTRON_MODEL:
                cur_payload["reasoning_effort"] = "high"
            else:
                cur_payload.pop("reasoning_effort", None)
            # The free router picks a model at request time; a routed model
            # may not support the reasoning object, and a 400 there is not
            # fallback-worthy, so never send reasoning to the router.
            if cur_model == OPENROUTER_FREE_ROUTER:
                cur_payload.pop("reasoning", None)
            data = json.dumps(cur_payload).encode("utf-8")
            for attempt in range(1, max_retries + 1):
                try:
                    self._wait(max(0, self.min_request_interval - (time.monotonic() - self._last_request_ts)) if self._last_request_ts is not None else 0)
                except DiscoveryBudgetExceeded:
                    if attempted:
                        raise DiscoveryAPIError("Discovery API attempts failed within budget") from None
                    raise
                timeout = min(self.timeout, self.deadline - time.monotonic())
                if timeout <= 0:
                    if attempted:
                        raise DiscoveryAPIError("Discovery API attempts failed within budget")
                    raise DiscoveryBudgetExceeded("Discovery budget exhausted")
                self._last_request_ts = time.monotonic()
                self.api_requests_count += 1
                attempted = True
                request_started = time.monotonic()
                try:
                    status, body = fetch_bytes(url, timeout=timeout,
                                               max_bytes=2 * 1024 * 1024, deadline=self.deadline,
                                               headers=headers, data=data, method="POST", authenticated=True)
                except NetworkError as exc:
                    status, body = 503, b""
                    transport_error = _network_error_token(exc)
                else:
                    transport_error = ""
                result: Any = None
                if 200 <= status < 300:
                    try:
                        result = json.loads(body)
                        if isinstance(result, dict):
                            status = 200
                        else:
                            status = 502
                    except (ValueError, UnicodeError, RecursionError):
                        status = 502
                trace_path = os.environ.get("NAVAROUTE_AI_TRACE_PATH", "").strip()
                if trace_path:
                    response_text = body.decode("utf-8", "replace")
                    truncated = len(response_text) > TRACE_MAX_RESPONSE_TEXT
                    record = {
                        "ts": round(time.time(), 3),
                        "request_index": self.api_requests_count,
                        "model": cur_model,
                        "attempt": attempt,
                        "status": status,
                        "duration_ms": round((time.monotonic() - request_started) * 1000.0, 1),
                        "request_bytes": len(data),
                        "response_bytes": len(body),
                        "request_sha256": hashlib.sha256(data).hexdigest(),
                        "response_sha256": hashlib.sha256(body).hexdigest(),
                        "request": _redact_for_trace(cur_payload),
                        "response_text": response_text[:TRACE_MAX_RESPONSE_TEXT] + ("<truncated>" if truncated else ""),
                    }
                    try:
                        _write_trace_line(trace_path, record)
                    except OSError:
                        logger.warning("AI trace record could not be written")
                if 200 <= status < 300 and isinstance(result, dict):
                    return result
                if not transport_error:
                    transport_only = False
                if status != 400:
                    fallback_worthy = True
                logger.warning("Discovery API attempt failed (provider=%s, HTTP %d, response_bytes=%d, transport_error=%s)", provider, status, len(body), transport_error or "-")
                if status not in (429, 500, 502, 503, 504) or attempt == max_retries:
                    break
                try:
                    self._wait(attempt * (6 if status == 429 else 2))
                except DiscoveryBudgetExceeded:
                    raise DiscoveryAPIError("Discovery API attempts failed within budget") from None
            # Transport failure (DNS, connect, TLS, broken pipe) is per-host,
            # not per-model: trying the next model on the same dead host wastes
            # budget and time. Stop this provider and let _call_api try the
            # fallback host.
            if transport_error:
                break
        self._last_failure_transport_only = transport_only and attempted
        self._last_failure_fallback_worthy = fallback_worthy and attempted
        raise DiscoveryAPIError("Discovery API attempts failed")

    def _call_api(
        self,
        payload: dict[str, Any],
        max_retries: int = 3,
        fallback_models: Optional[list[str]] = None,
    ) -> Optional[dict[str, Any]]:
        """Primary provider first, then the other provider on provider failure.

        The Nov 13 log was 6x HTTP 503 BrokenPipe + 1x HTTP 404 on NVIDIA,
        with no DNS line at all. A different host fixes both: OpenRouter
        resolves, routes and catalogues independently. Fallback runs only when
        its key is configured, only when the failure was not a malformed
        request (400), and still counts against the same API budget.
        There is no cross-provider fallback FROM OpenRouter: its model list
        already ends at the free router, and no NVIDIA model currently works
        (stepfun-ai/step-5-preview 404s), so that attempt would only burn a
        request on a 404.
        """
        if not self.is_available():
            logger.warning("NemotronClient: no discovery API key is configured.")
            return None
        self._last_failure_transport_only = False
        self._last_failure_fallback_worthy = False
        try:
            return self._call_api_on_provider(
                self.provider, self.base_url, self.api_key,
                payload, max_retries=max_retries, fallback_models=fallback_models,
            )
        except DiscoveryAPIError:
            if not getattr(self, "_last_failure_fallback_worthy", False):
                raise
            if not self.fallback_api_key or not self.fallback_api_key.strip():
                raise
            if not self.budget_remaining():
                raise
            # No cross-provider fallback from OpenRouter (see docstring):
            # the model list already ends at the free router.
            if self.provider == "openrouter":
                raise
            logger.warning("Discovery primary provider failed; trying fallback provider")
            fallback_payload = dict(payload)
            if self.fallback_provider == "openrouter" and fallback_payload.get("model") == DEFAULT_NEMOTRON_MODEL:
                fallback_payload["model"] = DEFAULT_OPENROUTER_MODEL
            return self._call_api_on_provider(
                self.fallback_provider, self.fallback_base_url, self.fallback_api_key,
                fallback_payload, max_retries=max_retries, fallback_models=fallback_models,
            )

    def complete(
        self,
        prompt: str,
        system_prompt: str = "You are an autonomous intelligence agent specialized in proxy configurations, network circumvention, and Telegram subscription discovery.",
        temperature: float = 0.6,
        max_tokens: int = 2048,
    ) -> str:
        """Sends a text completion request and returns the assistant's text response."""
        if not self.is_available():
            return ""

        # Reasoning tokens share the max_tokens budget on OpenRouter, so a
        # high reasoning effort needs headroom for the answer itself.
        if self.provider == "openrouter":
            max_tokens = max(max_tokens, 4096)
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt},
            ],
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if self.provider == "openrouter" and self.model == DEFAULT_OPENROUTER_MODEL:
            # Best reasoning: high effort on the vetted default model only.
            # (An env/explicit model override gets no reasoning object, so
            # a model without reasoning support cannot 400 here. Never sent
            # to the free router either — see _call_api_on_provider — and
            # never to NVIDIA, which uses reasoning_effort instead.)
            payload["reasoning"] = {"effort": "high"}

        res = self._call_api(payload, fallback_models=TEXT_FALLBACK_MODELS)
        return self._response_text(res)

    @staticmethod
    def _response_text(res: Any) -> str:
        if not isinstance(res, dict) or not isinstance(res.get("choices"), list) or not res["choices"]:
            raise DiscoveryAPIError("Invalid NVIDIA API response")
        choice = res["choices"][0]
        if not isinstance(choice, dict) or not isinstance(choice.get("message"), dict) or not isinstance(choice["message"].get("content"), str):
            raise DiscoveryAPIError("Invalid NVIDIA API response")
        return choice["message"]["content"].strip()

    def complete_json(
        self,
        prompt: str,
        system_prompt: str = "You are a JSON-only response agent. You must respond with valid JSON enclosed in ```json ... ``` or directly as a valid JSON object.",
        temperature: float = 0.3,
        max_tokens: int = 2048,
    ) -> Optional[Any]:
        """Sends a prompt and parses the returned JSON structure."""
        raw_text = self.complete(
            prompt=prompt,
            system_prompt=system_prompt,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        if not raw_text:
            return None

        # Clean markdown codeblocks
        clean_text = raw_text.strip()
        if "```json" in clean_text:
            match = re.search(r"```json\s*(.*?)\s*```", clean_text, re.DOTALL)
            if match:
                clean_text = match.group(1).strip()
        elif "```" in clean_text:
            match = re.search(r"```\s*(.*?)\s*```", clean_text, re.DOTALL)
            if match:
                clean_text = match.group(1).strip()

        try:
            return json.loads(clean_text)
        except (ValueError, RecursionError):
            # Fallback: find outer {...} or [...]
            m = re.search(r"(\{|\[).+(\}|\])", clean_text, re.DOTALL)
            if m:
                try:
                    return json.loads(m.group(0))
                except (ValueError, RecursionError):
                    pass
            logger.warning("Invalid NVIDIA JSON output")
            return None

    def analyze_image(
        self,
        image_url: str,
        prompt: str = "Extract all proxy configs (vless, vmess, trojan, ss, hysteria2) or subscription URLs visible in this image.",
        system_prompt: str = "You are a high-accuracy vision analysis agent for network configurations and QR codes.",
        max_tokens: int = 1024,
    ) -> str:
        """Sends an image (downloaded to base64 data URI when possible) to Nemotron 3.5 Super VL."""
        self.last_image_outcome = "retryable"
        if not self.is_available():
            return ""

        self._wait(0)
        prepared_url = _download_image_as_data_uri(image_url, deadline=self.deadline)
        if not prepared_url:
            return ""
        self._wait(0)
        image_hash = hashlib.sha256(prepared_url.encode("ascii")).hexdigest()
        if image_hash in self.image_results:
            self.last_image_outcome = "success"
            return self.image_results[image_hash]

        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {"type": "image_url", "image_url": {"url": prepared_url}},
                    ],
                },
            ],
            "max_tokens": max_tokens,
        }

        res = self._call_api(payload, fallback_models=VISION_FALLBACK_MODELS)
        text = self._response_text(res)
        if text and len(text.encode("utf-8")) <= 65536:
            self.last_image_outcome = "success"
            nodes, _ = extract_nodes_from_content(text, SourceConfig("ocr", "Public OCR", image_url))
            # Persist only canonical public configs, not raw model output.
            self.image_results[image_hash] = "\n".join(n.to_uri(include_formatted_remark=False) for n in nodes[:400])
            self.image_results = bounded_image_results(self.image_results)
        return text
