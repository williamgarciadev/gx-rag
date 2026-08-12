# DEPLOY — exposing the MCP publicly

How the gx-rag MCP server went from a local-only stdio tool to a public,
internet-facing endpoint, and everything learned along the way. For the
step-by-step runbook, see [`docs/deploy-vps.md`](docs/deploy-vps.md).

**Target:** `https://gxrag.wgsoft.com.co/mcp/` → VPS `173.249.49.68`, behind
Traefik + Let's Encrypt.

---

## Why it wasn't "just upload it"

The MCP server (`src/server.py`) speaks **stdio** — process pipes, no network
socket. stdio cannot be exposed over the internet at all. The FastAPI in
`src/api.py` is **not** an MCP server; it's an admin/ingestion API that mirrors
some tools for the UI. An MCP client cannot consume it as MCP.

So "let any developer use it from the internet" required real work, done in
three phases.

## Phase A — transport

Added `src/http_server.py`: the official SDK's **Streamable HTTP** transport
(`StreamableHTTPSessionManager`, stateless, JSON responses) mounted at `/mcp`.
It wraps the existing server object — no tool was rewritten.

- `mcp.server.fastmcp.FastMCP` **is** part of the official SDK; the project's
  "no standalone FastMCP" rule targets the 3rd-party package, not this. We still
  reused the low-level `Server` to avoid rewriting tools.
- The session manager needs Starlette's **lifespan** to keep its task group
  alive, or the transport isn't running.
- `POST /mcp` → **307** redirect to `/mcp/`. Clients must use the trailing
  slash. (`TestClient` follows redirects, so tests didn't reveal it — a real
  `curl` did.)

## Phase B — the endpoint was a door with no lock

A socket smoke test proved it: `tools/list` and even `add_from_url` (an **SSRF**
vector) were callable with a single unauthenticated `curl`. Hardening added, in
layers (defense in depth):

1. **Tool allowlist.** Split into `READ_TOOLS` / `WRITE_TOOLS`. The public
   transport builds a restricted server that only lists/dispatches read tools —
   write tools are structurally unreachable, even with a valid key.
2. **Bearer auth**, fail-closed (no keys configured → 401 everything),
   constant-time comparison (`hmac.compare_digest`).
3. **Per-key rate limiting** (429) and **per-IP auth-failure throttling** — the
   latter was a review catch: the first rate limiter only counted *after* auth,
   leaving key brute-forcing unthrottled.
4. **Redacted errors** — internal exception text (paths, SQL) is logged
   server-side, never returned to callers.
5. **TrustedHost**, **request body-size cap** (413), **arg-key-only logging**
   (no PII values), **min key length** (≥32 chars).

A fresh-context security review drove several of these (C1 brute-force, H1 error
leak, PII logging, key entropy).

## Phase C — deploy

- New `gx-rag-mcp` compose service (single worker) routed by Traefik with TLS.
- Admin API host port **removed** and admin API + UI kept internal (no Traefik
  labels) — reachable only via SSH tunnel.
- Added **`.dockerignore`** — it was missing, so `COPY . .` was baking `.env`,
  `data/`, and `.git` into the image (a secret-leak). It was also *gitignored*,
  which would have dropped it on the VPS; that entry was removed.

---

## What the VPS actually looks like (recon, 2026-07-21)

- Docker **29.5.3**. User `gga` is in the `sudo` group with **passwordless
  sudo** but is **not** in the `docker` group → use `sudo docker …`.
- `infra_gateway` is an existing external **bridge** network.
- **Traefik** (`traefik:latest`) runs with static config at
  `/opt/infra/traefik/config/traefik.yml` and dynamic config at
  `/opt/infra/traefik/config/dynamic.yml`; certs under `/opt/infra/traefik/certs`.
- House convention, confirmed from a live service (`magis-frontend`):
  - entrypoint **`websecure`**
  - **`tls.certResolver=letsencrypt`**
- Many apps already share the network (magis, firma-service, pantry-api, …).
  A **previous `gx-rag-api` / `gx-rag-ui` deployment is already running** — the
  new compose recreates them (dropping the exposed host port).

## Gotchas worth remembering

- **Healthcheck vs TrustedHost.** The container healthcheck curls
  `http://localhost:8080/health`, sending `Host: localhost`. With TrustedHost
  restricted to the public hostname only, that returns **400** and the container
  is marked unhealthy — failing the deploy. `GX_ALLOWED_HOSTS` **must** include
  `localhost,127.0.0.1`.
- **The autoformatter (ruff) strips a not-yet-used import** between edits. Add an
  import in the *same* edit that first uses it.
- **`env_file: .env` fails loudly** if `.env` is missing — run
  `cp .env.example .env` first (a good forcing function).
- Rate-limit / failure counters are **in-memory, per-process**. Run single
  worker, or move to Redis before scaling; the fixed window also allows ~2x
  burst at the minute boundary.

## Still open before go-live (NOT technical)

**Is the Bantotal documentation in the corpus proprietary/licensed?** Exposing a
public search over it is a **compliance** decision. Confirm with Legal/the team
before the endpoint serves the internet.
