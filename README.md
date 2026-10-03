# 🤖 Nova — AI Chatbot (Day 2 & Day 3)

A production-minded AI chatbot with a Flask backend and a clean web UI. It streams answers token-by-token, remembers the conversation, renders Markdown, and handles errors gracefully.

## Features

**Day 2 (core)**
- User input + AI response
- Real API integration (OpenAI-compatible; default provider **Groq**, free tier)
- Loading state (typing indicator + disabled input)
- Error handling (friendly messages for bad key, rate limit, timeout, no internet)
- Clean, responsive UI
- Defined **system prompt** (role: "Nova", tech/study assistant, multilingual)

**Day 3 (improvements)**
- Conversation history & context/memory (server-side per session, trimmed to last N messages)
- Clear chat
- Markdown responses (sanitized with DOMPurify)
- Input validation (empty, max length, session id) on client **and** server
- Better UI (streaming, copy button, suggestion chips, char counter)
- Voice input (mic) and voice output (read aloud)

**Production-oriented extras**
- Streaming via Server-Sent Events
- Fallback model if the main model fails/rate-limits
- Per-IP rate limiting, request timeout, logging, `/api/health`
- API key only in `.env` (never in frontend or Git)
- Provider-agnostic: change `LLM_BASE_URL`/`LLM_MODEL` to switch providers

## Setup

```bash
git clone <your-repo-url> && cd <repo>
python -m venv venv && source venv/bin/activate   # Windows: venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env        # then paste your key into LLM_API_KEY
python server.py            # open http://localhost:5000
```

Get a free key (no credit card): https://console.groq.com/keys

Production run: `gunicorn -w 1 --threads 8 -b 0.0.0.0:5000 server:app`

## Architecture

```
Browser (static/index.html)
   │  POST /api/chat {message, session_id}
   ▼
Flask (server.py)
   ├─ validate input → rate-limit → load session history
   ├─ build messages = [system prompt] + history + new user message
   ├─ call LLM (OpenAI SDK → Groq) with stream=True  (fallback model on failure)
   └─ stream tokens back as SSE → save assistant reply to history
```

| Endpoint | Purpose |
|---|---|
| `GET /` | Chat UI |
| `POST /api/chat` | Send message, receive streamed reply |
| `POST /api/clear` | Delete session history |
| `GET /api/health` | Health/config check |

History is stored in memory; for multiple servers use Redis or a database.

## What I learned
- How chat APIs work: the model is stateless, so *you* resend history each time.
- System prompts shape role, tone and language.
- Streaming (SSE) makes the app feel much faster.
- Never expose API keys in frontend code; keep them in environment variables.
- Always sanitize Markdown/HTML from an LLM before rendering.

## Problems & solutions
| Problem | Solution |
|---|---|
| Free-tier rate limits (429) | Per-IP limit, friendly message, fallback model |
| Long chats grow prompt size/cost | Keep only last `MAX_HISTORY_MESSAGES` |
| Slow-feeling responses | Streaming tokens |
| XSS risk from rendered Markdown | DOMPurify |
| Provider lock-in | OpenAI-compatible client + env config |

## Screenshots / Demo
Add your screenshots to a `screenshots/` folder and link them here.
