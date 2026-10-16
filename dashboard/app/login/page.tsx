"use client";

import { useState, useEffect, Suspense } from "react";
import { useSearchParams } from "next/navigation";
import Link from "next/link";
import { verifyOtp, devLogin } from "@/lib/api";
import { getToken, setToken } from "@/lib/auth";
import { IS_DEV_MODE, IS_SELF_HOSTED } from "@/lib/constants";
import { BrandLogo } from "@/components/BrandLogo";
import { OtpForm } from "@/components/auth/OtpForm";
import { SelfHostLogin } from "@/components/auth/SelfHostLogin";

function completeAuthRedirect(path: string) {
  window.location.assign(path);
}

export default function LoginPage() {
  return (
    <Suspense fallback={<LoginFallback />}>
      <LoginForm />
    </Suspense>
  );
}

function LoginFallback() {
  return (
    <div
      style={{
        minHeight: "100vh",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        background: "#fafafa",
        fontFamily: "Inter, system-ui, sans-serif",
        color: "#888",
      }}
    >
      Loading…
    </div>
  );
}

function LoginForm() {
  const searchParams = useSearchParams();
  const [devLoading, setDevLoading] = useState(false);
  const [navigating, setNavigating] = useState(false);
  const [error, setError] = useState("");

  const accountDeleted = searchParams.get("deleted") === "1";
  const emailPrefill = searchParams.get("email") ?? "";
  const nextPath = searchParams.get("next");
  const safeNext =
    nextPath && nextPath.startsWith("/dashboard") && !nextPath.startsWith("//")
      ? nextPath
      : "/dashboard";

  useEffect(() => {
    const existing = getToken();
    if (existing) {
      setToken(existing);
      window.location.assign(safeNext);
    }
  }, [safeNext]);

  async function handleVerified({
    email,
    otp,
  }: {
    email: string;
    otp: string;
  }) {
    const data = await verifyOtp(email, otp, { intent: "login" });
    setToken(data.access_token);
    setNavigating(true);
    completeAuthRedirect(safeNext);
  }

  async function handleDevLogin() {
    setDevLoading(true);
    setError("");
    try {
      const data = await devLogin();
      setToken(data.access_token);
      setNavigating(true);
      window.location.assign(safeNext);
    } catch {
      setError(
        "Could not connect to ZizkaDB API. Is docker-compose running on port 8000?",
      );
    } finally {
      setDevLoading(false);
    }
  }

  // Self-hosted: no email/OTP, signup or website links — just the owner sign-in.
  if (IS_SELF_HOSTED) {
    // Skip the automatic one-click sign-in right after an explicit sign-out.
    return (
      <SelfHostLogin
        next={safeNext}
        auto={searchParams.get("signed_out") !== "1"}
      />
    );
  }

  if (navigating) {
    return (
      <div
        style={{
          minHeight: "100vh",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          background: "#fafafa",
          fontFamily: "Inter, system-ui, sans-serif",
          color: "#555",
        }}
      >
        <p style={{ fontSize: 15 }}>Signing you in…</p>
      </div>
    );
  }

  return (
    <div
      style={{
        minHeight: "100vh",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        background: "#fafafa",
        fontFamily: "Inter, system-ui, sans-serif",
      }}
    >
      <div style={{ width: "100%", maxWidth: 400, padding: "0 24px" }}>
        <div style={{ textAlign: "center", marginBottom: 36 }}>
          <BrandLogo
            variant="full"
            suffix="The operational database for AI agents"
          />
        </div>

        {accountDeleted && (
          <div
            style={{
              background: "#fef3c7",
              border: "1px solid #fcd34d",
              borderRadius: 12,
              padding: "14px 16px",
              marginBottom: 16,
              fontSize: 13,
              color: "#92400e",
              lineHeight: 1.55,
            }}
          >
            Your account was deleted.{" "}
            <Link
              href="/signup"
              style={{ color: "#111", fontWeight: 600 }}
            >
              Create a new account →
            </Link>
          </div>
        )}

        {IS_DEV_MODE && (
          <div
            style={{
              background: "#f0fdf4",
              border: "1px solid #bbf7d0",
              borderRadius: 12,
              padding: "16px 20px",
              marginBottom: 20,
            }}
          >
            <div
              style={{
                display: "flex",
                alignItems: "center",
                gap: 8,
                marginBottom: 8,
              }}
            >
              <span
                style={{
                  background: "#22c55e",
                  color: "#fff",
                  fontSize: 11,
                  fontWeight: 700,
                  padding: "2px 7px",
                  borderRadius: 99,
                  letterSpacing: "0.05em",
                }}
              >
                SELF-HOSTED
              </span>
              <span style={{ fontSize: 13, fontWeight: 600, color: "#15803d" }}>
                Local dev mode
              </span>
            </div>
            <p style={{ fontSize: 13, color: "#166534", margin: "0 0 12px" }}>
              Running your own ZizkaDB instance? Open the dashboard directly —
              no account needed.
            </p>
            <button
              onClick={handleDevLogin}
              disabled={devLoading}
              style={{
                width: "100%",
                padding: "10px",
                borderRadius: 9,
                fontSize: 14,
                fontWeight: 600,
                background: "#16a34a",
                color: "#fff",
                border: "none",
                cursor: "pointer",
                opacity: devLoading ? 0.6 : 1,
              }}
            >
              {devLoading ? "Connecting..." : "Open my dashboard →"}
            </button>
          </div>
        )}

        <div
          style={{
            background: "#fff",
            borderRadius: 16,
            padding: "36px 32px",
            border: "1px solid #e5e5e5",
            boxShadow: "0 2px 20px rgba(0,0,0,0.05)",
          }}
        >
          {IS_DEV_MODE && (
            <p
              style={{
                fontSize: 12,
                color: "#aaa",
                marginTop: 0,
                marginBottom: 20,
                textAlign: "center",
              }}
            >
              Or sign in with managed service account
            </p>
          )}

          {error && (
            <p style={{ fontSize: 13, color: "#ef4444", marginTop: 0 }}>
              {error}
            </p>
          )}

          <OtpForm
            intent="login"
            initialEmail={emailPrefill}
            autoFocusEmail={!IS_DEV_MODE}
            hideEmailHeading={IS_DEV_MODE}
            onVerified={handleVerified}
          />
        </div>

        {!IS_DEV_MODE && (
          <p
            style={{
              textAlign: "center",
              fontSize: 13,
              color: "#aaa",
              marginTop: 16,
            }}
          >
            No account yet?{" "}
            <Link
              href="/signup"
              style={{ color: "#555", textDecoration: "none", fontWeight: 500 }}
            >
              Create one free →
            </Link>
          </p>
        )}
        <p
          style={{
            textAlign: "center",
            fontSize: 13,
            color: "#ccc",
            marginTop: 8,
          }}
        >
          Self-hosting?{" "}
          <a href="https://github.com/Zizka-ai/ZizkaDB/wiki" style={{ color: "#aaa", textDecoration: "none" }}>
            View setup guide →
          </a>
        </p>
      </div>
    </div>
  );
}
