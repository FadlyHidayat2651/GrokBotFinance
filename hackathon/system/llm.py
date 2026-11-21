"""Optional chat completion through xAI or OpenRouter. Standard-library HTTP only.

Temperature is 0. Responses are cached under system/cache so a repeated run
does not call the network again and cannot change a verdict.
"""

import hashlib
import json
import os
import ssl
import threading
import time
import urllib.error
import urllib.request

from config import CONFIG, HERE

CACHE_DIR = os.path.join(HERE, "cache")
_CACHE_LOCK = threading.Lock()


def _provider():
    forced = (os.environ.get("LLM_PROVIDER") or "").strip().lower()
    has_xai = bool(os.environ.get("XAI_API_KEY"))
    has_openrouter = bool(os.environ.get("OPENROUTER_API_KEY"))
    if forced in {"grok", "xai"} and has_xai:
        return "grok"
    if forced == "openrouter" and has_openrouter:
        return "openrouter"
    if has_xai:
        return "grok"
    if has_openrouter:
        return "openrouter"
    return None


def _endpoint(provider):
    if provider == "grok":
        return CONFIG["grok_url"], CONFIG["model_grok"], os.environ["XAI_API_KEY"]
    return CONFIG["openrouter_url"], CONFIG["model_openrouter"], os.environ["OPENROUTER_API_KEY"]


def _cache_path(key):
    return os.path.join(CACHE_DIR, key + ".json")


def complete_json(messages):
    """Return parsed JSON, or None when no key is set or every attempt fails."""
    provider = _provider()
    if not provider:
        return None
    url, model, key = _endpoint(provider)
    payload_base = {
        "model": model,
        "temperature": CONFIG["temperature"],
        "seed": CONFIG["seed"],
        "messages": messages,
    }
    digest = hashlib.sha256(
        json.dumps({"provider": provider, "payload": payload_base}, sort_keys=True).encode("utf-8")
    ).hexdigest()
    cached = _cache_path(digest)
    with _CACHE_LOCK:
        if os.path.isfile(cached):
            with open(cached, encoding="utf-8") as fh:
                return json.load(fh)

    variants = [
        dict(payload_base, response_format={"type": "json_object"}),
        payload_base,
    ]
    last_error = None
    for attempt in range(int(CONFIG["retries"]) + 1):
        body = variants[min(attempt, len(variants) - 1)]
        request = urllib.request.Request(
            url,
            data=json.dumps(body).encode("utf-8"),
            headers={
                "Authorization": "Bearer " + key,
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=CONFIG["timeout_seconds"], context=ssl.create_default_context()) as response:
                raw = json.loads(response.read().decode("utf-8"))
            content = raw["choices"][0]["message"]["content"]
            parsed = _parse_json(content)
            if parsed is None:
                last_error = "unparsed"
                continue
            with _CACHE_LOCK:
                os.makedirs(CACHE_DIR, exist_ok=True)
                temporary = cached + ".tmp"
                with open(temporary, "w", encoding="utf-8") as fh:
                    json.dump(parsed, fh, sort_keys=True)
                os.replace(temporary, cached)
            return parsed
        except (urllib.error.URLError, urllib.error.HTTPError, KeyError, TimeoutError, json.JSONDecodeError, OSError) as exc:
            last_error = exc
            time.sleep(0.4 * (attempt + 1))
    return None


def _parse_json(content):
    text = (content or "").strip()
    if text.startswith("```"):
        text = re_strip_fence(text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            try:
                return json.loads(text[start : end + 1])
            except json.JSONDecodeError:
                return None
    return None


def re_strip_fence(text):
    lines = text.splitlines()
    if lines and lines[0].startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].startswith("```"):
        lines = lines[:-1]
    return "\n".join(lines)
