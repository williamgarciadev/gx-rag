"""
gx_bt_rag.server — MCP server (stdio transport).

Retrieval semántico (hybrid search sobre el store en Postgres) de documentación
GeneXus/Bantotal, más una capa de validación contra el catálogo SQL curado.

El consumidor es un AGENTE, no una persona: las descripciones de las tools son
el prompt que el agente lee para elegir, y los textos de respuesta son lo que
lee al momento de concluir. Por eso ambos declaran explícitamente qué NO cubre
el corpus y cómo NO leer cada veredicto — un agente no puede calibrar un
resultado que no se explica, y una respuesta ambigua se convierte en una
afirmación falsa aguas abajo. Al tocar una descripción o un mensaje acá,
mantené esa propiedad.
"""

import logging

from collections.abc import Awaitable, Callable, Sequence

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import (
    CallToolRequestParams,
    CallToolResult,
    ListToolsResult,
    PaginatedRequestParams,
    TextContent,
    Tool,
)

# Logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger(__name__)

# ─── Server instance ────────────────────────────────────────────────────────────

APP_NAME = "gx-bt-rag-mcp"
VERSION = "0.1.0"

server = Server(APP_NAME)


# ─── Tools ────────────────────────────────────────────────────────────────────


# Colecciones que el store puede contener. Fuente ÚNICA: alimenta el enum de las
# read tools Y el chequeo de runtime, porque el SDK no valida el inputSchema — un
# `gx_doc` mal escrito llegaba al SQL, no matcheaba nada y volvía como "Sin
# resultados", que el agente lee como evidencia sobre el CORPUS y no sobre su
# propio argumento.
KNOWN_COLLECTIONS = ("gx_docs", "bt_docs", "gx_syntax", "bt_apis")


# Read-only tools — safe for the public internet-facing transport.
READ_TOOLS: list[Tool] = [
    Tool(
        name="search_docs",
        description=(
            "Búsqueda híbrida sobre documentación indexada: denso (pgvector, "
            "e5-large) + léxico full-text (tsvector con config es_simple — sin "
            "stemming, insensible a tildes), fusionados con RRF. "
            "Devuelve fragmentos con score y PROCEDENCIA (url, article_id, hash): "
            "cita la url, no el fragmento suelto. Si la colección está vacía, "
            "informa cuáles tienen contenido.\n"
            "QUÉ HAY, mezclado en la MISMA colección: wiki de GeneXus, tickets SAC "
            "(sac.genexus.com — la mejor fuente para '¿es un bug conocido?' y sus "
            "workarounds) y la wiki de WorkWithPlus (docs.workwithplus.com).\n"
            "🔴 POR ESO, FILTRÁ POR PROCEDENCIA, NO POR PRESENCIA. GeneXus tiene un "
            "pattern nativo 'Work With for Web' y WorkWithPlus (DVelop) es OTRO "
            "producto: una query sobre 'el pattern' devuelve los dos entremezclados. "
            "El riesgo no es quedarte sin respuesta, es tomar la del producto "
            "equivocado. Mirá la url de cada hit: solo lo que viene de "
            "docs.workwithplus.com aplica a una KB con WWP aplicado.\n"
            "🔴 NUNCA concluyas 'no está documentado' desde un top_k chico: el corpus "
            "tiene decenas de miles de chunks y el ranking mezcla fuentes, así que "
            "un tema presente puede no entrar en los primeros 3. Antes de afirmar "
            "una ausencia, subí top_k y reformulá con términos del dominio.\n"
            "QUÉ COLECCIONES HAY: gx_docs (wiki GeneXus + SAC + WorkWithPlus), "
            "bt_docs (documentación Bantotal), gx_syntax, bt_apis. **CUÁLES "
            "tienen contenido y cuánto depende del deployment, así que no lo "
            "asumas de esta descripción**: cualquiera puede estar vacía, y cuando "
            "una búsqueda no devuelve nada la respuesta te informa los conteos "
            "reales, consultados en ese momento.\n"
            "Un fragmento recuperado NO valida una firma: recuperar ≠ verificar."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Consulta en lenguaje natural o palabras clave.",
                },
                "collection": {
                    "type": "string",
                    "enum": list(KNOWN_COLLECTIONS),
                    "description": "Colección a buscar.",
                    "default": "gx_docs",
                },
                "top_k": {
                    "type": "integer",
                    "description": "Cantidad de resultados a devolver.",
                    "default": 5,
                },
                "snippet_chars": {
                    "type": "integer",
                    "description": (
                        "Largo del fragmento por resultado. Subilo para ver la "
                        "sintaxis completa; usá get_article para el doc entero."
                    ),
                    "default": 600,
                },
                "min_score": {
                    "type": "number",
                    "description": (
                        "Piso de score RRF (rank-based, NO calibrado: no es "
                        "probabilidad). Filtro grueso, no umbral de confianza.\n"
                        "SÍ es interpretable su ESTRUCTURA: el score suma 1/(60+rank) "
                        "por cada mitad que encontró el chunk. Un score ≈0.0164 "
                        "(=1/61) significa que entró por UNA sola mitad, sin apoyo "
                        "léxico — típico de una consulta cuyo tema no está en la "
                        "colección. Los aciertos con apoyo de las dos mitades rondan "
                        "el doble. Medido sobre bt_docs: un tema ajeno al corpus "
                        "(«kubernetes ingress») puntúa exactamente 0.0164."
                    ),
                },
            },
            "required": ["query"],
        },
    ),
    Tool(
        name="get_article",
        description=(
            "Reensambla el documento COMPLETO a partir de sus chunks (orden "
            "chunk_index). Usalo tras search_docs, con el article_id, cuando "
            "el fragmento no alcanza para fundamentar sin alucinar."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "article_id": {
                    "type": "string",
                    "description": "ID de artículo (campo article_id/id de search_docs).",
                },
                "collection": {
                    "type": "string",
                    "enum": list(KNOWN_COLLECTIONS),
                    "description": "Colección donde está el artículo.",
                    "default": "gx_docs",
                },
            },
            "required": ["article_id"],
        },
    ),
    Tool(
        name="get_document",
        description=(
            "Devuelve UN fragmento (chunk) por su id, con su metadata y "
            "procedencia. Un chunk son ~800 caracteres: es un pedazo del "
            "artículo, NO el artículo.\n"
            "🔴 Para el documento entero usá get_article con el article_id. Si "
            "tratás este fragmento como el artículo completo, vas a concluir que "
            "lo que no está en él no existe."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "doc_id": {
                    "type": "string",
                    "description": (
                        "ID del chunk — el campo doc_id que devuelve search_docs. "
                        "Ojo: no es el article_id (ese va a get_article)."
                    ),
                },
                "collection": {
                    "type": "string",
                    "enum": list(KNOWN_COLLECTIONS),
                    "description": "Colección donde está el chunk.",
                    "default": "gx_docs",
                },
            },
            "required": ["doc_id"],
        },
    ),
    Tool(
        name="verify_syntax",
        description=(
            "Verifica una firma/método/comando contra el catálogo estructurado "
            "(SQL, curado a mano). Devuelve VÁLIDO / INVÁLIDO / NO_VERIFICABLE.\n"
            "🔴 CÓMO LEER EL VEREDICTO — acá se cometen los errores más caros. "
            "NINGUNO de los tres es simétrico: solo VÁLIDO es una afirmación.\n"
            "  VÁLIDO         = está en el catálogo curado. Afirmalo, con su source.\n"
            "  INVÁLIDO       = NO matcheó exacto Y existen entradas PARECIDAS. Es\n"
            "                   un detector de TYPO (mirá 'sugerencias'), NO una\n"
            "                   prueba de que la construcción no exista. Se calcula\n"
            "                   por similitud contra un catálogo incompleto: un\n"
            "                   nombre válido no catalogado que se parezca a otra\n"
            "                   entrada cae acá. Tratalo como '¿quisiste decir X?'.\n"
            "  NO_VERIFICABLE = 'NO SÉ'. No es inválido ni inexistente.\n"
            "El catálogo cubre una fracción mínima de GeneXus, así que "
            "NO_VERIFICABLE es la respuesta ESPERADA para casi todo, y para "
            "construcciones perfectamente válidas.\n"
            "Ante NO_VERIFICABLE o INVÁLIDO, verificá por otra vía antes de "
            "afirmar: (1) leé un objeto de la KB real que ya use la construcción — "
            "mirarlo, no recordarlo; (2) la wiki oficial para la semántica. "
            "Es distinto de search_docs, que RECUPERA prosa: acá hay veredicto "
            "contra catálogo cerrado. Cada token no encontrado se registra en la "
            "cola de curación (con hit_count), así que preguntar sirve incluso "
            "cuando la respuesta es 'no sé'.\n"
            "El catálogo es CHICO y curado a mano (decenas de entradas, no miles), "
            "y su tamaño depende del deployment: por eso NO_VERIFICABLE es la "
            "respuesta esperada. El catálogo de endpoints Bantotal (`bt_apis`) "
            "suele estar vacío, y mientras lo esté `language='bt_api'` sólo puede "
            "devolver NO_VERIFICABLE — usá `list_methods` para ver qué hay."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "snippet": {
                    "type": "string",
                    "description": "Código o snippet a verificar.",
                },
                "language": {
                    "type": "string",
                    "enum": ["gx", "bt_api"],
                    "description": (
                        "Lenguaje/dialecto del snippet. 'bt_api' consulta el "
                        "catálogo de endpoints Bantotal, que está vacío."
                    ),
                    "default": "gx",
                },
                "version": {
                    "type": "string",
                    "description": (
                        "Versión de GeneXus (gx16, gx18). NO filtra ni discrimina: "
                        "el catálogo no registra versión en ninguna entrada, así que "
                        "un VÁLIDO no prueba que la firma sea la de esa versión. Se "
                        "acepta para que quede en la auditoría y la respuesta lo "
                        "aclara."
                    ),
                },
            },
            "required": ["snippet"],
        },
    ),
    Tool(
        name="list_methods",
        description=(
            "Lista las entradas del CATÁLOGO para un object_type.\n"
            "🔴 'Sin resultados' significa 'no hay nada catalogado para ese "
            "object_type', NO 'ese objeto no tiene métodos'. El catálogo está "
            "incompleto, así que una lista vacía —o parcial— es lo normal. "
            "Nunca la presentes como el conjunto completo de métodos de un objeto "
            "ni concluyas de su ausencia que un método no existe.\n"
            "🔴 EL object_type NO ES EL TIPO DE OBJETO GENEXUS. Es la categoría de "
            "la entrada catalogada, y se compara exacto: Method, Event, Command, "
            "Rule, Property. Pedir 'Transaction' o 'Procedure' devuelve vacío "
            "porque no son categorías del catálogo, no porque no haya nada. La "
            "respuesta vacía lista las categorías que existen en este deployment."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "object_type": {
                    "type": "string",
                    "description": (
                        "Categoría de la entrada, exacta: Method | Event | Command "
                        "| Rule | Property."
                    ),
                },
                "version": {
                    "type": "string",
                    "description": (
                        "Versión de GeneXus (gx16, gx18). NO filtra ni discrimina: "
                        "ninguna entrada del catálogo tiene versión registrada, así "
                        "que filtrar devolvería vacío siempre. Se ignora al consultar."
                    ),
                },
            },
            "required": ["object_type"],
        },
    ),
]


# Collections an ingestion call may target. Single source of truth: it feeds the
# add_from_url enum AND the runtime check, so the schema and the validation can
# never drift apart.
WRITABLE_COLLECTIONS = frozenset({"gx_docs", "gx_syntax"})

# Dump parsers exposed through ingest_batch, by `format` value.
DUMP_FORMATS = ("genexus", "workwithplus")


# Write tools — mutate the corpus. Kept OFF the public transport (add_from_url is
# an SSRF vector; ingestion requires a human gate). stdio/admin surface only.
WRITE_TOOLS: list[Tool] = [
    Tool(
        name="add_from_url",
        description=(
            "Ingiere UN artículo de wiki desde su URL: lo scrapea, lo clasifica, "
            "lo guarda en data/scraped/ y lo indexa en el store (Postgres). "
            "Sirve tanto para wiki.genexus.com como para docs.workwithplus.com."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "URL completa del artículo wiki.",
                },
                "collection": {
                    "type": "string",
                    "enum": sorted(WRITABLE_COLLECTIONS),
                    "description": (
                        "Colección destino. Si se omite, la elige classify_genre: "
                        "genre 'reference' → gx_syntax, 'guide' → gx_docs. La "
                        "respuesta informa la colección efectiva y si hubo override."
                    ),
                },
            },
            "required": ["url"],
        },
    ),
    Tool(
        name="ingest_batch",
        description=(
            "Ingiere en lote un DUMP ya scrapeado (un solo archivo de texto con "
            "varios artículos delimitados), no un directorio de .md. Idempotente: "
            "un artículo cuyo contenido no cambió se salta sin re-embeddear. "
            "Corre en dry_run por defecto — hay que pasar dry_run=false para "
            "escribir, que es el gate ante un lote de miles de documentos."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "file": {
                    "type": "string",
                    "description": "Ruta al archivo .txt del dump.",
                },
                "format": {
                    "type": "string",
                    "enum": list(DUMP_FORMATS),
                    "description": (
                        "Formato del dump. 'genexus': bloques "
                        "'--- INÍCIO/FIM DO CONTEÚDO DE: {id}.html ---'. "
                        "'workwithplus': bloques '--- URL: {url} ---' separados por '===='."
                    ),
                },
                "collection": {
                    "type": "string",
                    "description": (
                        "Colección destino. Un dump SIEMPRE entra como genre 'guide' "
                        "(capa de retrieval): es texto plano, demasiado ruidoso para el "
                        "catálogo de veredicto binario, que requiere curación humana."
                    ),
                    "default": "gx_docs",
                },
                "dry_run": {
                    "type": "boolean",
                    "description": (
                        "true (default): parsea y reporta cuántos documentos y chunks "
                        "entrarían, con muestras, SIN escribir nada."
                    ),
                    "default": True,
                },
                "limit": {
                    "type": "integer",
                    "description": "Procesar solo los primeros N documentos del dump.",
                },
            },
            "required": ["file", "format"],
        },
    ),
]


# Tool names allowed on the public internet-facing transport (read-only).
PUBLIC_TOOL_NAMES = frozenset(t.name for t in READ_TOOLS)


# What to DO with each verdict, appended to every verify_syntax response. The
# tool description carries the same rules, but a description is read once when
# choosing the tool while this is read at the moment of concluding — which is
# where a NO_VERIFICABLE gets mistaken for "does not exist".
_VERDICT_GUIDANCE = {
    "VÁLIDO": (
        "→ Confirmado contra el catálogo curado. Podés afirmarlo; citá el 'source'."
    ),
    "INVÁLIDO": (
        "→ NO es prueba de que no exista. Significa: no matcheó exacto y hay "
        "entradas parecidas (mirá 'sugerencias') — señal de TYPO, calculada por "
        "similitud contra un catálogo incompleto. Antes de descartar la "
        "construcción, verificala contra un objeto real de la KB o la wiki oficial."
    ),
    "NO_VERIFICABLE": (
        "→ Es 'NO SÉ', NO 'inválido' ni 'no existe'. El catálogo cubre una "
        "fracción mínima de GeneXus, así que este es el veredicto esperado para "
        "casi todo, incluidas construcciones válidas. Verificá por otra vía: leé "
        "un objeto de la KB que ya use la construcción (mirarlo, no recordarlo) o "
        "la wiki oficial. El token quedó en la cola de curación."
    ),
}


def _verdict_guidance(verdict: object) -> str:
    """Return the next-step guidance for a verify_syntax result.

    Defensive on purpose: an unknown or malformed payload must never mask the
    verdict itself, so it degrades to a conservative line instead of raising.
    """
    key = verdict.get("verdict") if isinstance(verdict, dict) else None
    return _VERDICT_GUIDANCE.get(
        key,
        "→ Veredicto no reconocido: tratalo como NO concluyente y verificá contra "
        "la KB real o la wiki oficial.",
    )


def _reject_unknown_collection(collection: str) -> str | None:
    """Mensaje de rechazo si la colección no existe; None si es válida.

    Se chequea en el handler porque el SDK NO valida el enum del inputSchema. Sin
    esto, un nombre mal escrito viaja al SQL, no matchea nada y vuelve como "Sin
    resultados" / "no encontrado" — indistinguible de una ausencia real, que es
    justo la lectura que el agente propaga aguas abajo.
    """
    if collection in KNOWN_COLLECTIONS:
        return None
    return (
        f"Colección desconocida '{collection}'. "
        f"Válidas: {', '.join(KNOWN_COLLECTIONS)}.\n"
        "Esto NO dice nada sobre el corpus: se rechazó el argumento ANTES de "
        "consultar el store. Corregí el nombre y repetí la llamada."
    )


def _run_ingest_batch(arguments: dict) -> str:
    """Parse a pre-scraped dump and (unless dry_run) index it.

    Business logic lives in src.ingestion.bulk_loader — this only adapts it to the
    MCP transport, which must RETURN text instead of printing it like the
    scripts/ingest_dump.py CLI does.

    Raises FileNotFoundError for a bad path and ValueError for a bad argument, so
    the caller can report the actual cause instead of the generic error (an agent
    that gets "internal error" cannot tell a typo from an outage).
    """
    import itertools

    from src.ingestion.bulk_loader import (
        load_dump,
        parse_genexus_dump,
        parse_wwp_dump,
    )

    parsers = {"genexus": parse_genexus_dump, "workwithplus": parse_wwp_dump}

    path = arguments.get("file", "")
    fmt = arguments.get("format", "")
    collection = arguments.get("collection") or "gx_docs"
    # Default TRUE: the safe branch has to be the one you get by omission.
    dry_run = arguments.get("dry_run", True)
    limit = arguments.get("limit")

    if not path:
        raise ValueError("Falta 'file': ruta al archivo .txt del dump.")
    if fmt not in parsers:
        raise ValueError(
            f"Formato '{fmt}' inválido. Válidos: {', '.join(parsers)}."
        )
    if collection not in WRITABLE_COLLECTIONS:
        raise ValueError(
            f"Colección '{collection}' inválida. "
            f"Válidas: {', '.join(sorted(WRITABLE_COLLECTIONS))}."
        )

    # Propagates FileNotFoundError by design — see docstring.
    with open(path, encoding="utf-8") as fh:
        text = fh.read()

    docs = parsers[fmt](text)
    if limit is not None:
        docs = itertools.islice(docs, limit)

    if dry_run:
        from src.vectorstore.pg_indexer import CHUNK_SIZE

        total = 0
        est_chunks = 0
        samples = []
        for doc in docs:
            total += 1
            est_chunks += max(1, len(doc.content) // CHUNK_SIZE)
            if len(samples) < 5:
                samples.append(doc)

        if total == 0:
            return (
                f"[dry-run] 0 documentos parseados de '{path}'.\n"
                f"El formato '{fmt}' no matcheó ningún bloque: probablemente el dump "
                f"esté en el otro formato. Válidos: {', '.join(parsers)}."
            )

        lines = [
            f"[dry-run] NADA SE ESCRIBIÓ. Esto es lo que entraría:",
            f"  documentos : {total}",
            f"  chunks     : ~{est_chunks} (estimado a {CHUNK_SIZE} chars)",
            f"  colección  : {collection}  (genre 'guide' — capa de retrieval)",
            "  muestras   :",
        ]
        lines += [
            f"    - [{d.page_id}] {d.title[:60]} ({len(d.content)} chars) {d.url}"
            for d in samples
        ]
        lines.append(
            "Para escribir de verdad, repetí la llamada con dry_run=false. "
            "Es idempotente: lo ya indexado sin cambios se salta."
        )
        return "\n".join(lines)

    report = load_dump(docs, collection=collection)
    if report.total == 0:
        return (
            f"0 documentos parseados de '{path}' con formato '{fmt}': "
            f"nada indexado. Revisá que el formato del dump sea el correcto."
        )
    return (
        f"Ingesta OK en '{collection}' — total={report.total} "
        f"indexed={report.indexed} reindexed={report.reindexed} "
        f"skipped={report.skipped}\n"
        f"(skipped = ya estaba indexado con el mismo contenido, no se re-embeddeó.)"
    )


ToolDispatcher = Callable[[str, dict], Awaitable[list[TextContent]]]


def register_tools(
    srv: Server,
    tools: Sequence[Tool],
    dispatch: ToolDispatcher,
) -> None:
    """Cablea tools/list y tools/call en `srv`.

    mcp 2.0 elimino los decoradores @server.list_tools()/@server.call_tool() en
    favor de add_request_handler(method, params_type, handler), donde el handler
    recibe (contexto, params ya validados) y devuelve el Result completo en vez
    de la lista pelada.

    Se centraliza aca porque hay dos servidores que exponen el mismo par de
    handlers con distinta politica: el de stdio (read + write) y el publico
    (read-only, en http_server.build_public_server). Duplicar el cableado en
    ambos era el camino a que uno quede desincronizado.
    """

    async def _list(_ctx, _params: PaginatedRequestParams) -> ListToolsResult:
        return ListToolsResult(tools=list(tools))

    async def _call(_ctx, params: CallToolRequestParams) -> CallToolResult:
        # arguments es opcional en el protocolo; los dispatchers esperan un dict.
        content = await dispatch(params.name, params.arguments or {})
        return CallToolResult(content=list(content))

    srv.add_request_handler("tools/list", PaginatedRequestParams, _list)
    srv.add_request_handler("tools/call", CallToolRequestParams, _call)


# Client-facing error text. Detail is logged server-side, never returned — the
# public transport must not leak internal paths, SQL, or library internals.
GENERIC_ERROR = "Error interno procesando la solicitud."


async def dispatch_tool(name: str, arguments: dict) -> list[TextContent]:
    """Execute a tool by name. Shared by the stdio and public HTTP transports."""
    # Log argument KEYS only — values may carry PII on now-public traffic.
    log.info("call_tool: %s (args: %s)", name, sorted(arguments))

    if name == "search_docs":
        from src.vectorstore.pg_retriever import hybrid_search
        from src.vectorstore.pg_collections import collection_counts

        q = arguments.get("query", "")
        col = arguments.get("collection", "gx_docs")
        k = arguments.get("top_k", 5)
        snippet_chars = arguments.get("snippet_chars", 600)
        min_score = arguments.get("min_score")
        # Antes de embeddear: rechazar acá evita además pagar la carga de e5-large
        # (~2.2 GB en frío) por un argumento que ya sabemos inválido.
        if rejection := _reject_unknown_collection(col):
            return [TextContent(type="text", text=rejection)]
        try:
            results = hybrid_search(q, collection=col, top_k=k, min_score=min_score)
            if not results:
                # Guide the caller to a collection that actually has content.
                populated = []
                try:
                    populated = [f"{n} ({c} chunks)" for n, c in collection_counts()]
                except Exception:
                    pass
                hint = (
                    f" Colecciones con contenido: {', '.join(populated)}."
                    if populated
                    else " No hay colecciones con contenido; ingestá con add_from_url."
                )
                return [
                    TextContent(type="text", text=f"Sin resultados en '{col}'.{hint}")
                ]
            lines = [f"## Resultados ({len(results)} found)\n"]
            for r in results:
                content = r["content"] or ""
                snippet = content[:snippet_chars]
                truncated = "…" if len(content) > snippet_chars else ""
                lines.append(
                    f"**{r['title']}** (score={r['score']:.3f})\n"
                    f"doc_id: `{r['doc_id']}`\n"
                    f"article_id: `{r.get('id', '')}`\n"
                    f"Fuente: {r.get('url') or r.get('source', '?')}\n"
                    f"Hash: {r.get('content_hash', '')}\n"
                    f"{snippet}{truncated}\n"
                )
            return [TextContent(type="text", text="\n".join(lines))]
        except Exception:
            log.exception("search_docs error")
            return [TextContent(type="text", text=GENERIC_ERROR)]

    elif name == "get_document":
        from src.vectorstore.pg_collections import get_document

        doc_id = arguments.get("doc_id", "")
        collection = arguments.get("collection", "gx_docs")
        if rejection := _reject_unknown_collection(collection):
            return [TextContent(type="text", text=rejection)]
        try:
            doc = get_document(doc_id, collection=collection)
            if not doc:
                return [
                    TextContent(
                        type="text",
                        text=(
                            f"No hay ningún chunk con id '{doc_id}' en "
                            f"'{collection}'.\n"
                            "Verificá que sea un doc_id de search_docs (id de "
                            "CHUNK) y no un article_id — ese va a get_article."
                        ),
                    )
                ]
            return [
                TextContent(
                    type="text",
                    # Rotulado como fragmento: sin esto el agente lee 800 chars y
                    # concluye que el articulo entero dice solo eso.
                    text=f"# {doc['title']} — FRAGMENTO (1 chunk, no el artículo)\n\n"
                    f"**Fuente:** {doc.get('source', '')}\n"
                    f"**Colección:** {doc.get('collection', '')}\n"
                    f"**Hash:** {doc.get('content_hash', '')}\n"
                    f"_Para el documento completo: get_article con su article_id._\n\n"
                    f"{doc.get('content', '')}",
                )
            ]
        except Exception:
            log.exception("%s failed", name)
            return [TextContent(type="text", text=GENERIC_ERROR)]

    elif name == "get_article":
        from src.vectorstore.pg_collections import get_article

        article_id = arguments.get("article_id", "")
        collection = arguments.get("collection", "gx_docs")
        if rejection := _reject_unknown_collection(collection):
            return [TextContent(type="text", text=rejection)]
        try:
            art = get_article(article_id, collection=collection)
            if not art:
                return [TextContent(type="text", text="Artículo no encontrado.")]
            return [
                TextContent(
                    type="text",
                    text=f"# {art['title']} ({art['chunks']} chunks)\n\n"
                    f"**Fuente:** {art.get('url') or art.get('source', '')}\n"
                    f"**Colección:** {art.get('collection', '')}\n"
                    f"**Hash:** {art.get('content_hash', '')}\n\n"
                    f"{art.get('content', '')}",
                )
            ]
        except Exception:
            log.exception("%s failed", name)
            return [TextContent(type="text", text=GENERIC_ERROR)]

    elif name == "verify_syntax":
        from src.syntax_db.gx_repository import verify_syntax

        snippet = arguments.get("snippet", "")
        lang = arguments.get("language", "gx")
        version = arguments.get("version")
        try:
            verdict = verify_syntax(snippet, language=lang, version=version)
            return [
                TextContent(type="text", text=f"{verdict}\n{_verdict_guidance(verdict)}")
            ]
        except Exception:
            log.exception("%s failed", name)
            return [TextContent(type="text", text=GENERIC_ERROR)]

    elif name == "list_methods":
        from src.syntax_db.gx_repository import list_methods, list_object_types

        obj_type = arguments.get("object_type", "")
        version = arguments.get("version")
        try:
            methods = list_methods(obj_type, version=version)
            if not methods:
                # Los object_type REALES, consultados al catálogo. Antes acá se
                # sugerían 'Transaction' y 'Procedure', que no tienen una sola
                # fila: la tool mandaba al agente a repetir la consulta con un
                # valor que garantiza vacío, y el segundo vacío ya se lee como
                # "el catálogo no sabe nada de esto".
                disponibles = ", ".join(list_object_types()) or "(catálogo vacío)"
                return [
                    TextContent(
                        type="text",
                        text=(
                            f"Sin entradas catalogadas para object_type='{obj_type}'.\n"
                            "Esto NO significa que ese objeto no tenga métodos: el "
                            "catálogo está incompleto y una lista vacía es lo normal. "
                            "No concluyas de esta respuesta que un método no existe — "
                            "verificá contra un objeto real de la KB o la wiki oficial.\n"
                            f"object_type disponibles en el catálogo: {disponibles}. "
                            "Se compara exacto."
                        ),
                    )
                ]
            return [
                TextContent(
                    type="text",
                    text=f"Métodos catalogados para '{obj_type}' ({len(methods)}):\n"
                    + "\n".join(f"- {m}" for m in methods)
                    + "\n(Lista PARCIAL: es lo que hay en el catálogo, no el conjunto "
                    "completo de métodos del objeto. La ausencia no prueba nada.)",
                )
            ]
        except Exception:
            log.exception("%s failed", name)
            return [TextContent(type="text", text=GENERIC_ERROR)]

    elif name == "add_from_url":
        from scripts.scrape_wiki import scrape
        from src.ingestion.ingestor import ingest

        url = arguments.get("url", "")
        if not url:
            return [TextContent(type="text", text="URL requerida.")]

        override = arguments.get("collection")
        if override and override not in WRITABLE_COLLECTIONS:
            return [
                TextContent(
                    type="text",
                    text=f"Colección '{override}' inválida. "
                    f"Válidas: {', '.join(sorted(WRITABLE_COLLECTIONS))}.",
                )
            ]
        try:
            article = scrape(url)
            auto = article.metadata.destination
            # El override del schema tiene que ganarle a classify_genre: hasta
            # 2026-08-11 se declaraba y se ignoraba, y la tool decia haber escrito
            # donde el llamador pidio cuando en realidad escribio donde clasifico.
            if override:
                article.metadata.destination = override
            result = ingest(article)

            routing = f"Colección: {result['destination']}"
            if override:
                routing += (
                    f"  (override explícito; classify_genre proponía '{auto}')"
                    if override != auto
                    else "  (override coincide con classify_genre)"
                )
            else:
                routing += f"  (elegida por classify_genre desde genre '{result['genre']}')"

            return [
                TextContent(
                    type="text",
                    text=f"Scraped: {result['title']}\n"
                    f"Género: {result['genre']}\n"
                    f"{routing}\n"
                    f"Chunks indexados: {result['chunks_indexed']}\n"
                    f"Hash: {result['content_hash'][:16]}...\n"
                    f"Palabras: {result['word_count']}\n"
                    f"Saved: {result['md_path']}",
                )
            ]
        except Exception:
            log.exception("add_from_url error")
            return [TextContent(type="text", text=GENERIC_ERROR)]

    elif name == "ingest_batch":
        try:
            return [TextContent(type="text", text=_run_ingest_batch(arguments))]
        except FileNotFoundError:
            return [
                TextContent(
                    type="text",
                    text=f"No existe el archivo: {arguments.get('file', '')}",
                )
            ]
        except ValueError as e:
            return [TextContent(type="text", text=str(e))]
        except Exception:
            log.exception("ingest_batch error")
            return [TextContent(type="text", text=GENERIC_ERROR)]

    else:
        return [TextContent(type="text", text=f"Tool '{name}' no implementada.")]


# El cableado va aca, despues de dispatch_tool: register_tools recibe la funcion
# ya definida. Con los decoradores esto quedaba arriba porque el cuerpo del
# handler resolvia el nombre recien al invocarse.
register_tools(server, [*READ_TOOLS, *WRITE_TOOLS], dispatch_tool)


# ─── Entry point ──────────────────────────────────────────────────────────────


async def main():
    log.info(f"{APP_NAME} v{VERSION} starting (stdio)...")
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            server.create_initialization_options(),
        )


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())
