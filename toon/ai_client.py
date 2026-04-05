from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Any

from zai import ZaiClient

from toon.config import _Settings


class AIClient:
    def __init__(self, settings: _Settings) -> None:
        self._client = ZaiClient(
            api_key=settings.zai_api_key,
            base_url=settings.zai_base_url,
        )
        self._settings = settings

    # --- Vision (GLM-5V-Turbo) ---

    def vision_chat(
        self,
        image_source: str | Path,
        prompt: str,
        system_prompt: str = "",
    ) -> str:
        """Send an image + text prompt to GLM-5V-Turbo. Returns text response."""
        image_content = self._build_image_content(image_source)
        messages: list[dict[str, Any]] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({
            "role": "user",
            "content": [
                image_content,
                {"type": "text", "text": prompt},
            ],
        })
        response = self._client.chat.completions.create(
            model=self._settings.vision_model,
            messages=messages,
            max_tokens=4096,
        )
        return response.choices[0].message.content

    def vision_chat_multi(
        self,
        image_sources: list[str | Path],
        prompt: str,
        system_prompt: str = "",
    ) -> str:
        """Send multiple images + text prompt to GLM-5V-Turbo."""
        content: list[dict[str, Any]] = [
            self._build_image_content(src) for src in image_sources
        ]
        content.append({"type": "text", "text": prompt})
        messages: list[dict[str, Any]] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": content})
        response = self._client.chat.completions.create(
            model=self._settings.vision_model,
            messages=messages,
            max_tokens=4096,
        )
        return response.choices[0].message.content

    # --- Text (GLM-5) ---

    def chat(
        self,
        messages: list[dict[str, str]],
        system_prompt: str = "",
        max_tokens: int = 4096,
    ) -> str:
        """Send a text chat request to GLM-5. Returns text response."""
        import time
        full_messages: list[dict[str, Any]] = []
        if system_prompt:
            full_messages.append({"role": "system", "content": system_prompt})
        full_messages.extend(messages)
        for attempt in range(5):
            try:
                response = self._client.chat.completions.create(
                    model=self._settings.text_model,
                    messages=full_messages,
                    max_tokens=max_tokens,
                    timeout=60,  # seconds — prevent indefinite hangs
                )
                content = response.choices[0].message.content
                if content:
                    return content
                # Empty response — wait and retry
                time.sleep(3 * (attempt + 1))
            except Exception as e:
                msg = str(e)
                if "1302" in msg or "429" in msg or "rate limit" in msg.lower():
                    wait = min(15 * (attempt + 1), 60)  # cap at 60s
                    time.sleep(wait)
                elif "timeout" in msg.lower() or "timed out" in msg.lower():
                    time.sleep(5)  # short wait then retry on timeout
                else:
                    raise
        return ""

    # --- OCR fallback (GLM-OCR) ---

    def ocr_parse(self, image_source: str | Path) -> str:
        """Use GLM-OCR layout parsing to extract raw text from an image/PDF."""
        if isinstance(image_source, Path):
            # GLM-OCR accepts URLs; for local files, use base64 data URI
            data_uri = self._path_to_data_uri(image_source)
            file_ref = data_uri
        else:
            file_ref = image_source
        response = self._client.layout_parsing.create(
            model=self._settings.ocr_model,
            file=file_ref,
        )
        # Return the parsed content as a string
        if hasattr(response, "content"):
            return str(response.content)
        return str(response)

    # --- Helpers ---

    def _build_image_content(self, source: str | Path) -> dict[str, Any]:
        if isinstance(source, Path):
            data_uri = self._path_to_data_uri(source)
            return {"type": "image_url", "image_url": {"url": data_uri}}
        # Assume it's already a URL or data URI string
        return {"type": "image_url", "image_url": {"url": source}}

    @staticmethod
    def _path_to_data_uri(path: Path) -> str:
        suffix = path.suffix.lower()
        mime_map = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp"}
        mime = mime_map.get(suffix, "image/jpeg")
        data = base64.b64encode(path.read_bytes()).decode("ascii")
        return f"data:{mime};base64,{data}"


def parse_json_response(text: str) -> Any:
    """Extract JSON from a model response that may contain markdown code fences."""
    text = text.strip()
    # Strip ```json ... ``` or ``` ... ```
    if text.startswith("```"):
        lines = text.split("\n")
        inner = "\n".join(lines[1:-1]) if lines[-1].strip() == "```" else "\n".join(lines[1:])
        text = inner.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # Try to find the largest valid JSON prefix (handles truncated responses)
        for end in range(len(text), 0, -1):
            try:
                return json.loads(text[:end])
            except json.JSONDecodeError:
                continue
        raise
