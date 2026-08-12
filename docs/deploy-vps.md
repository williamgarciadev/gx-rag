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

> ✅ **DESPLEGADO el 2026-08-11.** El VPS pasó de ChromaDB + BM25 a
> Postgres + pgvector, sirviendo el código de `vps-produccion` (HEAD `28a9a08`).
>
> Verificado end-to-end contra `https://gxrag.wgsoft.com.co/mcp/`, no supuesto:
>
> | Verificación | Resultado |
> |---|---|
> | `grep -c BM25 /app/src/server.py` en el contenedor | **0** (antes 1) |
> | Módulos `pg_*` en el contenedor | 4 |
> | `tools/list` sin key | **401** (fail-closed intacto) |
> | `tools/list` con **la key de siempre** | **200**, y sin `BM25` |
> | `search_docs("autorización…", bt_docs)` | 2 hits desde Postgres |
> | `list_methods("Method", version="gx18")` | **34** (antes: vacío siempre) |
> | `verify_syntax("do")` | **NO_VERIFICABLE** (antes: `INVÁLIDO` fabricado) |
> | `search_docs(collection="gx_doc")` | "Colección desconocida" (antes: "Sin resultados") |
>
> Corpus en producción: 51.211 chunks — `gx_docs` 50.063 · `bt_docs` 1.122 ·
> `gx_syntax` 26. Prueba de tildes: `autorizacion` 131 = `autorización` 131.
>
> **Lo que quedó recuperable:** el trabajo previo del VPS está en un `git stash`
> allá (`git stash list`) **y** rescatado en local; la config de los contenedores
> viejos en `/home/gga/gx-rag-containers-pre-deploy.json`; los backups del `.env`
> en `.env.bak-predeploy-postgres-*`; y el dump del corpus en `~/gxrag.dump`
> (239 MB) por si hay que reconstruir el store.

> 🔴 **Hallazgo abierto: la procedencia de `bt_docs` no es citable.** Los hits del
> manual funcional traen `Fuente: C:\Users\wigam\pautas-genexus\rag\manuales_
> bantotal\*.txt` — la ruta del disco de quien lo ingirió. Sale de
> `parse_text_files`, que hace `url=str(path.resolve())`. Para un agente
> consumidor esa procedencia **no existe y no se puede verificar**, lo que choca
> con la regla del repo de citar la fuente. Arreglarlo exige decidir una URL
> canónica para el manual y **reindexar `bt_docs`**: decisión de política de
> corpus, no un fix mecánico.
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

### ⛔ Bloqueo MÁS DE FONDO: el VPS no tiene el store Postgres

Antes que la rama, falta la infraestructura. Medido el 2026-08-11:

| Verificación | Resultado |
|---|---|
| Módulos en `gx-rag-mcp` | `chroma_client.py`, `collections.py`, `indexer.py`, `retriever.py` — **ningún `pg_*`** |
| `GXRAG_DSN` en el contenedor | **no definida** |
| Corpus que sirve hoy | **ChromaDB, 461 MB** en `data/chroma` (+ 2 backups `chroma.bak-*`) |
| Contenedor Postgres | `postgres-main` (`postgres:16-alpine`), up 10 días, **sin puerto publicado** |
| **Extensión `vector`** | **NI DISPONIBLE**: `pg_available_extensions where name='vector'` devuelve vacío |
| Base / rol `gxrag` | **no existen** (bases: `banco_contactar_db`, `gxapp_db`, `magis`, `postgres`, `wgsoft`; rol `gxapp_user`) |

O sea: **el endpoint público sirve la versión ChromaDB + BM25**, y desplegar esta
rama ahí no es difícil, es **imposible** en el estado actual. `pg_client.get_pool()`
lanza `RuntimeError` al arrancar sin `GXRAG_DSN` (a propósito, `open=True`), y aun
definiéndola, `CREATE EXTENSION vector` falla porque la extensión no está en esa
imagen.

Lo que haría falta, en orden, y **nada de esto es un rebuild**:

1. **pgvector en el VPS**: imagen `pgvector/pgvector:pg16` o compilar la extensión
   en `postgres-main`. ⚠️ Ese motor es **compartido** con `banco_contactar_db`,
   `magis` y `gxapp_db` — tocarlo es riesgo para otras apps. **Decisión humana.**
2. Rol + base `gxrag`, y correr `sql/001` **y** `sql/002` en ese orden (ver el
   gotcha del final: con solo el 001 la mitad léxica queda incoherente).
3. **Reindexar el corpus**: los 461 MB de Chroma no migran solos. Hay
   `scripts/migrate_chroma_to_pg.py`, pero implica re-embeddear ~51k chunks con
   e5-large **en el VPS** (CPU y ~2.2 GB de RAM por proceso).
4. Recién entonces, el `up -d --build`.

> **Corolario para las descripciones de las tools:** los conteos de corpus NO se
> hardcodean en el texto de una tool. El dev tiene ~50k chunks en Postgres y el
> VPS un ChromaDB sin `bt_docs`: un número en la descripción se despliega a los
> dos y es falso en uno. Los tamaños son hechos de runtime y ya viajan en la
> respuesta vacía vía `collection_counts()`. Hay un test que lo impide
> (`test_no_description_hardcodes_a_corpus_size`).

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

### 🔑 Las API keys NO se regeneran — requisito explícito

Los clientes MCP ya configurados tienen que **seguir funcionando con la misma
key**. Eso sale gratis si se respeta una sola cosa:

> ⛔ **NO corras `cp .env.example .env` en el VPS.** Es el paso 1 del bootstrap de
> más arriba, pensado para una instalación NUEVA: sobre un deploy existente
> **pisa `GX_PUBLIC_API_KEYS` y revoca a todos los clientes en el acto.**

El `.env` está gitignored, así que un `pull` o un `up -d --build` **no lo tocan**:
las keys sobreviven solas. Para el deploy con Postgres, lo único que hay que hacer
es **agregar** la variable que falta, sin reescribir el archivo:

```bash
# APPEND, nunca sobrescribir. Verificá antes que no exista ya:
grep -q '^GXRAG_DSN=' .env || echo 'GXRAG_DSN=postgresql://gxrag:<pass>@<host>:5432/gxrag' | sudo tee -a .env

# Y confirmá que las keys siguen ahí (sin imprimirlas):
grep -c '^GX_PUBLIC_API_KEYS=' .env      # -> 1
```

Verificación después del rebuild, con una key existente: si devuelve 200, la key
sigue viva; un 401 significa que se pisó el `.env`.

```bash
curl -s -o /dev/null -w '%{http_code}\n' https://gxrag.wgsoft.com.co/mcp/ \
  -H "Authorization: Bearer <key-de-siempre>" \
  -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'
```

Ojo: en el VPS ya hay `.env.bak-rotacion-20260811-095403` y `.env.bak-restore-102440`,
o sea que hubo al menos una rotación y un restore manual. **Antes de tocar el
`.env`, hacé un backup más** — y no los traigas a otra máquina: son secretos.

### Procedimiento REAL — ejecutado y verificado el 2026-08-11

Esto no es un plan: es lo que se corrió, con los tiempos medidos. Cinco cosas que
el procedimiento "obvio" daba por sentadas resultaron **falsas** en este VPS, y
están marcadas 🔴 porque cada una cortó el deploy a mitad de camino.

**1. Levantar el store dedicado** (una sola vez):

```bash
# Password generada EN el VPS, nunca en el transcript de nadie.
PGPASS=$(openssl rand -hex 24)
sudo docker run -d --name gx-rag-pg --network infra_gateway --restart unless-stopped \
  -e POSTGRES_USER=gxrag -e POSTGRES_PASSWORD="$PGPASS" -e POSTGRES_DB=gxrag \
  -v gx-rag-pgdata:/var/lib/postgresql/data \
  --health-cmd 'pg_isready -U gxrag -d gxrag' --health-interval 15s \
  pgvector/pgvector:pg16
umask 077; printf 'GXRAG_DSN=postgresql://gxrag:%s@gx-rag-pg:5432/gxrag\n' "$PGPASS" > ~/gx-rag-dsn.txt
```

Verificado ahí mismo: `create extension vector` → **0.8.6**, y el operador `<=>`
responde.

**2. NO hace falta correr `sql/001` ni `sql/002`.** 🔴 El plan decía que sí. Un
`pg_dump` completo **ya trae** `EXTENSION unaccent`, `EXTENSION vector`, la
`TEXT SEARCH CONFIGURATION es_simple` y los índices. Correr el `001` encima sería
además reintroducir su config `'simple'` (hallazgo #29). El deploy nace coherente
sin tocar nada.

**3. Dump y restore** — 51.211 chunks, **239 MB** comprimido:

```bash
# Local (Windows: pg_dump NO está en el PATH; está en Program Files\PostgreSQL\16\bin).
pg_dump -Fc --no-owner --no-privileges -d "$GXRAG_DSN" -f gxrag.dump    # 1m09s
scp gxrag.dump vps2-tunel:~/                                            # 1m27s

# 🔴 `pg_restore -j` NO funciona leyendo de stdin ("parallel restore from standard
# input is not supported"): hay que pasar el archivo, así que se copia adentro.
sudo docker cp ~/gxrag.dump gx-rag-pg:/tmp/
sudo docker exec gx-rag-pg pg_restore --no-owner --no-privileges -j2 \
     -U gxrag -d gxrag /tmp/gxrag.dump                                  # 2m24s
sudo docker exec gx-rag-pg rm -f /tmp/gxrag.dump
```

Compatibilidad que hay que chequear **antes**: local **16.2** → VPS **16.14**
(misma major) y pgvector **0.8.6 en los dos lados**. Con un local en 17 el dump no
restauraría, y se descubriría a mitad de camino.

**4. Agregar el DSN al `.env`** — ver la sección de keys de más arriba:

```bash
# 🔴 TODO lo que toque el .env va DENTRO de sudo. El glob y el redirect `<` los
# expande el shell SIN sudo y dan "Permission denied" (el dir es de `admin`).
# Con `set -e` eso corta el script justo antes del append, sin escribir nada.
sudo bash -c 'cd <dir>; cp -a .env .env.bak-predeploy-$(date +%s);
  grep -q "^GXRAG_DSN=" .env || echo "GXRAG_DSN=..." >> .env;
  grep -q "^GXRAG_PG_PASSWORD=" .env || echo "GXRAG_PG_PASSWORD=..." >> .env;
  grep -c "^GX_PUBLIC_API_KEYS=" .env'    # -> 1, las keys intactas
```

`GXRAG_PG_PASSWORD` hace falta además del DSN: el compose la exige con `:?` para
que el contenedor no arranque nunca con password vacía.

**5. Traer el código: con `git bundle`, no con `git pull`.** 🔴 El VPS **no tiene
credenciales de GitHub** y el repo es privado: `fetch` muere con
`could not read Username for 'https://github.com'`. Poner un token en el VPS es
peor. Un bundle lleva los commits **preservando la historia**, sin red ni
credenciales, y como el VPS ya tenía la base común pesa **0.2 MB**:

```bash
# Local — sólo lo que el VPS no tiene:
git bundle create gxrag-vps.bundle ^<base-comun> vps-produccion
git bundle verify gxrag-vps.bundle
scp gxrag-vps.bundle vps2-tunel:~/

# VPS (el dir es de `admin`, de ahí el safe.directory por comando):
sudo bash -c 'cd <dir>; G="git -c safe.directory=$PWD";
  $G stash push -u -m "pre-deploy";                       # reversible, no reset --hard
  $G fetch ~/gxrag-vps.bundle "refs/heads/vps-produccion:refs/remotes/origin/vps-produccion";
  $G checkout -B vps-produccion refs/remotes/origin/vps-produccion;
  chown -R admin:admin .git'                              # git corrió como root
```

🔴 El `chown` final no es opcional: sin él, `.git` queda con objetos de root y el
agente que opera el repo como `admin` no puede volver a usarlo.

**6. Build y arranque — servicios EXPLÍCITOS:**

```bash
sudo docker compose build gx-rag-mcp gx-rag-api gx-rag-ui
sudo docker compose up -d --no-deps gx-rag-mcp gx-rag-api gx-rag-ui
```

🔴 **Nombrá los servicios.** Un `up -d` pelado intenta gestionar también
`gx-rag-pg`, que se creó con `docker run` y no tiene labels de compose → choca por
nombre y, peor, invita a borrarlo. Nombrándolos, el store queda fuera del
alcance del comando. (Adoptarlo con compose es una ventana de mantenimiento
aparte: `docker rm -f gx-rag-pg` y dejar que compose lo recree — el volumen
`gx-rag-pgdata` persiste, así que el corpus sobrevive.)

🔴 **`gx-rag-api` y `gx-rag-ui` también estaban fuera de compose** (creados el
2026-07-25, sin labels), así que el `up` aborta con
`Conflict. The container name "/gx-rag-api" is already in use`. **`DEPLOY.md` dice
que "el nuevo compose los recrea": es falso, choca.** Hay que borrarlos primero —
respaldando antes su definición, porque es la única copia de cómo estaban puestos:

```bash
docker inspect gx-rag-api gx-rag-ui > ~/gx-rag-containers-pre-deploy.json
docker rm -f gx-rag-api gx-rag-ui        # data/ es bind-mount: no se pierde nada
docker compose up -d --no-deps gx-rag-mcp gx-rag-api gx-rag-ui
```

Las imágenes viejas quedan en el host (sin tag, por id) por si hay que volver
atrás; el `inspect` guarda con qué estaban corriendo.

### Garantizar que el VPS sirve EXACTAMENTE el código del repo

El checkout en el mismo commit **no** prueba que los contenedores sirvan ese código:
las imágenes se construyen en momentos distintos y es fácil rebuildear unas y no
otras. La verificación fuerte compara el **blob del commit** contra el archivo
**dentro del contenedor** — blob y no working tree, porque en Windows el working
tree tiene CRLF y daría falsos negativos:

```bash
# Local: hashes canónicos del commit
git ls-files 'src/*.py' 'src/**/*.py' 'scripts/*.py' | while read f; do
  echo "$(git show "HEAD:$f" | sha256sum | cut -c1-16)  $f"
done > hashes.txt && scp hashes.txt vps2-tunel:~/

# VPS: comparar contra CADA contenedor de Python (son imágenes distintas)
for C in gx-rag-mcp gx-rag-api; do
  while read h f; do
    ch=$(docker exec "$C" sha256sum "/app/$f" | cut -c1-16)
    [ "$ch" = "$h" ] || echo "DISTINTO en $C: $f"
  done < ~/hashes.txt
done
```

Corriéndolo el 2026-08-12 apareció justo lo que buscaba: `gx-rag-api` tenía un
`src/server.py` viejo y le faltaba `scripts/eval_bt_docs.py`, porque los deploys
anteriores rebuildearon `gx-rag-ui` y `gx-rag-mcp` pero no la API. **No afectaba al
runtime** (la API corre `src.api:app` y nunca importa `src/server.py`), pero engaña
a cualquiera que inspeccione el contenedor. Tras rebuildear: **26/26 idénticos en
los dos**.

🔴 **El bundle del frontend NO se puede comparar por hash desde Windows.** Con
`core.autocrlf=true` el working tree tiene CRLF y el checkout del VPS LF, así que el
input del build ya difiere: el mismo commit produce `index-BE_0ZAc_.js` desde LF y
`index-B4SeJq98.js` desde CRLF. `npm ci` (ya aplicado) fija las dependencias pero no
esto. Para el frontend, verificá **los fuentes contra el blob** y **marcadores
literales** en el bundle (`WorkWithPlus`, `total_chunks`), nunca nombres de
funciones: el minificador los renombra, y `grep -c` sobre un bundle devuelve 1
aunque haya cien ocurrencias porque es una sola línea — usá `grep -o | wc -l`.

**Verificación end-to-end** (el `Started` de compose no prueba nada). Ojo: el
endpoint responde **SSE**, así que hay que sacarle el prefijo `data: ` antes de
parsear el JSON — si no, muere con `Extra data: line 1 column 5`:

```bash
K=$(sudo grep '^GX_PUBLIC_API_KEYS=' .env | cut -d= -f2- | cut -d, -f1)
curl -s https://gxrag.wgsoft.com.co/mcp/ -H "Authorization: Bearer $K" \
  -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}' \
| sed 's/^data: //' | grep '^{' | grep -c BM25       # -> 0 = imagen nueva
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
