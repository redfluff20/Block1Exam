import os
import base64
import json
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

# Provider config. Prefer DB settings, then LLM_* env. Defaults are pinned to
# OpenRouter (pay-per-token, no subscription) with a Qwen3-VL vision model.
_DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"
_DEFAULT_MODEL = "qwen/qwen3-vl-8b-instruct"

BASE_URL = os.environ.get("LLM_BASE_URL", os.environ.get("DEEPSEEK_BASE_URL", _DEFAULT_BASE_URL))
MODEL = os.environ.get("LLM_MODEL", os.environ.get("DEEPSEEK_MODEL", _DEFAULT_MODEL))


def _db_setting(key):
    try:
        from app.db import get_setting
        v = get_setting(key)
        return v.strip() if v else None
    except Exception:
        return None


def get_client():
    api_key = _api_key()
    if not api_key:
        raise RuntimeError(
            "LLM_API_KEY not set. Add it in the Dashboard (Settings panel) or export "
            "LLM_API_KEY in .env before running, e.g. export LLM_API_KEY=sk-..."
        )
    return OpenAI(api_key=api_key, base_url=get_base_url())


def _api_key():
    """Resolve the API key: DB-stored setting first, then env vars."""
    return (_db_setting("llm_api_key")
            or os.environ.get("LLM_API_KEY")
            or os.environ.get("OPENROUTER_API_KEY")
            or os.environ.get("DEEPSEEK_API_KEY"))


def _deepseek_client():
    """Text-only client for fast, cheap jobs (summaries). Uses DEEPSEEK_* env vars."""
    key = os.environ.get("DEEPSEEK_API_KEY") or _db_setting("llm_api_key")
    if not key:
        raise RuntimeError("DEEPSEEK_API_KEY not set; cannot use fast text path")
    base = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
    return OpenAI(api_key=key, base_url=base)


def _deepseek_model():
    return os.environ.get("DEEPSEEK_MODEL", "deepseek-chat")


def chat_text(prompt, system=None, temperature=0.3, max_tokens=4000, json_mode=False):
    """Fast text-only call via DeepSeek (summaries/polish)."""
    client = _deepseek_client()
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})
    kwargs = dict(
        model=_deepseek_model(),
        messages=messages,
        temperature=temperature,
        max_tokens=max_tokens,
    )
    if json_mode:
        kwargs["response_format"] = {"type": "json_object"}
    resp = client.chat.completions.create(**kwargs)
    return resp.choices[0].message.content.strip()


def chat_json_text(prompt, system=None, temperature=0.3, max_tokens=4000, retries=2):
    """Fast text-only JSON call via DeepSeek."""
    sys = (system or "") + (
        "\n\nRespond with ONLY a valid JSON object. No markdown fences, no commentary."
    )
    last_err = None
    for attempt in range(retries + 1):
        try:
            raw = chat_text(
                prompt, system=sys, temperature=temperature,
                max_tokens=max_tokens, json_mode=(attempt > 0),
            )
            return _parse_json(raw)
        except Exception as e:
            last_err = e
    raise last_err


def get_model():
    """Model: DB setting > env > default."""
    return _db_setting("llm_model") or MODEL


def get_base_url():
    """Base URL: DB setting > env > default."""
    return _db_setting("llm_base_url") or BASE_URL


def _image_data_url(path):
    """Encode a local image as a data URL for the vision API."""
    ext = os.path.splitext(path)[1].lower().lstrip(".") or "png"
    if ext == "jpg":
        ext = "jpeg"
    mime = "image/" + ext
    with open(path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode("ascii")
    return f"data:{mime};base64,{b64}"


def _build_user_content(prompt, images):
    """Build a multimodal user message: text part + one image_url part per image."""
    if not images:
        return prompt
    parts = [{"type": "text", "text": prompt}]
    for p in images:
        parts.append({"type": "image_url", "image_url": {"url": _image_data_url(p)}})
    return parts


def chat(prompt, system=None, temperature=0.7, max_tokens=2000, json_mode=False, images=None):
    client = get_client()
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": _build_user_content(prompt, images)})
    kwargs = dict(
        model=MODEL,
        messages=messages,
        temperature=temperature,
        max_tokens=max_tokens,
    )
    if json_mode:
        kwargs["response_format"] = {"type": "json_object"}
    resp = client.chat.completions.create(**kwargs)
    return resp.choices[0].message.content.strip()


def chat_json(prompt, system=None, temperature=0.7, max_tokens=4000, retries=2, images=None):
    """Ask the model for strict JSON output and parse it. Retries with JSON mode."""
    sys = (system or "") + (
        "\n\nRespond with ONLY a valid JSON object. No markdown fences, no commentary."
    )
    last_err = None
    for attempt in range(retries + 1):
        try:
            raw = chat(
                prompt, system=sys, temperature=temperature,
                max_tokens=max_tokens, json_mode=(attempt > 0), images=images,
            )
            parsed = _parse_json(raw)
            return parsed
        except Exception as e:
            last_err = e
    raise last_err


def _parse_json(raw):
    import re as _re
    raw = raw.strip()
    if raw.startswith("```"):
        raw = _re.sub(r"^```[a-zA-Z]*\n?", "", raw)
        raw = _re.sub(r"\n?```$", "", raw)
    try:
        return json.loads(raw)
    except (ValueError, TypeError):
        pass
    # fallback: find the outermost {...} object
    start = raw.find("{")
    end = raw.rfind("}")
    if start != -1 and end != -1 and end > start:
        return json.loads(raw[start : end + 1])
    raise ValueError(f"Could not parse JSON from model output: {raw[:200]}")


import re
