# ADR-0007: API keys are random tokens verified with SHA-256, not argon2

Date: 2026-10-31
Status: accepted (supersedes the argon2 mention in the architecture draft)

## Context

Every request resolves an API key. The architecture draft said argon2. Argon2 is designed
to be slow (tens of milliseconds) to protect low-entropy passwords from brute force, which
would alone exceed the 15 ms gateway overhead budget (N1).

## Decision

Keys are `sb_` plus 32 random bytes (base64url). Only a SHA-256 hash is stored, with the
first 11 characters kept as an indexed lookup prefix. Verification is a constant-time
compare of the hash. There is nothing to brute force in a 256-bit random token, so a fast
hash is as safe as a slow one here.

## Consequences

- Key verification costs microseconds and one indexed query (a Redis cache follows in
  phase 2).
- A leaked database does not leak usable keys.
- Keys are shown once at creation and cannot be recovered; rotation is create-then-revoke.
