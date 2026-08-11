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

> ⏳ **PENDIENTE AL 2026-08-11 — hay código commiteado que NO está desplegado, y
> desplegarlo está BLOQUEADO por una reconciliación de ramas (ver más abajo).**
>
> Commits `e4544ed`, `84d1bb5`, `cfbb892` (auditoría de interfaz) están en
> `vps-produccion` y **el VPS todavía sirve el código anterior**. Verificado, no
> supuesto: `docker exec gx-rag-mcp grep -c BM25 /app/src/server.py` → **1**.
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

### 🔴 ESTADO REAL DEL VPS — medido por SSH el 2026-08-11. NO hagas `git pull` acá.

Reconocimiento hecho sobre `vps2-tunel` (`173.249.49.68`, usuario `gga`). Lo que hay
**no es un checkout de esta rama esperando un fast-forward**:

| Qué | Valor medido |
|---|---|
| Directorio del proyecto | `/home/admin/.openclaw/workspace/gx-bt-rag-mcp` (dueño `admin:admin`, **no** `gga`) |
| Remote | `https://github.com/williamgarciadev/gx-rag.git` |
| **Rama del VPS** | **`feat/hybrid-retrieval-and-api-hardening`**, HEAD `3229fd8` |
| Working tree | **SUCIO: 15 entradas**, incluida `M src/vectorstore/retriever.py` — archivo **borrado** en `c4fb609` |
| Sin trackear | `DiscoverPanel.vue`, `data/scraped_sac/`, backups de `.env` y de `data/chroma.bak-*` |
| `gx-rag-mcp` | imagen del 2026-08-11 10:26, healthy — y su `/app/src/server.py` **contiene `BM25`** |
| `gx-rag-api` / `gx-rag-ui` | del 2026-07-25 y **sin labels de compose**: se levantaron por otra vía, no con este `docker-compose.yml` |

**Divergencia real** desde la base común `3229fd8`:

- El VPS tiene **11 commits que esta rama no tiene**: panel admin de SAC (UI +
  endpoints de scrape jobs), `DiscoverPanel.vue`, guías de setup Windows, fix de
  `StatusBar`. **Hay trabajo hecho directo en el VPS que nunca volvió al repo.**
- Esta rama tiene **16 commits que el VPS no tiene**: toda la migración a
  Postgres (`c4fb609`), el catálogo de sintaxis, y la auditoría de interfaz.

O sea: el árbol del VPS es **anterior a la migración a Postgres**, con parches
locales encima. Un `git pull origin vps-produccion` ahí no es un fast-forward: es
un merge de ramas divergentes sobre un árbol sucio, y puede pisar el panel de SAC.

Además `vps-produccion` **no existe en el remoto** (solo hay `main` y
`feat/hybrid-retrieval-and-api-hardening`) y la rama local **no tiene upstream**:
"hacer push" acá significa **crear una rama nueva en GitHub**, no actualizar una.

### ⛔ Reconciliar las dos ramas es DECISIÓN HUMANA — no la tome un agente

Hay trabajo no publicado en los dos lados, así que cualquier camino puede perder
código. Las opciones, con su costo:

1. **Publicar `vps-produccion` y reconciliar en un branch de integración.** Push
   de la rama nueva, luego `merge` con `feat/hybrid-retrieval-and-api-hardening`
   resolviendo a mano los conflictos de `src/api.py`, `scrape_wiki.py` y
   `frontend/`. Es el único camino que preserva las dos mitades. El conflicto
   gordo es estructural, no textual: el VPS tiene `retriever.py` (ChromaDB) y acá
   ese motor ya no existe.
2. **Rescatar primero lo del VPS.** Commitear/exportar los 15 archivos sucios y
   los 11 commits del panel de SAC **antes** de tocar nada, para no depender de
   que ese disco siga vivo.
3. **Redeploy limpio desde esta rama**, aceptando explícitamente que se pierde el
   panel admin de SAC hasta que se re-porte sobre el store Postgres.

**Ninguna de las tres se ejecuta sin decisión explícita.** Mientras no se decida,
el endpoint público sigue sirviendo el código pre-auditoría — con las
descripciones viejas, que son el prompt del agente consumidor.

### Procedimiento, una vez reconciliadas las ramas

```bash
ssh vps2-tunel                    # 173.249.49.68, usuario gga
# (host alternativo: vps2 — mismo server, usuario geo, puerto 54022)

cd /home/admin/.openclaw/workspace/gx-bt-rag-mcp
# El dir es de `admin`, así que git corre bajo sudo y se queja de ownership.
# El flag va por comando, para NO ensuciar la config global del VPS:
sudo git -c safe.directory=$PWD status --short     # tiene que estar LIMPIO antes

sudo git -c safe.directory=$PWD pull origin <la-rama-que-se-haya-acordado>

# `gga` tiene sudo passwordless pero NO está en el grupo docker → sudo docker.
sudo docker compose up -d --build

# Verificar salud desde el host (puerto interno del contenedor).
sudo docker compose exec gx-rag-mcp curl -fs http://localhost:8080/health
# -> {"status":"ok"}

# Verificar que el endpoint público sigue cerrado sin key.
curl -s -o /dev/null -w '%{http_code}\n' https://gxrag.wgsoft.com.co/mcp/   # 401
```

⚠️ Ojo con `up -d --build`: `gx-rag-api` y `gx-rag-ui` hoy corren **fuera** de este
compose (sin labels, del 2026-07-25). El comando los **recrea** según este
`docker-compose.yml`, que a propósito les quita el puerto host publicado. Es lo
que dice `DEPLOY.md`, pero significa que la URL por la que hoy se llegue al admin
puede cambiar: preparate para usar el túnel SSH de más abajo.

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
