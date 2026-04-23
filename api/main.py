import re
import os
import asyncio
from pathlib import Path
from contextlib import asynccontextmanager

from fastapi import FastAPI, Depends, HTTPException, Request
from fastapi.responses import FileResponse, Response

import sentry_sdk
from sentry_sdk.integrations.fastapi import FastApiIntegration
from sqlalchemy.orm import Session
from pydantic import BaseModel

from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded

# Internal absolute imports for Vercel
from api.database import SessionLocal
import api.database
import api.cache
from api.worker import job_fetch_and_send, send_telegram # Import your new Telegram sender

# --- Initialization ---

sentry_sdk.init(
    dsn=os.getenv("SENTRY_DSN"),
    integrations=[FastApiIntegration()],
    traces_sample_rate=0.2,
    enable_tracing=True,
)

limiter = Limiter(key_func=get_remote_address)

@asynccontextmanager
async def lifespan(app: FastAPI):
    print("Vercel instance starting...")
    yield
    print("Vercel instance shutting down...")

app = FastAPI(lifespan=lifespan)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

STATIC_DIR = Path(__file__).parent / "static"
PHONE_PATTERN = re.compile(r"^\+[1-9]\d{7,14}$")

# --- Models & Dependencies ---

class RegistrationRequest(BaseModel):
    name: str
    phone: str

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# --- Helper Functions ---

def normalize_name(name: str) -> str:
    cleaned = (name or "").strip()
    if len(cleaned) < 2:
        raise HTTPException(status_code=400, detail="Please enter your full name.")
    return cleaned

def normalize_phone(phone: str) -> str:
    # Keeps the website registration working for leads
    stripped = re.sub(r"[\s\-\(\)]", "", (phone or "").strip())
    if stripped and not stripped.startswith("+") and stripped.isdigit():
        stripped = f"+{stripped}"
    if not PHONE_PATTERN.fullmatch(stripped):
        raise HTTPException(status_code=400, detail="Use an international phone number.")
    return stripped

# --- Routes ---

@app.get("/", include_in_schema=False)
def serve_landing_page():
    index_file = STATIC_DIR / "index.html"
    if not index_file.exists():
        return Response(content="Error: index.html not found", status_code=404)
    return FileResponse(index_file)

@app.get("/api/cron")
def trigger_daily_research(request: Request):
    auth_header = request.headers.get("Authorization")
    expected_auth = f"Bearer {os.getenv('CRON_SECRET')}"
    
    if not auth_header or auth_header != expected_auth:
        raise HTTPException(status_code=401, detail="Unauthorized")

    try:
        job_fetch_and_send()
        return {"status": "success", "message": "Daily updates sent via Telegram."}
    except Exception as e:
        return {"status": "error", "message": str(e)}

@app.post("/register")
@limiter.limit("5/minute")
def register(
    request: Request,
    payload: RegistrationRequest,
    db: Session = Depends(get_db)
):
    # This remains for website lead capture
    name = normalize_name(payload.name)
    phone = normalize_phone(payload.phone)

    lead = db.query(api.database.RegistrationLead).filter(
        api.database.RegistrationLead.phone_number == phone
    ).first()

    if lead:
        lead.full_name = name
    else:
        db.add(api.database.RegistrationLead(full_name=name, phone_number=phone))

    db.commit()
    return {"status": "success", "message": "You're registered. Now find our bot on Telegram!"}

@app.post("/webhook")
async def webhook(request: Request, db: Session = Depends(get_db)):
    """Handles incoming JSON data from Telegram."""
    data = await request.json()
    
    # Extract message details from Telegram's JSON structure
    if "message" not in data or "text" not in data["message"]:
        return {"status": "ignored"}

    chat_id = str(data["message"]["chat"]["id"])
    incoming = data["message"]["text"].strip()

    # In this Telegram version, the 'phone_number' column stores the Chat ID
    user = db.query(api.database.User).filter(
        api.database.User.phone_number == chat_id
    ).first()

    # Auto-register new users who find the bot directly
    if not user:
        user = api.database.User(phone_number=chat_id, current_step="IDLE")
        db.add(user)
        db.commit()

    cached = api.cache.get_user_cache(chat_id)
    state = cached['step'] if cached else user.current_step

    # --- Logic Flow (State Machine) ---

    if state == "IDLE":
        msg = "🚀 <b>Welcome to UpToDate!</b>\n\nWhat topic would you like to follow? (e.g. 'Pharmacy AI', 'Crypto', 'Lagos Business')"
        if send_telegram(chat_id, msg):
            user.current_step = "AWAITING_TOPIC"
            db.commit()
            api.cache.set_user_cache(chat_id, {"step": "AWAITING_TOPIC"})
        return {"status": "ok"}

    if state == "AWAITING_TOPIC":
        if len(incoming) < 2:
            send_telegram(chat_id, "Please enter a valid topic to continue.")
            return {"status": "ok"}

        user.notification_topic = incoming
        user.current_step = "CONFIRMED"
        db.commit()
        api.cache.set_user_cache(chat_id, {"step": "CONFIRMED"})

        confirm_msg = f"✅ Got it! You'll receive updates on <b>{incoming}</b> every 12 hours.\n\nReply 'change' anytime to update your topic."
        send_telegram(chat_id, confirm_msg)
        return {"status": "ok"}

    if state == "CONFIRMED":
        if incoming.lower() == "change":
            user.current_step = "AWAITING_TOPIC"
            db.commit()
            api.cache.set_user_cache(chat_id, {"step": "AWAITING_TOPIC"})
            send_telegram(chat_id, "What is your new research topic?")
        else:
            status_msg = f"You are currently following: <b>{user.notification_topic}</b>.\n\nReply 'change' to update it."
            send_telegram(chat_id, status_msg)
        return {"status": "ok"}

    return {"status": "error"}