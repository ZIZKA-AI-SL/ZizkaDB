"""
Public community board — posts, replies, image uploads. No login required.
"""

from __future__ import annotations

import json
import logging
import os
import re
import uuid
from pathlib import Path

from fastapi import APIRouter, File, Query, Request, UploadFile
from services.exceptions import bad_request, not_found
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from api.utils import client_ip
from db.connection import get_pool
from services.rate_limiter import (
    InMemoryStorage,
    RateLimiter,
    RedisStorage,
    SlidingWindowStrategy,
    check_rate_fail_open,
)

router = APIRouter()
log = logging.getLogger(__name__)

UPLOAD_DIR = Path(os.getenv("COMMUNITY_UPLOAD_DIR", "/data/community_uploads"))
MAX_UPLOAD_BYTES = 3 * 1024 * 1024
ALLOWED_EXT = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
CATEGORIES = {"question", "experience", "showcase"}


RATE_WINDOW_SEC = 3600
RATE_MAX_POSTS = 10

community_limiter = RateLimiter(
    limit=RATE_MAX_POSTS,
    window_sec=RATE_WINDOW_SEC,
    storage=RedisStorage(key_prefix="ratelimit:community"),
    strategy=SlidingWindowStrategy(),
    detail="Too many posts. Try again later."
)

_community_fallback_storage = InMemoryStorage()

community_limiter_fallback = RateLimiter(
    limit=RATE_MAX_POSTS,
    window_sec=RATE_WINDOW_SEC,
    storage=_community_fallback_storage,
    strategy=SlidingWindowStrategy(),
    detail="Too many posts. Try again later.",
)


async def _check_community_rate(key: str) -> None:
    await check_rate_fail_open(
        community_limiter,
        community_limiter_fallback,
        key,
        context="community rate limit",
    )


class CreatePostBody(BaseModel):
    author_name: str = Field(min_length=1, max_length=120)
    author_email: str | None = Field(default=None, max_length=255)
    category: str = "question"
    title: str = Field(min_length=3, max_length=300)
    body: str = Field(min_length=10, max_length=12000)
    image_urls: list[str] = Field(default_factory=list)
    website: str | None = None  # honeypot — must be empty


class CreateReplyBody(BaseModel):
    author_name: str = Field(min_length=1, max_length=120)
    author_email: str | None = Field(default=None, max_length=255)
    body: str = Field(min_length=2, max_length=8000)
    website: str | None = None



def _public_base_url(request: Request) -> str:
    env = os.getenv("PUBLIC_API_URL", "").rstrip("/")
    if env:
        return env
    scheme = request.headers.get("x-forwarded-proto", request.url.scheme)
    host = request.headers.get("host", "db.zizka.ai")
    return f"{scheme}://{host}"


@router.get("/posts")
async def list_posts(
    request: Request,
    category: str | None = None,
    limit: int = Query(default=40, ge=1, le=100),
):
    pool = get_pool()
    params: list[object] = []
    where = ""
    if category and category in CATEGORIES:
        where = "WHERE category = $1"
        params.append(category)

    limit_idx = len(params) + 1
    params.append(limit)

    rows = await pool.fetch(
        f"""
        SELECT post_id, author_name, category, title,
               LEFT(body, 280) AS excerpt,
               image_urls, reply_count, created_at
        FROM community_posts
        {where}
        ORDER BY created_at DESC
        LIMIT ${limit_idx}
        """,
        *params,
    )
    base = _public_base_url(request)
    return [
        {
            "id":           str(r["post_id"]),
            "author_name":  r["author_name"],
            "category":     r["category"],
            "title":        r["title"],
            "excerpt":      r["excerpt"],
            "image_urls":   _normalize_image_urls(r["image_urls"], base),
            "reply_count":  r["reply_count"],
            "created_at":   r["created_at"].isoformat(),
        }
        for r in rows
    ]


@router.get("/posts/{post_id}")
async def get_post(post_id: str, request: Request):
    pool = get_pool()
    try:
        pid = uuid.UUID(post_id)
    except ValueError:
        raise not_found("Post not found")

    post = await pool.fetchrow(
        """
        SELECT post_id, author_name, author_email, category, title, body,
               image_urls, reply_count, created_at
        FROM community_posts WHERE post_id = $1
        """,
        pid,
    )
    if not post:
        raise not_found("Post not found")

    replies = await pool.fetch(
        """
        SELECT reply_id, author_name, body, created_at
        FROM community_replies
        WHERE post_id = $1
        ORDER BY created_at ASC
        """,
        pid,
    )
    base = _public_base_url(request)
    return {
        "id":           str(post["post_id"]),
        "author_name":  post["author_name"],
        "category":     post["category"],
        "title":        post["title"],
        "body":         post["body"],
        "image_urls":   _normalize_image_urls(post["image_urls"], base),
        "reply_count":  post["reply_count"],
        "created_at":   post["created_at"].isoformat(),
        "replies": [
            {
                "id":          str(r["reply_id"]),
                "author_name": r["author_name"],
                "body":        r["body"],
                "created_at":  r["created_at"].isoformat(),
            }
            for r in replies
        ],
    }


@router.post("/posts", status_code=201)
async def create_post(body: CreatePostBody, request: Request):
    if body.website:
        raise bad_request("Invalid submission")
    if body.category not in CATEGORIES:
        raise bad_request("Invalid category")
    await _check_community_rate(client_ip(request))
    pool = get_pool()
    urls = [u for u in body.image_urls if isinstance(u, str) and u.startswith("/v1/community/media/")][:6]

    row = await pool.fetchrow(
        """
        INSERT INTO community_posts (
            author_name, author_email, category, title, body, image_urls
        )
        VALUES ($1, $2, $3, $4, $5, $6::jsonb)
        RETURNING post_id, created_at
        """,
        body.author_name.strip(),
        body.author_email.lower().strip() if body.author_email else None,
        body.category,
        body.title.strip(),
        body.body.strip(),
        json.dumps(urls),
    )
    return {"id": str(row["post_id"]), "created_at": row["created_at"].isoformat()}


@router.post("/posts/{post_id}/replies", status_code=201)
async def create_reply(post_id: str, body: CreateReplyBody, request: Request):
    if body.website:
        raise bad_request("Invalid submission")
    await _check_community_rate(client_ip(request))
    pool = get_pool()
    try:
        pid = uuid.UUID(post_id)
    except ValueError:
        raise not_found("Post not found")

    exists = await pool.fetchval(
        "SELECT 1 FROM community_posts WHERE post_id = $1", pid,
    )
    if not exists:
        raise not_found("Post not found")

    row = await pool.fetchrow(
        """
        INSERT INTO community_replies (post_id, author_name, author_email, body)
        VALUES ($1, $2, $3, $4)
        RETURNING reply_id, created_at
        """,
        pid,
        body.author_name.strip(),
        body.author_email.lower().strip() if body.author_email else None,
        body.body.strip(),
    )
    await pool.execute(
        """
        UPDATE community_posts
        SET reply_count = reply_count + 1, updated_at = NOW()
        WHERE post_id = $1
        """,
        pid,
    )
    return {"id": str(row["reply_id"]), "created_at": row["created_at"].isoformat()}


def _is_valid_image(data: bytes, ext: str) -> bool:
    if len(data) < 12:
        return False
    if ext == ".png":
        return data.startswith(b"\x89PNG\r\n\x1a\n")
    if ext in (".jpg", ".jpeg"):
        return data.startswith(b"\xff\xd8\xff")
    if ext == ".gif":
        return data.startswith((b"GIF87a", b"GIF89a"))
    if ext == ".webp":
        return data.startswith(b"RIFF") and data[8:12] == b"WEBP"
    return False


@router.post("/upload")
async def upload_image(request: Request, file: UploadFile = File(...)):
    await _check_community_rate(client_ip(request) + ":upload")

    if not file.filename:
        raise bad_request("No file")

    ext = Path(file.filename).suffix.lower()
    if ext not in ALLOWED_EXT:
        raise bad_request("Use PNG, JPG, WEBP, or GIF")

    data = await file.read()
    if len(data) > MAX_UPLOAD_BYTES:
        raise bad_request("Image must be under 3MB")
    if not _is_valid_image(data, ext):
        raise bad_request("Invalid image file format")

    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    name = f"{uuid.uuid4().hex}{ext}"
    path = UPLOAD_DIR / name
    path.write_bytes(data)

    return {"url": f"/v1/community/media/{name}"}


@router.get("/media/{filename}")
async def serve_media(filename: str):
    safe = re.sub(r"[^a-zA-Z0-9._-]", "", filename)
    if not safe or safe != filename:
        raise not_found("Not found")
    path = UPLOAD_DIR / safe
    if not path.is_file():
        raise not_found("Not found")
    media = {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".webp": "image/webp",
        ".gif": "image/gif",
    }
    headers = {
        "X-Content-Type-Options": "nosniff",
        "Content-Security-Policy": "default-src 'none'",
    }
    return FileResponse(
        path,
        media_type=media.get(path.suffix.lower(), "application/octet-stream"),
        headers=headers,
    )


def _normalize_image_urls(raw, base: str) -> list[str]:
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            return []
    if not isinstance(raw, list):
        return []
    out = []
    for u in raw:
        if not isinstance(u, str):
            continue
        if u.startswith("http"):
            out.append(u)
        elif u.startswith("/"):
            out.append(f"{base}{u}")
    return out
