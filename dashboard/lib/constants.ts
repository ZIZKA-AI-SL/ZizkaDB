export const GITHUB_URL = "https://github.com/ZIZKA-AI-SL/ZizkaDB";
export const FOUNDER_EMAIL = "founder@zizka.ai";
export const POLL_INTERVAL_MS = 10_000;
export const OTP_LENGTH = 6;
export const IS_DEV_MODE = process.env.NEXT_PUBLIC_DEV_MODE === "true";
// Self-hosted build: no marketing site, signup or billing — just the owner's dashboard.
// Strict match so an unset value (managed cloud PM2 build) keeps today's behavior.
export const IS_SELF_HOSTED =
  process.env.NEXT_PUBLIC_DEPLOYMENT_MODE?.trim().toLowerCase() === "self_hosted";
