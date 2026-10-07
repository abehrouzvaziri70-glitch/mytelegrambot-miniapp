import os
import hmac
import hashlib
from datetime import datetime
from typing import Optional
from urllib.parse import parse_qsl

import httpx
from fastapi import FastAPI, HTTPException, Header, Depends
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel


SUPABASE_URL = os.getenv("SUPABASE_URL", "").rstrip("/")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")

app = FastAPI(
    title="MyTelegramBot API",
    version="1.1.0",
)

# GitHub Pages Mini App
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://abehrouzvaziri70-glitch.github.io",
    ],
    allow_credentials=False,
    allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)


# ---------------------------------------------------------
# Supabase
# ---------------------------------------------------------

def supabase_headers():
    if not SUPABASE_URL or not SUPABASE_KEY:
        raise HTTPException(
            status_code=500,
            detail="Supabase environment variables are not configured."
        )

    return {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
    }


async def sb_request(method: str, table: str, **kwargs):
    url = f"{SUPABASE_URL}/rest/v1/{table}"

    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.request(
            method,
            url,
            headers=supabase_headers(),
            **kwargs
        )

    if response.status_code >= 400:
        raise HTTPException(
            status_code=response.status_code,
            detail=response.text
        )

    if not response.content:
        return None

    return response.json()


# ---------------------------------------------------------
# Telegram Mini App authentication
# ---------------------------------------------------------

def validate_telegram_init_data(init_data: str) -> int:
    if not TELEGRAM_BOT_TOKEN:
        raise HTTPException(
            status_code=500,
            detail="TELEGRAM_BOT_TOKEN is not configured."
        )

    if not init_data:
        raise HTTPException(
            status_code=401,
            detail="Telegram initData is missing."
        )

    try:
        parsed = dict(parse_qsl(init_data, keep_blank_values=True))
    except Exception:
        raise HTTPException(
            status_code=401,
            detail="Invalid Telegram initData."
        )

    received_hash = parsed.pop("hash", None)

    if not received_hash:
        raise HTTPException(
            status_code=401,
            detail="Telegram initData hash is missing."
        )

    data_check_string = "\n".join(
        f"{key}={value}"
        for key, value in sorted(parsed.items())
    )

    secret_key = hmac.new(
        b"WebAppData",
        TELEGRAM_BOT_TOKEN.encode(),
        hashlib.sha256
    ).digest()

    calculated_hash = hmac.new(
        secret_key,
        data_check_string.encode(),
        hashlib.sha256
    ).hexdigest()

    if not hmac.compare_digest(calculated_hash, received_hash):
        raise HTTPException(
            status_code=401,
            detail="Invalid Telegram initData."
        )

    user_data = parsed.get("user")

    if not user_data:
        raise HTTPException(
            status_code=401,
            detail="Telegram user data is missing."
        )

    try:
        import json
        user = json.loads(user_data)
        telegram_user_id = int(user["id"])
    except Exception:
        raise HTTPException(
            status_code=401,
            detail="Invalid Telegram user data."
        )

    return telegram_user_id


async def get_current_user(
    x_telegram_init_data: Optional[str] = Header(
        default=None,
        alias="X-Telegram-Init-Data"
    )
) -> int:
    return validate_telegram_init_data(x_telegram_init_data or "")


# ---------------------------------------------------------
# Models
# ---------------------------------------------------------

class TaskCreate(BaseModel):
    title: str
    description: str = ""
    priority: str = "normal"
    due_date: Optional[str] = None
    status: str = "pending"


class TaskUpdate(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    priority: Optional[str] = None
    due_date: Optional[str] = None
    status: Optional[str] = None


class ReminderCreate(BaseModel):
    title: str
    remind_at: str


class ReminderUpdate(BaseModel):
    title: Optional[str] = None
    remind_at: Optional[str] = None
    done: Optional[int] = None


# ---------------------------------------------------------
# Basic
# ---------------------------------------------------------

@app.get("/")
async def root():
    return {
        "ok": True,
        "service": "MyTelegramBot API",
        "version": "1.1.0"
    }


@app.get("/health")
async def health():
    return {"ok": True}


# ---------------------------------------------------------
# Tasks
# ---------------------------------------------------------

@app.get("/tasks")
async def list_tasks(user_id: int = Depends(get_current_user)):
    params = {
        "user_id": f"eq.{user_id}",
        "select": "*",
        "order": "id.asc",
    }

    return await sb_request(
        "GET",
        "tasks",
        params=params
    )


@app.post("/tasks")
async def create_task(
    task: TaskCreate,
    user_id: int = Depends(get_current_user)
):
    payload = task.model_dump()

    payload["user_id"] = user_id
    payload["created_at"] = datetime.now().isoformat()
    payload["due_notified"] = 0

    return await sb_request(
        "POST",
        "tasks",
        params={"select": "*"},
        json=payload,
    )


@app.patch("/tasks/{task_id}")
async def update_task(
    task_id: int,
    task: TaskUpdate,
    user_id: int = Depends(get_current_user)
):
    payload = {
        k: v
        for k, v in task.model_dump().items()
        if v is not None
    }

    if payload.get("status") == "done":
        payload["completed_at"] = datetime.now().isoformat()

    elif "status" in payload and payload["status"] != "done":
        payload["completed_at"] = None

    # مهم:
    # فقط کار متعلق به همین کاربر قابل ویرایش است.
    params = {
        "id": f"eq.{task_id}",
        "user_id": f"eq.{user_id}",
        "select": "*",
    }

    return await sb_request(
        "PATCH",
        "tasks",
        params=params,
        json=payload,
    )


@app.delete("/tasks/{task_id}")
async def delete_task(
    task_id: int,
    user_id: int = Depends(get_current_user)
):
    params = {
        "id": f"eq.{task_id}",
        "user_id": f"eq.{user_id}",
    }

    await sb_request(
        "DELETE",
        "tasks",
        params=params,
    )

    return {"ok": True}


# ---------------------------------------------------------
# Reminders
# ---------------------------------------------------------

@app.get("/reminders")
async def list_reminders(
    user_id: int = Depends(get_current_user)
):
    params = {
        "user_id": f"eq.{user_id}",
        "select": "*",
        "order": "id.asc",
    }

    return await sb_request(
        "GET",
        "reminders",
        params=params
    )


@app.post("/reminders")
async def create_reminder(
    reminder: ReminderCreate,
    user_id: int = Depends(get_current_user)
):
    payload = reminder.model_dump()

    payload["user_id"] = user_id
    payload["done"] = 0
    payload["created_at"] = datetime.now().isoformat()

    return await sb_request(
        "POST",
        "reminders",
        params={"select": "*"},
        json=payload,
    )


@app.patch("/reminders/{reminder_id}")
async def update_reminder(
    reminder_id: int,
    reminder: ReminderUpdate,
    user_id: int = Depends(get_current_user)
):
    payload = {
        k: v
        for k, v in reminder.model_dump().items()
        if v is not None
    }

    params = {
        "id": f"eq.{reminder_id}",
        "user_id": f"eq.{user_id}",
        "select": "*",
    }

    return await sb_request(
        "PATCH",
        "reminders",
        params=params,
        json=payload,
    )


@app.delete("/reminders/{reminder_id}")
async def delete_reminder(
    reminder_id: int,
    user_id: int = Depends(get_current_user)
):
    params = {
        "id": f"eq.{reminder_id}",
        "user_id": f"eq.{user_id}",
    }

    await sb_request(
        "DELETE",
        "reminders",
        params=params,
    )

    return {"ok": True}