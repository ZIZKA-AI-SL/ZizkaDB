"""Auth flow tests — login vs signup intent, account existence checks."""

import bcrypt
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from main import app
from api.auth import otp_limiter
from services.auth import (
    _LOGIN_NO_ACCOUNT,
    _SIGNUP_ALREADY_REGISTERED,
    _SIGNUP_GDPR_REQUIRED,
    verify_otp,
)

client = TestClient(app)

TEST_OTP = "123456"
TEST_OTP_HASH = bcrypt.hashpw(TEST_OTP.encode(), bcrypt.gensalt()).decode()
TEST_OTP_ROW = {
    "otp_id": "otp-1",
    "otp_hash": TEST_OTP_HASH,
    "attempts": 0,
    "max_attempts": 5,
}


class TestRequestOtpIntent:
    def setup_method(self):
        import asyncio
        from services.rate_limiter import InMemoryStorage

        otp_limiter.storage = InMemoryStorage()
        asyncio.run(otp_limiter.storage.clear())

    @patch("api.auth.request_otp", new_callable=AsyncMock)
    @patch("api.auth.email_exists", new_callable=AsyncMock)
    def test_login_rejects_unknown_email(self, mock_exists, mock_request):
        mock_exists.return_value = False
        res = client.post(
            "/v1/auth/request-otp",
            json={"email": "gone@example.com", "intent": "login"},
        )
        assert res.status_code == 404
        assert "No account found" in res.json()["detail"]
        mock_request.assert_not_called()

    @patch("api.auth.request_otp", new_callable=AsyncMock)
    @patch("api.auth.email_exists", new_callable=AsyncMock)
    def test_login_sends_code_for_existing_user(self, mock_exists, mock_request):
        mock_exists.return_value = True
        res = client.post(
            "/v1/auth/request-otp",
            json={"email": "user@example.com", "intent": "login"},
        )
        assert res.status_code == 200
        mock_request.assert_called_once()

    @patch("api.auth.request_otp", new_callable=AsyncMock)
    @patch("api.auth.email_exists", new_callable=AsyncMock)
    def test_signup_rejects_existing_email(self, mock_exists, mock_request):
        mock_exists.return_value = True
        res = client.post(
            "/v1/auth/request-otp",
            json={"email": "user@example.com", "intent": "signup"},
        )
        assert res.status_code == 409
        assert "already registered" in res.json()["detail"]
        mock_request.assert_not_called()

    @patch("api.auth.request_otp", new_callable=AsyncMock)
    @patch("api.auth.email_exists", new_callable=AsyncMock)
    def test_signup_sends_code_for_new_email(self, mock_exists, mock_request):
        mock_exists.return_value = False
        res = client.post(
            "/v1/auth/request-otp",
            json={"email": "new@example.com", "intent": "signup"},
        )
        assert res.status_code == 200
        mock_request.assert_called_once()


    @patch("api.auth.request_otp", new_callable=AsyncMock)
    @patch("api.auth.email_exists", new_callable=AsyncMock)
    def test_request_otp_defaults_to_login(self, mock_exists, mock_request):
        mock_exists.return_value = True
        res = client.post(
            "/v1/auth/request-otp",
            json={"email": "user@example.com"},
        )
        assert res.status_code == 200
        mock_request.assert_called_once()


class TestVerifyOtpIntent:
    @pytest.mark.asyncio
    @patch("services.auth.email_exists", new_callable=AsyncMock)
    @patch("services.auth.get_pool")
    async def test_login_rejects_unknown_email_before_otp_burn(self, mock_pool_fn, mock_exists):
        mock_exists.return_value = False
        pool = MagicMock()
        pool.fetchrow = AsyncMock(return_value=TEST_OTP_ROW)
        pool.execute = AsyncMock()
        mock_pool_fn.return_value = pool

        with pytest.raises(ValueError, match=_LOGIN_NO_ACCOUNT):
            await verify_otp("deleted@example.com", TEST_OTP, intent="login")

        pool.execute.assert_not_called()

    @pytest.mark.asyncio
    @patch("services.auth.email_exists", new_callable=AsyncMock)
    @patch("services.auth.get_pool")
    async def test_signup_requires_gdpr_for_new_user(self, mock_pool_fn, mock_exists):
        mock_exists.return_value = False
        pool = MagicMock()
        pool.fetchrow = AsyncMock(return_value=TEST_OTP_ROW)
        pool.execute = AsyncMock()
        mock_pool_fn.return_value = pool

        with pytest.raises(ValueError, match=_SIGNUP_GDPR_REQUIRED):
            await verify_otp("new@example.com", TEST_OTP, intent="signup")

        pool.execute.assert_not_called()

    @pytest.mark.asyncio
    @patch("services.auth.email_exists", new_callable=AsyncMock)
    @patch("services.auth.get_pool")
    async def test_signup_rejects_existing_email(self, mock_pool_fn, mock_exists):
        mock_exists.return_value = True
        pool = MagicMock()
        pool.fetchrow = AsyncMock(return_value=TEST_OTP_ROW)
        pool.execute = AsyncMock()
        mock_pool_fn.return_value = pool

        with pytest.raises(ValueError, match=_SIGNUP_ALREADY_REGISTERED):
            await verify_otp(
                "user@example.com",
                TEST_OTP,
                intent="signup",
                gdpr_consent=True,
            )

        pool.execute.assert_not_called()

    @pytest.mark.asyncio
    @patch("services.auth.email_exists", new_callable=AsyncMock)
    @patch("services.auth.get_pool")
    async def test_invalid_otp_does_not_burn(self, mock_pool_fn, mock_exists):
        pool = MagicMock()
        pool.fetchrow = AsyncMock(return_value=TEST_OTP_ROW)
        pool.execute = AsyncMock()
        pool.acquire = MagicMock()
        mock_pool_fn.return_value = pool

        with pytest.raises(ValueError, match="Invalid OTP"):
            await verify_otp("user@example.com", "000000", intent="login")

        pool.acquire.assert_not_called()
        mock_exists.assert_not_called()
        pool.execute.assert_awaited_once_with(
            "UPDATE auth_otps SET attempts = $2 WHERE otp_id = $1",
            "otp-1",
            1,
        )

    @pytest.mark.asyncio
    @patch("services.auth.email_exists", new_callable=AsyncMock)
    @patch("services.auth.get_pool")
    async def test_otp_already_used_when_burn_fails(self, mock_pool_fn, mock_exists):
        mock_exists.return_value = True
        pool = MagicMock()
        pool.fetchrow = AsyncMock(return_value=TEST_OTP_ROW)

        conn = MagicMock()
        conn.fetchrow = AsyncMock(return_value={
            "user_id": "uid-1",
            "email": "user@example.com",
            "tenant_id": "tid-1",
        })
        conn.execute = AsyncMock()
        conn.fetchval = AsyncMock(side_effect=[None])  # burn UPDATE returns nothing

        tx = MagicMock()
        tx.__aenter__ = AsyncMock(return_value=None)
        tx.__aexit__ = AsyncMock(return_value=None)
        conn.transaction = MagicMock(return_value=tx)

        acquire_cm = MagicMock()
        acquire_cm.__aenter__ = AsyncMock(return_value=conn)
        acquire_cm.__aexit__ = AsyncMock(return_value=None)
        pool.acquire = MagicMock(return_value=acquire_cm)

        mock_pool_fn.return_value = pool

        with pytest.raises(ValueError, match="OTP already used"):
            await verify_otp("user@example.com", TEST_OTP, intent="login")


class TestVerifyOtpRoute:
    @patch("api.auth.verify_otp", new_callable=AsyncMock)
    @patch("services.billing.fetch_user_billing", new_callable=AsyncMock)
    def test_verify_passes_intent_to_service(self, mock_billing, mock_verify):
        mock_verify.return_value = {
            "access_token": "tok",
            "refresh_token": "ref",
            "token_type": "bearer",
        }
        mock_billing.return_value = {"plan": "pro", "subscription_status": "trialing"}

        res = client.post(
            "/v1/auth/verify-otp",
            json={
                "email": "user@example.com",
                "otp": "123456",
                "intent": "signup",
                "gdpr_consent": True,
            },
        )
        assert res.status_code == 200
        assert res.json()["access_token"] == "tok"
        mock_verify.assert_called_once()
        call_kwargs = mock_verify.call_args.kwargs
        assert call_kwargs["intent"] == "signup"
        assert call_kwargs["gdpr_consent"] is True

    @patch("api.auth.verify_otp", new_callable=AsyncMock)
    @patch("services.billing.fetch_user_billing", new_callable=AsyncMock)
    def test_verify_returns_tokens_when_billing_lookup_fails(self, mock_billing, mock_verify):
        mock_verify.return_value = {
            "access_token": "tok",
            "refresh_token": "ref",
            "token_type": "bearer",
        }
        mock_billing.side_effect = RuntimeError("db down")

        res = client.post(
            "/v1/auth/verify-otp",
            json={"email": "user@example.com", "otp": "123456", "intent": "login"},
        )
        assert res.status_code == 200
        assert res.json()["access_token"] == "tok"
