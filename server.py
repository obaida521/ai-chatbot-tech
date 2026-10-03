"""
AI Chatbot - Day 2 & Day 3
Flask backend that talks to an OpenAI-compatible LLM API (default: Groq, free tier).

Features
- System prompt (role / personality)
- Streaming responses (Server-Sent Events)
- Server-side conversation history per session (context/memory) with trimming
- Input validation, per-IP rate limiting, retries with fallback model
- Clear chat endpoint, health check, structured logging
"""
import json
import logging
import os
import threading
import time
import uuid
from collections import defaultdict, deque

from dotenv import load_dotenv
from flask import Flask, Response, jsonify, request, send_from_directory, stream_with_context
from openai import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    AuthenticationError,
    OpenAI,
    RateLimitError,
)

load_dotenv()

# ----------------------------- Configuration -----------------------------
API_KEY = os.getenv("LLM_API_KEY", "")
BASE_URL = os.getenv("LLM_BASE_URL", "https://api.groq.com/openai/v1")
MODEL = os.getenv("LLM_MODEL", "llama-3.3-70b-versatile")
FALLBACK_MODEL = os.getenv("LLM_FALLBACK_MODEL", "llama-3.1-8b-instant")
MAX_INPUT_CHARS = int(os.getenv("MAX_INPUT_CHARS", "2000"))
MAX_HISTORY_MESSAGES = int(os.getenv("MAX_HISTORY_MESSAGES", "20"))
RATE_LIMIT_PER_MIN = int(os.getenv("RATE_LIMIT_PER_MIN", "20"))
REQUEST_TIMEOUT = float(os.getenv("REQUEST_TIMEOUT", "30"))
TEMPERATURE = float(os.getenv("TEMPERATURE", "0.6"))
PORT = int(os.getenv("PORT", "5000"))

SYSTEM_PROMPT = os.getenv(
    "SYSTEM_PROMPT",
    """You are "Nova", a friendly and knowledgeable AI study and tech assistant.

Role:
- Help students and developers understand programming, AI, and general technology.
- Explain step by step, using simple language first, then add technical depth if useful.

Style rules:
- Reply in the same language the user writes in (English, Urdu, or Roman Urdu).
- Be concise. Use Markdown: short headings, bullet lists, and fenced code blocks with a language tag.
- If you are not sure about something, say so instead of guessing.
- If a question is unclear, ask one short clarifying question.
- Politely refuse harmful or illegal requests.""",
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("chatbot")

if not API_KEY:
    log.warning("LLM_API_KEY is not set. Copy .env.example to .env and add your key.")

client = OpenAI(api_key=API_KEY or "missing", base_url=BASE_URL, timeout=REQUEST_TIMEOUT, max_retries=1)

app = Flask(__name__, static_folder="static", static_url_path="")

# ----------------------------- In-memory state -----------------------------
# NOTE: for multi-server production, replace with Redis / a database.
_sessions: dict[str, list[dict]] = {}
_sessions_lock = threading.Lock()
_hits: dict[str, deque] = defaultdict(deque)


def rate_limited(ip: str) -> bool:
    now = time.time()
    q = _hits[ip]
    while q and now - q[0] > 60:
        q.popleft()
    if len(q) >= RATE_LIMIT_PER_MIN:
        return True
    q.append(now)
    return False


def get_history(session_id: str) -> list[dict]:
    with _sessions_lock:
        return list(_sessions.get(session_id, []))


def save_history(session_id: str, history: list[dict]) -> None:
    # keep only the most recent messages so prompts stay small and cheap
    with _sessions_lock:
        _sessions[session_id] = history[-MAX_HISTORY_MESSAGES:]


def sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def friendly_error(exc: Exception) -> tuple[str, int]:
    if isinstance(exc, AuthenticationError):
        return "API key is invalid or missing. Check LLM_API_KEY in your .env file.", 401
    if isinstance(exc, RateLimitError):
        return "Rate limit reached on the AI provider. Please wait a few seconds and try again.", 429
    if isinstance(exc, APITimeoutError):
        return "The AI took too long to respond. Please try again.", 504
    if isinstance(exc, APIConnectionError):
        return "Could not reach the AI service. Check your internet connection.", 503
    if isinstance(exc, APIStatusError):
        return f"AI service error ({exc.status_code}). Please try again later.", 502
    return "Something went wrong on the server.", 500


# ----------------------------- Routes -----------------------------
@app.get("/")
def index():
    return send_from_directory(app.static_folder, "index.html")


@app.get("/api/health")
def health():
    return jsonify(status="ok", model=MODEL, provider=BASE_URL, key_configured=bool(API_KEY))


@app.post("/api/chat")
def chat():
    ip = request.headers.get("X-Forwarded-For", request.remote_addr or "unknown").split(",")[0].strip()
    if rate_limited(ip):
        return jsonify(error="Too many requests. Please slow down a little."), 429

    body = request.get_json(silent=True) or {}
    message = body.get("message")
    session_id = body.get("session_id") or str(uuid.uuid4())

    # ---- input validation ----
    if not isinstance(message, str) or not message.strip():
        return jsonify(error="Message cannot be empty."), 400
    message = message.strip()
    if len(message) > MAX_INPUT_CHARS:
        return jsonify(error=f"Message too long (max {MAX_INPUT_CHARS} characters)."), 400
    if not isinstance(session_id, str) or len(session_id) > 64:
        return jsonify(error="Invalid session id."), 400

    history = get_history(session_id)
    history.append({"role": "user", "content": message})
    messages = [{"role": "system", "content": SYSTEM_PROMPT}] + history

    def generate():
        yield sse("meta", {"session_id": session_id})
        reply_parts: list[str] = []
        last_exc: Exception | None = None

        # try main model first, then fallback model (resilience)
        for model_name in [MODEL, FALLBACK_MODEL]:
            if not model_name:
                continue
            try:
                log.info("chat session=%s model=%s", session_id[:8], model_name)
                stream = client.chat.completions.create(
                    model=model_name, messages=messages, temperature=TEMPERATURE, stream=True
                )
                for chunk in stream:
                    if not chunk.choices:
                        continue
                    delta = chunk.choices[0].delta.content
                    if delta:
                        reply_parts.append(delta)
                        yield sse("token", {"text": delta})
                last_exc = None
                break
            except (RateLimitError, APIStatusError, APITimeoutError, APIConnectionError) as exc:
                last_exc = exc
                log.warning("model %s failed: %s", model_name, exc)
                if reply_parts:  # already streamed partial text; don't restart
                    break
                if isinstance(exc, AuthenticationError):
                    break
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                log.exception("unexpected error")
                break

        if last_exc is not None and not reply_parts:
            msg, status = friendly_error(last_exc)
            yield sse("error", {"message": msg, "status": status})
            return

        reply = "".join(reply_parts)
        history.append({"role": "assistant", "content": reply})
        save_history(session_id, history)
        yield sse("done", {"length": len(reply)})

    headers = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
    return Response(stream_with_context(generate()), mimetype="text/event-stream", headers=headers)


@app.post("/api/clear")
def clear():
    body = request.get_json(silent=True) or {}
    session_id = body.get("session_id")
    if isinstance(session_id, str):
        with _sessions_lock:
            _sessions.pop(session_id, None)
    return jsonify(status="cleared")


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=PORT, debug=False, threaded=True)
