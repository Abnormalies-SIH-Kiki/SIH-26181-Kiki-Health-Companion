"""
Multi-Provider Router for Non-Speaking LLM Work
================================================

Used by Unified Idle Mind, workers, vision processing, and summarization.
This is the SLOW, HIGH-QUALITY path — completely separate from the fast
core/llm.py streaming pipeline.

Supports: Gemini (cloud) + Groq (cloud) + local models (LM Studio)
Ported from KIKI-SMART — standalone, no LiveKit dependencies.
"""

import os
import json
import base64
from google import genai
from google.genai import types
from google.oauth2 import service_account
from openai import OpenAI
from groq import Groq

from tools_and_config.config_loader import get_full_config, get_llm_config

# Load API keys from environment
KEY_LIST = json.loads(os.getenv("GEMINI_KEY_LIST", "[]"))
GROQ_KEY_LIST = json.loads(os.getenv("GROQ_API_KEY_LIST", "[]"))
GROQ_MODEL = "openai/gpt-oss-120b"

grounding_tool = types.Tool(
    google_search=types.GoogleSearch()
)

# --- Vertex AI (via litellm) config ---
# The Vertex model strings + service-account credentials mirror the speaking path
# in core/llm.py. Used as the PRIMARY summarizer (see generate()). litellm is
# heavy to import, so it's loaded lazily on first use (this is a background-only
# path). Auth matches apiusage.py: the service-account key file is loaded and
# litellm builds cloud-platform-scoped service_account credentials from it.
_VERTEX_SA_FILE = os.getenv("VERTEX_SERVICE_ACCOUNT_FILE", "")
_VERTEX_PROJECT = os.getenv("VERTEX_PROJECT", "")
_VERTEX_LOCATION = "global"  # gemini-3.x preview models are served global-only
_completion = None
_vertex_credentials_json = None
_genai_vertex_client = None


def _get_vertex_genai_client():
    """google-genai client pointed at Vertex AI with service-account auth.

    Mirrors apiusage.py exactly: service_account.Credentials.from_service_account_file
    with the cloud-platform scope, then genai.Client(vertexai=True, project, location,
    credentials). Built once and reused. This replaces the old API-key Gemini path
    (genai.Client(api_key=...)) — there are no API keys with Vertex, just the SA."""
    global _genai_vertex_client
    if _genai_vertex_client is None:
        credentials = service_account.Credentials.from_service_account_file(
            _VERTEX_SA_FILE,
            scopes=["https://www.googleapis.com/auth/cloud-platform"],
        )
        _genai_vertex_client = genai.Client(
            vertexai=True,
            project=_VERTEX_PROJECT,
            location=_VERTEX_LOCATION,
            credentials=credentials,
        )
    return _genai_vertex_client


def _get_vertex_models():
    """Vertex model + fallback model strings from the llm config block."""
    llm_cfg = get_full_config().get("llm", {})
    models = [llm_cfg.get("model"), llm_cfg.get("fallback_model")]
    # Keep only Vertex models, de-duped, order preserved.
    seen = set()
    out = []
    for m in models:
        if m and "vertex_ai" in m and m not in seen:
            seen.add(m)
            out.append(m)
    return out


def _get_completion():
    """Lazily import litellm.completion (multi-second import on the Pi)."""
    global _completion
    if _completion is None:
        from litellm import completion as _c
        _completion = _c
    return _completion


def _get_vertex_credentials():
    """Lazily load the service-account key json used for Vertex auth (apiusage.py)."""
    global _vertex_credentials_json
    if _vertex_credentials_json is None:
        with open(_VERTEX_SA_FILE, "r") as f:
            _vertex_credentials_json = json.dumps(json.load(f))
    return _vertex_credentials_json


def _call_vertex(content, b64_image=None, thinking_level="MEDIUM"):
    """Call Vertex AI Gemini via litellm, trying the configured model then fallback."""
    models = _get_vertex_models()
    if not models:
        print("[Vertex] No vertex_ai model configured in llm.model/fallback_model")
        return None

    if b64_image is None:
        messages = [{"role": "user", "content": content}]
    else:
        messages = [{
            "role": "user",
            "content": [
                {"type": "text", "text": content},
                {"type": "image_url",
                 "image_url": {"url": f"data:image/jpeg;base64,{b64_image}"}},
            ],
        }]

    for model in models:
        try:
            creds = _get_vertex_credentials()
            resp = _get_completion()(
                model=model,
                messages=messages,
                vertex_credentials=creds,
                vertex_project=_VERTEX_PROJECT,
                vertex_location=_VERTEX_LOCATION,
            )
            result = resp.choices[0].message.content
            if result:
                print(f"[Vertex] {model} responded ({len(result)} chars)")
                return result
        except Exception as e:
            print(f"[Vertex] {model} failed: {e}, trying next...")

    return None


def _get_local_llm_config():
    """Load local LLM config from config.json."""
    config = get_full_config()
    return config.get("use_local_llm", {})


def _get_local_client(cfg):
    """Create an OpenAI-compatible client pointing at the local server.

    NOTE: currently UNUSED — _call_local_model routes through
    local_llm.generate_background (the shared single-slot coordinator), so this
    direct client isn't on any live path. Kept for direct-client callers. Its
    default now points at the SAME box as core/llm.py / core/local_llm.py
    (llm.local_api_base) — one source of truth for the local endpoint."""
    default_base = get_llm_config().get("local_api_base", "http://127.0.0.1:8080/v1")
    return OpenAI(
        base_url=cfg.get("local_api_base", default_base),
        api_key=cfg.get("local_api_key", "lm-studio"),
    )


def _call_local_model(content, b64_image=None, model_name="nanbeige4.1-3b", cfg=None):
    """
    Call the local llama.cpp box via the shared single-slot coordinator
    (chat/completions, mmproj vision, abortable). Routed here so summary/vision/
    reasoning all share ONE local path that the speaking model can preempt.
    """
    from core import local_llm  # local import to avoid any import cycle
    try:
        result = local_llm.generate_background(
            prompt=content, image_b64=b64_image, max_tokens=512, temperature=0.6
        )
        if result:
            print(f"[LocalLLM] {model_name} responded ({len(result)} chars)")
        return result
    except Exception as e:
        print(f"[LocalLLM] {model_name} failed: {e}")
        return None


def _call_gemini(content, b64_image=None, thinking_level="MEDIUM", websearch=False, model="gemini-3-flash-preview"):
    """Call Gemini on Vertex AI (service-account auth, like apiusage.py).

    Was an API-key path (genai.Client(api_key=...)); now goes through the shared
    Vertex client so vision/grounding/reasoning use the same SA creds as the rest
    of the Vertex pipeline. b64_image must be the RAW base64 string."""
    use_thinking = (model == "gemini-3-flash-preview")

    try:
        client = _get_vertex_genai_client()

        config_kwargs = {}
        if use_thinking:
            config_kwargs["thinking_config"] = types.ThinkingConfig(
                thinking_level=thinking_level.upper(),
            )
        config_kwargs["tools"] = [grounding_tool] if websearch else []
        # config_kwargs["temperature"]=1.05
        # config_kwargs["frequency_penalty"]=0.5

        generate_content_config = types.GenerateContentConfig(**config_kwargs)

        if b64_image is None:
            contents = [
                types.Content(
                    role="user",
                    parts=[types.Part.from_text(text=content)],
                ),
            ]
        else:
            contents = [
                types.Content(
                    role="user",  # Vertex requires an explicit role on every Content
                    parts=[
                        types.Part(text=content),
                        types.Part(
                            inline_data=types.Blob(
                                mime_type="image/jpeg",
                                data=base64.b64decode(b64_image),
                            ),
                            media_resolution={"level": "media_resolution_high"}
                        )
                    ]
                )
            ]

        resp = client.models.generate_content(
            model=model,
            contents=contents,
            config=generate_content_config,
        )
        if resp.text:
            print(f"[Gemini/Vertex] {model} responded ({len(resp.text)} chars)")
        return resp.text

    except Exception as e:
        print(f"[Gemini/Vertex] {model} failed: {e}")
        return None


def _call_groq(content, thinking_level="MEDIUM", model=None):
    """Try all Groq API keys with the requested text model."""
    model = model or GROQ_MODEL
    effort_map = {"LOW": "low", "MEDIUM": "medium", "HIGH": "high"}
    reasoning_effort = effort_map.get(thinking_level.upper(), "medium")

    for api_key in GROQ_KEY_LIST:
        try:
            client = Groq(api_key=api_key)
            chat_completion = client.chat.completions.create(
                messages=[{"role": "user", "content": content}],
                reasoning_effort=reasoning_effort,
                model=model,
            )
            result = chat_completion.choices[0].message.content
            print(f"[Groq] {model} responded ({len(result)} chars)")
            return result

        except Exception as e:
            print(f"[Groq] Key failed: {e}")

    return None


def generate(content, b64_image=None, thinking_level="MEDIUM", websearch=False,
             purpose="general", cloud_category=None, cloud_provider=None,
             cloud_model=None, cloud_fallback_model=None):
    """
    Generate a response using local LLMs and/or cloud, with smart routing.

    Args:
        content: The prompt text
        b64_image: Optional base64 encoded image
        thinking_level: "LOW", "MEDIUM", or "HIGH"
        websearch: Whether to enable web search grounding
        purpose: "general", "vision", "summary", or "reasoning"
        cloud_category: cost-guard bucket for the cloud rate limiter
            (defaults to `purpose`). Callers that want finer granularity than
            `purpose` (for example Unified Idle Mind uses its own category).
        cloud_provider/cloud_model/cloud_fallback_model: Optional dedicated
            cloud route for a caller such as Unified Idle Mind. Supported
            providers are "vertex_ai" and "groq". When omitted, the legacy
            shared cloud fallback chain below is used.
    """
    cfg = _get_local_llm_config()

    # Summarization ALWAYS runs on the cloud (Gemini), never on the single-slot
    # local box. Summaries trigger right after a turn (inside the conversation-hot
    # window) and we never want them competing for the box that must stay warm for
    # speaking. This is enforced in code here, independent of the use_local_llm
    # flags, so summary can never be routed locally (neither as primary nor as the
    # last-resort fallback below).
    force_cloud = (purpose == "summary")

    use_vision_local = cfg.get("vision", False)
    use_summary_local = cfg.get("summary", False) and not force_cloud
    use_reasoning_local = cfg.get("reasoning", False)

    vision_model = cfg.get("vision_model", "zwz-4b")
    text_model = cfg.get("text_model", "nanbeige4.1-3b")

    has_image = b64_image is not None
    local_primary = False
    local_model = text_model
    local_image = None

    if use_vision_local and has_image:
        local_primary = True
        local_model = vision_model
        local_image = b64_image
        print(f"[LLM Router] Local PRIMARY: {vision_model} (vision + image)")
    elif use_summary_local and purpose == "summary":
        local_primary = True
        local_model = text_model
        local_image = None
        print(f"[LLM Router] Local PRIMARY: {text_model} (summary)")
    elif use_reasoning_local and purpose == "reasoning":
        local_primary = True
        local_model = text_model
        local_image = None
        print(f"[LLM Router] Local PRIMARY: {text_model} (reasoning)")

    if local_primary:
        result = _call_local_model(content, b64_image=local_image, model_name=local_model, cfg=cfg)
        if result:
            return result
        print(f"[LLM Router] Local {local_model} failed, falling back to Gemini cloud...")

    # --- CLOUD COST GUARD --------------------------------------------------
    # Everything below this point hits a paid provider (Vertex/Gemini/Groq).
    # The rate limiter (core/cloud_budget.py) enforces per-hour/day + per-
    # category caps from the live `cloud_limits` config. Denied → skip the
    # cloud call (return None), same as when the cloud is unreachable. Local
    # PRIMARY work above is never charged against the budget (it's the free box).
    try:
        from core.cloud_budget import get_cloud_budget
        category = cloud_category or purpose
        allowed, reason = get_cloud_budget().allow(category)
        if not allowed:
            print(f"[CloudBudget] ⛔ '{category}' cloud call skipped — {reason}")
            return None
    except Exception as e:                       # never let the guard break a call
        print(f"[CloudBudget] guard error (allowing call): {e}")

    # A caller with its own model policy gets that exact primary/fallback pair.
    # Keep this separate from llm.model so changing the idle mind never changes
    # Kiki's foreground speaking or summary models.
    if cloud_model:
        provider = str(cloud_provider or "vertex_ai").strip().lower()
        models = []
        for candidate in (cloud_model, cloud_fallback_model):
            candidate = str(candidate or "").strip()
            if candidate and candidate not in models:
                models.append(candidate)
        for model in models:
            if provider in {"vertex", "vertex_ai", "gemini"}:
                vertex_model = model.removeprefix("vertex_ai/")
                result = _call_gemini(
                    content, b64_image, thinking_level, websearch,
                    model=vertex_model)
            elif provider == "groq":
                if has_image:
                    print("[LLM Router] Configured Groq route does not support images")
                    result = None
                else:
                    result = _call_groq(content, thinking_level, model=model)
            else:
                print(f"[LLM Router] Unsupported configured cloud provider: {provider}")
                return None
            if result:
                return result
            print(f"[LLM Router] Configured {provider}/{model} failed")
        return None

    # Summarization PRIMARY: Vertex AI Gemini (via litellm). This is the preferred
    # path for summaries — same Vertex models the speaking pipeline uses. The
    # google-genai (API-key) Gemini calls below remain as fallbacks if Vertex fails.
    if force_cloud:
        result = _call_vertex(content, b64_image, thinking_level)
        if result:
            return result
        print("[LLM Router] Vertex AI summary failed, falling back to Gemini API keys...")

    # Try gemini-3-flash-preview
    result = _call_gemini(content, b64_image, thinking_level, websearch, model="gemini-3-flash-preview")
    if result:
        return result
    print("[LLM Router] All gemini-3-flash keys exhausted!")

    # Try gemini-2.5-flash
    result = _call_gemini(content, b64_image, thinking_level, websearch, model="gemini-2.5-flash")
    if result:
        print("[LLM Router] Using gemini-2.5-flash")
        return result
    print("[LLM Router] All gemini-2.5-flash keys exhausted!")

    # Try Groq (text-only)
    if not has_image:
        result = _call_groq(content, thinking_level)
        if result:
            return result
        print("[LLM Router] All Groq keys exhausted!")
    else:
        print("[LLM Router] Skipping Groq (does not support images)")

    # LAST RESORT — local models even if flags are off. Gated by config:
    # allow_local_fallback=false (default) keeps background work CLOUD-ONLY so it
    # never lands on the single-slot speaking box; the task simply skips (None).
    if not local_primary and not force_cloud and cfg.get("allow_local_fallback", False):
        print("[LLM Router] ALL cloud providers failed! Using local models as LAST RESORT...")
        if has_image:
            result = _call_local_model(content, b64_image=b64_image, model_name=vision_model, cfg=cfg)
            if result:
                return result
        result = _call_local_model(content, b64_image=None, model_name=text_model, cfg=cfg)
        if result:
            return result
        print("[LLM Router] CRITICAL: All providers (cloud + local) failed!")

    return None
