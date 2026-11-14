"""Authentication routes: register / login / logout / me. Sessions are httpOnly cookies."""

import uuid

from fastapi import APIRouter, Depends, HTTPException, Response, status

from lib.db import db
from lib.security import (
    clear_session_cookie,
    create_session_token,
    current_user,
    hash_password,
    set_session_cookie,
    verify_password,
)
from models.users import LoginIn, UserCreate, UserOut, utcnow

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/register", response_model=UserOut, status_code=status.HTTP_201_CREATED)
async def register(input: UserCreate, response: Response):
    email = input.email.lower()
    if await db.users.find_one({"email": email}):
        raise HTTPException(status_code=409, detail="An account with this email already exists")
    user = {
        "id": str(uuid.uuid4()),
        "name": input.name.strip(),
        "email": email,
        "role": "citizen",  # role is stamped server-side; never accepted from the client
        "password_hash": hash_password(input.password),
        "created_at": utcnow(),
    }
    await db.users.insert_one(user)
    set_session_cookie(response, create_session_token(user["id"]))
    return UserOut(**user)


@router.post("/login", response_model=UserOut)
async def login(input: LoginIn, response: Response):
    user = await db.users.find_one({"email": input.email.lower()})
    if not user or not verify_password(input.password, user["password_hash"]):
        raise HTTPException(status_code=401, detail="Invalid email or password")
    set_session_cookie(response, create_session_token(user["id"]))
    return UserOut(**user)


@router.post("/logout")
async def logout(response: Response):
    clear_session_cookie(response)
    return {"ok": True}


@router.get("/me", response_model=UserOut | None)
async def me(user: dict | None = Depends(current_user)):
    if not user:
        return None
    return UserOut(**user)
