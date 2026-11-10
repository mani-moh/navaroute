from __future__ import annotations

import json
import logging
import os
import re
import time
import urllib.error
import urllib.request
from typing import Any, Optional

logger = logging.getLogger(__name__)

DEFAULT_NVIDIA_BASE_URL = "https://integrate.api.nvidia.com/v1"
DEFAULT_NEMOTRON_MODEL = "nvidia/nemotron-3.5-super-vl-120b-a12b"


class NemotronClient:
    """Client for NVIDIA NIM Nemotron LLM/VLM API (OpenAI-compatible)."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
        base_url: Optional[str] = None,
        timeout: int = 45,
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

    def is_available(self) -> bool:
        """Returns True if an API key is configured."""
        return bool(self.api_key and self.api_key.strip())

    def _call_api(self, payload: dict[str, Any], max_retries: int = 3) -> Optional[dict[str, Any]]:
        """Executes an HTTP POST to the completions endpoint with exponential backoff."""
        if not self.is_available():
            logger.warning("NemotronClient: NVIDIA_API_KEY is not configured.")
            return None

        url = f"{self.base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "User-Agent": "PattN-Nemotron-Agent/1.0",
        }

        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(url, data=data, headers=headers, method="POST")

        for attempt in range(1, max_retries + 1):
            try:
                try:
                    with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                        resp_body = resp.read().decode("utf-8")
                        return json.loads(resp_body)
                except urllib.error.URLError as url_exc:
                    if isinstance(url_exc, urllib.error.HTTPError):
                        raise
                    # Fallback direct connection bypassing dead Windows local system proxy
                    opener_direct = urllib.request.build_opener(urllib.request.ProxyHandler({}))
                    with opener_direct.open(req, timeout=self.timeout) as resp:
                        resp_body = resp.read().decode("utf-8")
                        return json.loads(resp_body)
            except urllib.error.HTTPError as exc:
                # 429 Rate Limit or 5xx Server Error -> retry
                if exc.code in (429, 500, 502, 503, 504) and attempt < max_retries:
                    wait_time = attempt * 2
                    logger.warning(
                        "Nemotron API HTTP %d on attempt %d/%d, retrying in %ds...",
                        exc.code, attempt, max_retries, wait_time
                    )
                    time.sleep(wait_time)
                    continue
                logger.error("Nemotron API HTTP error %d: %s", exc.code, exc.reason)
                return None
            except Exception as exc:
                if attempt < max_retries:
                    time.sleep(attempt * 2)
                    continue
                logger.error("Nemotron API network error: %s", exc)
                return None

        return None

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

        res = self._call_api(payload)
        if not res or "choices" not in res or not res["choices"]:
            return ""

        choice = res["choices"][0]
        msg = choice.get("message", {})
        return msg.get("content", "").strip()

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
        except Exception:
            # Fallback: find outer {...} or [...]
            m = re.search(r"(\{|\[).+(\}|\])", clean_text, re.DOTALL)
            if m:
                try:
                    return json.loads(m.group(0))
                except Exception:
                    pass
            logger.warning("Failed to parse Nemotron JSON output: %s", raw_text[:200])
            return None

    def analyze_image(
        self,
        image_url: str,
        prompt: str = "Extract all proxy configs (vless, vmess, trojan, ss, hysteria2) or subscription URLs visible in this image.",
        system_prompt: str = "You are a high-accuracy vision analysis agent for network configurations and QR codes.",
        max_tokens: int = 1024,
    ) -> str:
        """Sends an image URL / base64 data to Nemotron 3.5 Super VL for multimodal analysis."""
        if not self.is_available():
            return ""

        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {"type": "image_url", "image_url": {"url": image_url}},
                    ],
                },
            ],
            "max_tokens": max_tokens,
        }

        res = self._call_api(payload)
        if not res or "choices" not in res or not res["choices"]:
            return ""

        choice = res["choices"][0]
        msg = choice.get("message", {})
        return msg.get("content", "").strip()
