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

## Actualizar un deploy que ya está corriendo

> ⏳ **PENDIENTE AL 2026-08-11 — hay código commiteado que NO está desplegado.**
>
> Commits `e4544ed`, `84d1bb5`, `cfbb892` (auditoría de interfaz) están en
> `vps-produccion` y **el VPS todavía sirve el código anterior**.
>
> **Por qué importa acá y no es cosmético:** esos commits corrigen las
> descripciones de las read tools, y en este sistema *la descripción de una tool
> es el prompt del agente que la consume*. Mientras no se rebuildee,
> `https://gxrag.wgsoft.com.co/mcp/` le sigue diciendo a cada agente que el motor
> léxico es BM25, que `get_document` devuelve el documento completo, y que
> `list_methods` acepta `object_type='Transaction'` — o sea que las mentiras de
> interfaz corregidas **siguen vivas en producción**.
>
> No hay migración de base en este lote: no se tocó ningún `sql/*.sql`, así que es
> un deploy de **solo código**.

### Por qué un `git push` no alcanza

Los tres servicios de `docker-compose.yml` usan `build: .`, y el único volumen es
`./data`. **El código viaja horneado en la imagen**, no bind-mounteado. Sin
`--build` el contenedor sigue corriendo el `src/` con el que se construyó.

### Procedimiento

```bash
ssh vps2-tunel                    # 173.249.49.68, usuario gga
# (host alternativo: vps2 — mismo server, usuario geo, puerto 54022)

cd <dir del repo en el VPS>
# Primera vez, confirmá CÓMO llegó el código (nadie lo dejó escrito):
git -C . remote -v                # si no es un clone, el paso de abajo es rsync/scp

git pull origin vps-produccion

# `gga` tiene sudo passwordless pero NO está en el grupo docker → sudo docker.
sudo docker compose up -d --build

# Verificar salud desde el host (puerto interno del contenedor).
sudo docker compose exec gx-rag-mcp curl -fs http://localhost:8080/health
# -> {"status":"ok"}

# Verificar que el endpoint público sigue cerrado sin key.
curl -s -o /dev/null -w '%{http_code}\n' https://gxrag.wgsoft.com.co/mcp/   # 401
```

### Verificar que el deploy REALMENTE cambió lo que tenía que cambiar

El `{"status":"ok"}` del healthcheck no prueba nada sobre el código nuevo: prueba
que el proceso arrancó. Para este lote, la verificación es que las descripciones
viajaron — pedí `tools/list` con una key válida y confirmá que la de `search_docs`
dice `tsvector`/`es_simple` y **no** `BM25`:

```bash
curl -s https://gxrag.wgsoft.com.co/mcp/ \
  -H "Authorization: Bearer <key>" \
  -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}' | grep -o 'BM25' && echo "❌ imagen vieja" || echo "✅ imagen nueva"
```

### Gotchas que ya costaron un deploy

- **`GX_ALLOWED_HOSTS` tiene que incluir `localhost,127.0.0.1`.** El healthcheck
  curlea con `Host: localhost`; con TrustedHost restringido solo al hostname
  público devuelve **400**, el contenedor queda unhealthy y el deploy falla.
- **`.env` está gitignored**, así que un `git pull` NO lo toca — las keys y el
  `GXRAG_DSN` del VPS sobreviven. Pero si el pull agrega variables nuevas al
  `.env.example`, hay que sumarlas a mano.
- **El catálogo de sintaxis vive en Postgres**, no en `data/syntax.db`, siempre
  que `GXRAG_DSN` esté definida (`models.py:113-140`). No hay que copiar ningún
  binario para que el validador tenga el catálogo.
- **El túnel del admin no está en el alias.** `vps2-tunel` forwardea 18789 y 5173,
  no el 8000 de la API admin: para curación usá
  `ssh -L 8000:gx-rag-api:8000 vps2-tunel`.
- ⚠️ **Si alguna vez recreás la base desde cero**, corré **todos** los
  `sql/0*.sql` en orden. El `001` todavía declara `tsv` con config `'simple'`
  mientras `pg_retriever` consulta `'es_simple'`: con solo el 001, la mitad léxica
  del RRF se rompe o pierde recall en silencio (hallazgo #29 de
  `docs/PLAN-optimizacion.md`).

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
