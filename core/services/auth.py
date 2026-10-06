import asyncio
import hashlib
import secrets
import os
import logging
import jwt
import bcrypt
import smtplib
from typing import Literal
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timedelta, timezone
from db.connection import get_pool

log = logging.getLogger(__name__)

def _required_secret(env_var: str, dev_fallback: str) -> str:
    """
    Resolve a signing secret from the environment.

    In development this falls back to a fixed insecure string so local
    self-host works without any env setup. In production (ENV !=
    "development"), the fallback string is visible in this public
    repository — using it to sign JWTs would let anyone forge a valid
    auth token. Refuse to start rather than silently sign tokens with a
    known secret.
    """
    secret = os.getenv(env_var)
    if secret:
        return secret
    if os.getenv("ENV", "development") != "development":
        raise RuntimeError(
            f"{env_var} must be set in production. Refusing to sign tokens "
            f"with the hardcoded development fallback, since that value is "
            f"visible in the public source code."
        )
    return dev_fallback


JWT_SECRET = _required_secret("JWT_SECRET", "dev-secret")
JWT_REFRESH_SECRET = _required_secret("JWT_REFRESH_SECRET", "dev-refresh-secret")
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24 * 7  # 7 days
REFRESH_TOKEN_EXPIRE_DAYS = 30


# ─────────────────────────────────────────
# API KEY AUTH
# ─────────────────────────────────────────

def generate_api_key() -> tuple[str, str, str]:
    """Returns (raw_key, key_hash, prefix). Store hash, show raw once."""
    raw = f"zizkadb_live_{secrets.token_urlsafe(32)}"
    key_hash = hashlib.sha256(raw.encode()).hexdigest()
    prefix = raw[:16]
    return raw, key_hash, prefix


async def verify_api_key(raw_key: str) -> dict | None:
    """Returns tenant info if valid, None if invalid.

    Legacy managed-cloud keys (``agdb_live_...``) and new keys
    (``zizkadb_live_...``) both authenticate the same way.
    """
    key_hash = hashlib.sha256(raw_key.encode()).hexdigest()
    pool = get_pool()

    row = await pool.fetchrow(
        """
        SELECT ak.tenant_id, ak.key_id, ak.agent_id, ak.revoked
        FROM api_keys ak
        WHERE ak.key_hash = $1
        """,
        key_hash,
    )

    if not row or row["revoked"]:
        return None

    # Update last_used async (fire and forget)
    await pool.execute(
        "UPDATE api_keys SET last_used = NOW() WHERE key_hash = $1",
        key_hash,
    )

    return {
        "tenant_id": str(row["tenant_id"]),
        "key_id": str(row["key_id"]),
        "agent_id": row["agent_id"],
    }


# ─────────────────────────────────────────
# PASSWORDLESS OTP AUTH
# ─────────────────────────────────────────

async def email_exists(email: str) -> bool:
    email = email.lower().strip()
    pool = get_pool()
    row = await pool.fetchval("SELECT 1 FROM users WHERE email = $1", email)
    return row is not None


async def request_otp(email: str) -> None:
    email = email.lower().strip()
    otp = str(secrets.randbelow(900_000) + 100_000)
    otp_hash = bcrypt.hashpw(otp.encode(), bcrypt.gensalt()).decode()
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=15)

    pool = get_pool()

    # Invalidate previous OTPs
    await pool.execute(
        "UPDATE auth_otps SET used = TRUE WHERE email = $1 AND used = FALSE",
        email,
    )

    await pool.execute(
        """
        INSERT INTO auth_otps (email, otp_hash, expires_at)
        VALUES ($1, $2, $3)
        """,
        email, otp_hash, expires_at,
    )

    await _send_otp_email(email, otp)


async def _save_signup_consent(
    *,
    db,
    user_id: str,
    consent_at: datetime,
    marketing_consent: bool,
) -> None:
    """Persist signup consents; self-heal if columns are missing."""
    try:
        await db.execute(
            """
            UPDATE users SET
                gdpr_consent_at = $2,
                marketing_consent = $3
            WHERE user_id = $1::uuid
            """,
            user_id,
            consent_at,
            marketing_consent,
        )
        return
    except Exception as e:
        log.warning("consent update retry for user %s after schema sync: %s", user_id, e)

    # In case API started before migration touched this DB, self-heal once.
    await db.execute(
        """
        ALTER TABLE users ADD COLUMN IF NOT EXISTS gdpr_consent_at TIMESTAMPTZ;
        ALTER TABLE users ADD COLUMN IF NOT EXISTS marketing_consent BOOLEAN NOT NULL DEFAULT FALSE;
        ALTER TABLE users ADD COLUMN IF NOT EXISTS marketing_consent_at TIMESTAMPTZ;
        """
    )
    await db.execute(
        """
        UPDATE users SET
            gdpr_consent_at = $2,
            marketing_consent = $3
        WHERE user_id = $1::uuid
        """,
        user_id,
        consent_at,
        marketing_consent,
    )


AuthIntent = Literal["login", "signup"]

_LOGIN_NO_ACCOUNT = (
    "No account found for this email. Create an account to get started."
)
_SIGNUP_ALREADY_REGISTERED = (
    "This email is already registered. Please sign in instead."
)
_SIGNUP_GDPR_REQUIRED = "GDPR consent is required to create an account."


async def verify_otp(
    email: str,
    otp: str,
    *,
    intent: AuthIntent = "login",
    gdpr_consent: bool | None = None,
    marketing_consent: bool | None = None,
) -> dict:
    email = email.lower().strip()
    pool = get_pool()

    row = await pool.fetchrow(
        """
        SELECT otp_id, otp_hash, attempts, max_attempts FROM auth_otps
        WHERE email = $1 AND used = FALSE AND expires_at > NOW()
        ORDER BY created_at DESC LIMIT 1
        """,
        email,
    )

    if not row:
        raise ValueError("OTP expired or not found")

    valid = bcrypt.checkpw(otp.encode(), row["otp_hash"].encode())
    if not valid:
        attempts = (row["attempts"] if "attempts" in row else 0) + 1
        max_attempts = row["max_attempts"] if "max_attempts" in row else 5
        if attempts >= max_attempts:
            await pool.execute(
                "UPDATE auth_otps SET used = TRUE, attempts = $2 WHERE otp_id = $1",
                row["otp_id"], attempts,
            )
            raise ValueError("Too many failed attempts. Please request a new code.")
        await pool.execute(
            "UPDATE auth_otps SET attempts = $2 WHERE otp_id = $1",
            row["otp_id"], attempts,
        )
        raise ValueError("Invalid OTP")

    user_exists = await email_exists(email)

    if intent == "login":
        if not user_exists:
            raise ValueError(_LOGIN_NO_ACCOUNT)
    elif intent == "signup":
        if user_exists:
            raise ValueError(_SIGNUP_ALREADY_REGISTERED)
        if not gdpr_consent:
            raise ValueError(_SIGNUP_GDPR_REQUIRED)
    else:
        raise ValueError("Invalid authentication intent")

    async with pool.acquire() as conn:
        async with conn.transaction():
            if intent == "login":
                user = await conn.fetchrow(
                    """
                    SELECT user_id, email, tenant_id
                    FROM users WHERE email = $1
                    """,
                    email,
                )
                if not user:
                    raise ValueError(_LOGIN_NO_ACCOUNT)

                await conn.execute(
                    "UPDATE users SET last_login = NOW() WHERE user_id = $1::uuid",
                    user["user_id"],
                )
                user_id = str(user["user_id"])
                tenant_id = user["tenant_id"]
            else:
                tenant_id = await conn.fetchval(
                    "INSERT INTO tenants (name) VALUES ($1) RETURNING tenant_id",
                    email,
                )
                user = await conn.fetchrow(
                    """
                    INSERT INTO users (email, tenant_id, last_login)
                    VALUES ($1, $2, NOW())
                    RETURNING user_id, email, tenant_id
                    """,
                    email,
                    tenant_id,
                )
                user_id = str(user["user_id"])
                consent_at = datetime.now(timezone.utc)
                try:
                    await _save_signup_consent(
                        db=conn,
                        user_id=user_id,
                        consent_at=consent_at,
                        marketing_consent=bool(marketing_consent),
                    )
                except Exception as e:
                    log.warning(
                        "consent persistence skipped for user %s: %s", user_id, e
                    )
                await conn.execute(
                    """
                    UPDATE users
                    SET plan = COALESCE(plan, 'pro'),
                        subscription_status = COALESCE(subscription_status, 'trialing'),
                        trial_ends_at = COALESCE(
                            trial_ends_at, NOW() + INTERVAL '30 days'
                        )
                    WHERE user_id = $1::uuid
                    """,
                    user_id,
                )

            # Legacy rows: user without tenant breaks /v1/agents (JWT has no valid tenant_id)
            if not tenant_id:
                tenant_id = await conn.fetchval(
                    "INSERT INTO tenants (name) VALUES ($1) RETURNING tenant_id",
                    email,
                )
                await conn.execute(
                    "UPDATE users SET tenant_id = $1::uuid WHERE user_id = $2::uuid",
                    tenant_id,
                    user_id,
                )

            tenant_id = str(tenant_id)

            burned = await conn.fetchval(
                """
                UPDATE auth_otps SET used = TRUE
                WHERE otp_id = $1 AND used = FALSE
                RETURNING otp_id
                """,
                row["otp_id"],
            )
            if not burned:
                raise ValueError("OTP already used")

    return _issue_tokens(user_id, email, tenant_id)


def _issue_tokens(user_id: str, email: str, tenant_id: str) -> dict:
    now = datetime.now(timezone.utc)

    access_token = jwt.encode(
        {
            "sub": user_id,
            "email": email,
            "tenant_id": tenant_id,
            "exp": now + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES),
            "iat": now,
        },
        JWT_SECRET,
        algorithm="HS256",
    )

    refresh_token = jwt.encode(
        {
            "sub": user_id,
            "exp": now + timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS),
            "iat": now,
        },
        JWT_REFRESH_SECRET,
        algorithm="HS256",
    )

    return {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "token_type": "bearer",
    }


def decode_access_token(token: str) -> dict:
    return jwt.decode(token, JWT_SECRET, algorithms=["HS256"])


# ─────────────────────────────────────────
# EMAIL
# ─────────────────────────────────────────

SMTP_TIMEOUT_SEC = int(os.getenv("EMAIL_SMTP_TIMEOUT", "15"))


def _send_otp_email_sync(email: str, otp: str) -> None:
    """Blocking SMTP — always call via asyncio.to_thread so the API stays responsive."""
    host = os.getenv("EMAIL_HOST")
    user = os.getenv("EMAIL_USER")
    password = os.getenv("EMAIL_PASS")

    if not host or not user or not password:
        log.warning(
            "Email not configured — OTP for %s was generated but not sent "
            "(set EMAIL_HOST / EMAIL_USER / EMAIL_PASS to send real emails)",
            email,
        )
        return

    port = int(os.getenv("EMAIL_PORT", 587))
    from_addr = os.getenv("EMAIL_FROM", f'"ZizkaDB" <{user}>')
    is_ssl = port == 465

    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"{otp} is your ZizkaDB login code"
    msg["From"] = from_addr
    msg["To"] = email

    text = f"Your ZizkaDB login code: {otp}\n\nExpires in 15 minutes."
    html = f"""
    <div style="font-family:Arial,sans-serif;max-width:420px;margin:40px auto;padding:24px;background:#fff;border-radius:12px;">
      <p style="font-size:14px;color:#555;margin:0 0 20px">Your ZizkaDB login code:</p>
      <div style="font-size:38px;font-weight:700;letter-spacing:10px;
                  background:#f5f5f5;padding:24px;text-align:center;
                  border-radius:8px;color:#111;font-family:monospace">
        {otp}
      </div>
      <p style="color:#aaa;font-size:12px;margin-top:20px;line-height:1.6">
        Expires in 15 minutes.<br>
        If you did not request this, ignore this email.
      </p>
    </div>
    """

    msg.attach(MIMEText(text, "plain"))
    msg.attach(MIMEText(html, "html"))

    if is_ssl:
        import ssl
        context = ssl.create_default_context()
        with smtplib.SMTP_SSL(
            host, port, context=context, timeout=SMTP_TIMEOUT_SEC,
        ) as server:
            server.login(user, password)
            server.sendmail(from_addr, email, msg.as_string())
    else:
        with smtplib.SMTP(host, port, timeout=SMTP_TIMEOUT_SEC) as server:
            server.starttls()
            server.login(user, password)
            server.sendmail(from_addr, email, msg.as_string())


async def _send_otp_email(email: str, otp: str) -> None:
    await asyncio.to_thread(_send_otp_email_sync, email, otp)
