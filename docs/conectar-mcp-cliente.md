# Conectar un cliente al MCP público de gx-rag

Guía para agregar el servidor MCP **gx-rag-vps** en Claude Code y consumir la
documentación GeneXus/Bantotal sobre HTTPS.

- **Endpoint:** `https://gxrag.wgsoft.com.co/mcp/`
- **Autenticación:** header `Authorization: Bearer <TU_BEARER_KEY>`

> **La `<TU_BEARER_KEY>` NO está en este documento a propósito.** Es una
> credencial de producción. Pedísela al responsable del proyecto por un canal
> seguro (gestor de secretos o mensaje efímero), nunca por chat/mail en texto
> plano. No la pegues en ningún archivo que se suba al repositorio.

---

## Opción A — Trabajás sobre el repo (recomendado)

El archivo `.mcp.json` del proyecto ya incluye la entrada `gx-rag-vps`. No lee
la key desde el archivo, sino desde la variable de entorno `GX_RAG_VPS_KEY`, así
que el secreto nunca se versiona:

```json
"gx-rag-vps": {
  "type": "http",
  "url": "https://gxrag.wgsoft.com.co/mcp/",
  "headers": { "Authorization": "Bearer ${GX_RAG_VPS_KEY}" }
}
```

Lo único que tenés que hacer es exportar la variable de entorno con tu key.

### Windows (PowerShell)

```powershell
# Persistente para tu usuario (se guarda en el registro de Windows)
setx GX_RAG_VPS_KEY "<TU_BEARER_KEY>"
```

Cerrá y volvé a abrir la terminal para que tome el valor (`setx` no afecta la
sesión actual). Verificá que quedó cargada:

```powershell
$env:GX_RAG_VPS_KEY
```

Después abrí Claude Code **desde esa misma terminal** para que herede la
variable.

### macOS / Linux

```bash
# Agregá esta línea a ~/.zshrc (macOS) o ~/.bashrc (Linux)
export GX_RAG_VPS_KEY="<TU_BEARER_KEY>"

# Recargá el shell
source ~/.zshrc
```

Abrí Claude Code desde esa terminal. El servidor `gx-rag-vps` se conecta solo.

---

## Opción B — Sin el repo (registro global standalone)

Si no vas a clonar el proyecto, registrá el servidor con un solo comando. La key
va directa en el comando (no en un archivo versionado).

### Windows (PowerShell)

```powershell
claude mcp add --transport http gx-rag-vps https://gxrag.wgsoft.com.co/mcp/ `
  --header "Authorization: Bearer <TU_BEARER_KEY>"
```

> El backtick `` ` `` al final de la primera línea es la continuación de línea de
> PowerShell. Si preferís, poné todo en una sola línea sin el backtick.

### macOS / Linux

```bash
claude mcp add --transport http gx-rag-vps https://gxrag.wgsoft.com.co/mcp/ \
  --header "Authorization: Bearer <TU_BEARER_KEY>"
```

---

## Verificar la conexión

1. En Claude Code, ejecutá `/mcp`.
2. `gx-rag-vps` debe aparecer como **connected**.
3. Probá una herramienta, por ejemplo `search_docs`, con una consulta corta.

---

## Cómo lo usa Claude Code

Este MCP **no tiene su propio LLM**: es pura recuperación de documentación
GeneXus/Bantotal. Claude Code sigue liderando el razonamiento; el MCP le da
**fuentes verificables** para no alucinar. El transport público es de **solo
lectura** (5 herramientas). No podés ingerir corpus desde acá.

### El principio de dos capas (esto es lo importante)

- **`search_docs` / `get_article` → recuperación.** Mitigan la alucinación:
  traen documentación real, rankeada por relevancia. Es difusa y con score.
- **`verify_syntax` / `list_methods` → validación.** Cortan la alucinación:
  responden contra un catálogo cerrado con veredicto binario.

Regla de oro: **si vas a afirmar que un método, propiedad o firma de
GeneXus/Bantotal existe, pasalo por `verify_syntax` antes.** No lo des por
válido solo porque apareció en un fragmento recuperado.

### Las 5 herramientas

| Herramienta | Para qué | Parámetros clave |
|---|---|---|
| `search_docs` | Buscar en la doc por relevancia (dense + BM25 + RRF) | `query` (req), `collection`, `top_k`, `snippet_chars`, `min_score` |
| `get_article` | Reensamblar el **documento completo** cuando el fragmento no alcanza | `article_id` (req), `collection` |
| `get_document` | Traer un documento indexado puntual con su metadata/procedencia | `doc_id` (req), `collection` |
| `verify_syntax` | Veredicto **VÁLIDO / INVÁLIDO / NO_VERIFICABLE** de un snippet | `snippet` (req), `language` (`gx`\|`bt_api`), `version` (`gx16`\|`gx18`) |
| `list_methods` | Listar métodos/propiedades conocidos de un tipo de objeto | `object_type` (req), `version` |

**Colecciones** para `search_docs`: `gx_docs` (guías GeneXus, default),
`gx_syntax` (referencia/sintaxis), `bt_docs` y `bt_apis` (Bantotal). Si una
colección está vacía, `search_docs` te avisa cuáles sí tienen contenido — el
corpus está en construcción (Fase 1), así que no todas responden todavía.

### Flujo recomendado

1. **Buscar** con `search_docs`. Anotá el `article_id` de los resultados útiles.
2. Si el fragmento no alcanza para fundamentar bien, **reensamblar** con
   `get_article` usando ese `article_id`.
3. Antes de escribir código GeneXus, **validar** cada método/firma con
   `verify_syntax`. Si da `NO_VERIFICABLE`, tratalo como "no confirmado", no
   como "válido".

### Ejemplos de prompt (lo que tu compañero le pide a Claude Code)

```text
Buscá en gx-rag cómo se define un Data Provider en GeneXus y mostrame el
artículo completo de la fuente más relevante.
```

```text
Verificá contra gx-rag si el método `Trn.Save()` existe en GeneXus 18 antes de
usarlo en el ejemplo.
```

```text
Listame en gx-rag los métodos conocidos del objeto Procedure en gx18.
```

> Claude Code decide solo cuándo invocar cada herramienta a partir de tu pedido
> en lenguaje natural. Vos guiás; el MCP aporta la fuente. Mencionar "gx-rag" en
> el pedido ayuda a que elija este servidor.

---

## Problemas comunes

| Síntoma | Causa probable | Solución |
|---|---|---|
| `HTTP 401 Unauthorized` | La variable `GX_RAG_VPS_KEY` está vacía o mal escrita | Verificá el valor y **reiniciá Claude Code desde una terminal nueva** — las variables de entorno se heredan al arrancar el proceso, no se refrescan en caliente. |
| `gx-rag-vps` no aparece en `/mcp` | Abriste Claude Code sin exportar la variable, o desde otra terminal | Exportá la key y relanzá desde esa misma terminal. |
| Conecta pero no responde | Endpoint o red | Confirmá que `https://gxrag.wgsoft.com.co/mcp/` responde desde tu red. |
