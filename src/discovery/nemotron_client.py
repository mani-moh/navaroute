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

DEFAULT_NVIDIA_BASE_URL = "https://integrate.api.nvidia.com/v1"
DEFAULT_NEMOTRON_MODEL = "stepfun-ai/step-5-preview"
VISION_FALLBACK_MODELS = [
    "moonshotai/kimi-k2.6",
    "mistralai/mistral-large-4-1t-a52b",
    "nvidia/nemotron-3.5-super-vl-120b-a12b",
]
TEXT_FALLBACK_MODELS = [
    "moonshotai/kimi-k2.6",
    "mistralai/mistral-large-4-1t-a52b",
    "nvidia/nemotron-3-ultra-550b-a55b",
    "nvidia/nemotron-3.5-super-vl-120b-a12b",
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
    """Client for NVIDIA NIM Nemotron LLM/VLM API (OpenAI-compatible)."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
        base_url: Optional[str] = None,
        timeout: int = 45,
        min_request_interval: float = 0.0,
    ) -> None:
        self.api_key = (
            api_key
            if api_key is not None
            else os.environ.get("NVIDIA_API_KEY", "").strip()
        )
        self.model = (
            model
            if model is not None
            else os.environ.get("NVIDIA_MODEL", DEFAULT_NEMOTRON_MODEL).strip()
        )
        self.base_url = (
            base_url
            if base_url is not None
            else os.environ.get("NVIDIA_BASE_URL", DEFAULT_NVIDIA_BASE_URL).rstrip("/")
        )
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
        """Returns True if an API key is configured."""
        return bool(self.api_key and self.api_key.strip())

    def _call_api(
        self,
        payload: dict[str, Any],
        max_retries: int = 3,
        fallback_models: Optional[list[str]] = None,
    ) -> Optional[dict[str, Any]]:
        """Executes an HTTP POST with pacing, exponential backoff, and model fallback."""
        if not self.is_available():
            logger.warning("NemotronClient: NVIDIA_API_KEY is not configured.")
            return None

        if self.base_url != DEFAULT_NVIDIA_BASE_URL:
            raise ValueError("Unapproved NVIDIA API origin")
        if self.deadline is None:
            self.configure_cycle(max_attempts=500, deadline=time.monotonic() + self.timeout)
        url = f"{self.base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "User-Agent": "NavaRoute-Discovery/1.0",
        }

        primary_model = payload.get("model", self.model)
        models_to_try = [primary_model]
        for fm in (fallback_models or []):
            if fm not in models_to_try:
                models_to_try.append(fm)

        attempted = False
        for cur_model in models_to_try:
            cur_payload = dict(payload)
            cur_payload["model"] = cur_model
            # reasoning_effort=high is verified only for Step 5 Preview; never send it to other models.
            if cur_model == DEFAULT_NEMOTRON_MODEL:
                cur_payload["reasoning_effort"] = "high"
            else:
                cur_payload.pop("reasoning_effort", None)
            data = json.dumps(cur_payload).encode("utf-8")
            for attempt in range(1, max_retries + 1):
                try:
                    self._wait(max(0, self.min_request_interval - (time.monotonic() - self._last_request_ts)) if self._last_request_ts is not None else 0)
                except DiscoveryBudgetExceeded:
                    if attempted:
                        raise DiscoveryAPIError("NVIDIA API attempts failed within budget") from None
                    raise
                timeout = min(self.timeout, self.deadline - time.monotonic())
                if timeout <= 0:
                    if attempted:
                        raise DiscoveryAPIError("NVIDIA API attempts failed within budget")
                    raise DiscoveryBudgetExceeded("Discovery budget exhausted")
                self._last_request_ts = time.monotonic()
                self.api_requests_count += 1
                attempted = True
                try:
                    status, body = fetch_bytes(url, timeout=timeout,
                                               max_bytes=2 * 1024 * 1024, deadline=self.deadline,
                                               headers=headers, data=data, method="POST", authenticated=True)
                except NetworkError:
                    status, body = 503, b""
                if 200 <= status < 300:
                    try:
                        result = json.loads(body)
                        if isinstance(result, dict):
                            return result
                    except (ValueError, UnicodeError, RecursionError):
                        pass
                    status = 502
                logger.warning("NVIDIA API attempt failed (HTTP %d)", status)
                if status not in (429, 500, 502, 503, 504) or attempt == max_retries:
                    break
                try:
                    self._wait(attempt * (6 if status == 429 else 2))
                except DiscoveryBudgetExceeded:
                    raise DiscoveryAPIError("NVIDIA API attempts failed within budget") from None
        raise DiscoveryAPIError("NVIDIA API attempts failed")

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

        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt},
            ],
            "temperature": temperature,
            "max_tokens": max_tokens,
        }

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
