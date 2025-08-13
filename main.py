import os, time, httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from typing import Optional, List

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "").strip()
FLOWISE_API_URL = os.getenv("FLOWISE_API_URL", "").strip()
FLOWISE_API_KEY = os.getenv("FLOWISE_API_KEY", "").strip()
TIMEOUT_SECONDS = int(os.getenv("TIMEOUT_SECONDS", "60"))
ALLOWED_USER_IDS = [s.strip() for s in os.getenv("ALLOWED_USER_IDS", "").split(",") if s.strip()]

TELEGRAM_API = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"
app = FastAPI(title="Flowise ↔ Telegram Bridge")

memory_buffer = {}
last_seen = {}

class FlowiseResponse(BaseModel):
    text: Optional[str] = None
    output: Optional[str] = None
    data: Optional[str] = None

async def send_message(chat_id: int, text: str):
    async with httpx.AsyncClient(timeout=30) as client:
        await client.post(f"{TELEGRAM_API}/sendMessage", json={"chat_id": chat_id, "text": text})

def allowed_user(user_id: int) -> bool:
    if not ALLOWED_USER_IDS:
        return True
    return str(user_id) in ALLOWED_USER_IDS

def rate_limited(user_id: int, min_interval: float = 1.5) -> bool:
    now = time.time()
    if user_id in last_seen and (now - last_seen[user_id]) < min_interval:
        return True
    last_seen[user_id] = now
    return False

@app.get("/health")
async def health():
    return {"ok": True, "flowise_url": bool(FLOWISE_API_URL)}

@app.post("/telegram/webhook")
async def telegram_webhook(req: Request):
    body = await req.json()
    message = body.get("message") or body.get("edited_message")
    if not message:
        return JSONResponse({"ok": True, "ignored": True})

    chat_id = message.get("chat", {}).get("id")
    user_id = message.get("from", {}).get("id")
    text = message.get("text", "") or ""

    if not allowed_user(user_id):
        return JSONResponse({"ok": True, "ignored": True})

    if rate_limited(user_id):
        return JSONResponse({"ok": True, "rate_limited": True})

    if text.startswith("/"):
        if text.startswith("/start"):
            await send_message(chat_id, "Olá! Sou seu assistente IA integrado ao Flowise.")
            return {"ok": True}
        if text.startswith("/novo"):
            memory_buffer.pop(chat_id, None)
            await send_message(chat_id, "Contexto resetado.")
            return {"ok": True}
        if text.startswith("/status"):
            await send_message(chat_id, "✅ Online.")
            return {"ok": True}

    history: List[str] = memory_buffer.get(chat_id, [])[-8:]
    payload = {
        "question": text,
        "overrideConfig": {
            "sessionId": str(chat_id),
            "metadata": {"history": history}
        }
    }

    headers = {"Content-Type": "application/json"}
    if FLOWISE_API_KEY:
        headers["Authorization"] = f"Bearer {FLOWISE_API_KEY}"

    try:
        async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS) as client:
            resp = await client.post(FLOWISE_API_URL, headers=headers, json=payload)
            resp.raise_for_status()
            data = resp.json()
    except Exception as e:
        await send_message(chat_id, "⚠️ Erro ao falar com o motor Flowise.")
        print("Erro Flowise:", e)
        return JSONResponse({"ok": False})

    model = FlowiseResponse(**data)
    answer = model.text or model.output or model.data or "Sem resposta."

    history.append(f"U:{text}")
    history.append(f"A:{answer}")
    memory_buffer[chat_id] = history[-16:]

    await send_message(chat_id, answer[:4096])
    return {"ok": True}

@app.get("/")
async def root():
    return {"message": "Bridge ativo."}
