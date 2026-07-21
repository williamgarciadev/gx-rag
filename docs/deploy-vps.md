# Deploy: public MCP on the VPS (Phase C)

Exposes the **read-only** MCP endpoint at `https://gxrag.wgsoft.com.co/mcp/`,
behind Traefik + TLS, so any authorized developer can use it over the internet.

The admin Ingestor API and the curator UI are **not** internet-exposed.

## Prerequisites

- A running **Traefik** on the external docker network `infra_gateway`, with an
  HTTPS entrypoint and a Let's Encrypt certresolver.
- DNS: `gxrag.wgsoft.com.co` → `173.249.49.68` (A record). ✅ already set.
- The network exists: `docker network create infra_gateway` (once, if missing).

> **Verify two names against your Traefik static config** before deploying — the
> compose labels assume the conventional defaults:
> - entrypoint `websecure`
> - certresolver `letsencrypt`
>
> If yours differ, edit the `traefik.http.routers.gxrag-mcp.*` labels in
> `docker-compose.yml`.

## Steps

```bash
# 1. Create the real environment file from the template.
cp .env.example .env

# 2. Generate a bearer key per developer (>= 32 chars) and add them
#    comma-separated to GX_PUBLIC_API_KEYS in .env.
openssl rand -hex 32          # repeat per client

# 3. Build and start (admin API + UI stay internal; only the MCP is routed).
docker compose up -d --build

# 4. Verify health from the host (container-internal port).
docker compose exec gx-rag-mcp curl -fs http://localhost:8080/health
# -> {"status":"ok"}

# 5. Verify TLS + auth from the outside once Traefik has the cert.
curl -s -o /dev/null -w '%{http_code}\n' https://gxrag.wgsoft.com.co/mcp/   # 401 (no key)
```

## How a developer connects

Point an MCP client at the Streamable HTTP endpoint with a bearer token:

```
URL:    https://gxrag.wgsoft.com.co/mcp/     (trailing slash matters)
Header: Authorization: Bearer <their-key>
```

Available tools are **read-only**: `search_docs`, `get_article`, `get_document`,
`verify_syntax`, `list_methods`. Ingestion tools are not on this surface.

## Rotating / revoking access

Edit `GX_PUBLIC_API_KEYS` in `.env`, then `docker compose up -d` to reload.
Removing a key immediately revokes that client.

## Curator access (admin API + UI)

Not exposed to the internet. Reach it over an SSH tunnel:

```bash
ssh -L 8000:gx-rag-api:8000 <vps>     # then hit http://localhost:8000
```

## Security posture

Enforced in-app: read-only tool allowlist, fail-closed bearer auth, per-key rate
limiting, per-IP auth-failure throttling, request body-size cap, TrustedHost,
redacted error messages. Traefik terminates TLS.

## Open gate before going live

**Confirm with Legal/the team that the Bantotal documentation in the corpus is
not proprietary/licensed material** before exposing search over it to the
internet. This is a compliance decision, not a technical one.
