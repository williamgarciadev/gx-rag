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

import json
import logging
import re

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

# A nivel de modulo y no diferido como el resto de los src.*: los enums de las
# secciones y los tipos sugeridos se evaluan al CONSTRUIR el inputSchema, que pasa al
# importar. Ambos modulos son livianos —kb_fichas solo trae el splitter de
# langchain-text-splitters, sin modelos— y kb_loader difiere pg_indexer, asi que esto
# no arrastra Postgres ni e5 al arranque.
from src.ingestion import kb_fichas
from src.ingestion.kb_loader import KB_MAX_INLINE

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
KNOWN_COLLECTIONS = ("gx_docs", "bt_docs", "gx_syntax", "bt_apis", "gx_kb_objects")

# La coleccion de objetos de Knowledge Base exige `kb_name` en los filtros. Es lo que
# hace imposible mezclar dos KBs sin darse cuenta: un hit de la KB equivocada haria
# que el agente escriba codigo con nombres de objetos que no existen en la KB activa,
# y eso es PEOR que no recuperar nada. El CHECK de sql/003 garantiza que la clave
# exista en cada fila; esto garantiza que la consulta la use.
KB_COLLECTION = "gx_kb_objects"
COLLECTIONS_REQUIRING_KB_NAME = frozenset({KB_COLLECTION})


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
            "(la mejor fuente para '¿es un bug conocido?' y sus workarounds) y la wiki "
            "de WorkWithPlus. **Los tickets SAC son MÁS DE LA MITAD del corpus** y la "
            "wiki de WorkWithPlus apenas una fracción chica.\n"
            "🔴 POR ESO, USÁ `filters={\"origin\": [...]}`. Valores: `gx_wiki` (wiki "
            "GeneXus) · `sac` (tickets) · `wwp` (WorkWithPlus). Toda fila tiene la "
            "clave, así que un filtro que no matchea significa 'ese origen no lo "
            "tiene', nunca 'no está clasificado'.\n"
            "Medido: la consulta 'como configurar el pattern en una transaccion' "
            "SIN filtro devuelve 10 de 10 hits de SAC — puros tickets de incidentes — "
            "mientras la documentación que responde la pregunta existe y queda fuera "
            "del top. Con `origin=gx_wiki` aparece primera. El ranking solo NO alcanza "
            "cuando una fuente es la mitad del corpus.\n"
            "🔴 Y ojo con el producto: GeneXus tiene un pattern nativo 'Work With for "
            "Web', y WorkWithPlus (DVelop) es OTRO producto. Solo `origin=wwp` aplica "
            "a una KB con WWP aplicado. El riesgo no es quedarte sin respuesta: es "
            "tomar la del producto equivocado.\n"
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
                        "(=1/61) significa que ESE chunk apareció en UNA sola de las "
                        "dos mitades, en rank 1.\n"
                        "🔴 ESO NO PRUEBA QUE EL TEMA FALTE, y leerlo así es el error "
                        "fácil. Las dos mitades rankean con criterios distintos "
                        "(distancia coseno vs ts_rank_cd), así que ES NORMAL que sus "
                        "listas no se solapen: 30 resultados densos excelentes y 30 "
                        "léxicos excelentes que no comparten ningún chunk dan un "
                        "máximo de 0.0164 igual. El score alto no aparece por "
                        "relevancia sino por COINCIDENCIA entre las dos mitades — es "
                        "señal de consenso, no de calidad, y su ausencia no es "
                        "evidencia de nada sobre el corpus.\n"
                        "Para decidir si un tema está: subí top_k y reformulá con "
                        "términos del dominio. No lo deduzcas del score."
                    ),
                },
                "filters": {
                    "type": "object",
                    "description": (
                        "Filtro por metadatos del chunk. Valor: string o lista de "
                        "strings (OR entre los valores de una clave, AND entre "
                        "claves). Comparación EXACTA, sin normalizar.\n"
                        "Claves: kb_name, obj_type, obj_name, chunk_kind, module, "
                        "origin, related.\n"
                        "En 'gx_docs' la clave útil es `origin` (gx_wiki | sac | wwp): "
                        "es la forma mecánica de resolver la mezcla de fuentes en vez "
                        "de mirar la url de cada hit a ojo.\n"
                        "En 'gx_kb_objects' (objetos de una Knowledge Base GeneXus) "
                        "`kb_name` es OBLIGATORIO: sin él la búsqueda se rechaza, "
                        "porque un hit de otra KB haría que escribas código con "
                        "nombres de objetos que no existen en la KB activa.\n"
                        "🔴 Una clave desconocida o una lista vacía se RECHAZAN, no se "
                        "ignoran: devolver resultados sin filtrar que el llamador cree "
                        "filtrados es peor que un error.\n"
                        "Los filtros se aplican DENTRO de cada mitad del híbrido, así "
                        "que un filtro selectivo no degrada el recall — pero sí sube "
                        "los scores en promedio, así que un min_score calibrado sin "
                        "filtros no es transferible tal cual."
                    ),
                },
                "rerank": {
                    "type": "boolean",
                    "description": (
                        "Opcional. Si es true, reordena los mejores candidatos mediante un modelo "
                        "Cross-Encoder de atención completa, refinando la precisión semántica final."
                    ),
                    "default": False,
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
            "(SQL). Devuelve VÁLIDO / PISTA / INVÁLIDO / NO_VERIFICABLE.\n"
            "🔴 CÓMO LEER EL VEREDICTO — aquí se cometen los errores más caros. "
            "NINGUNO es simétrico: solo VÁLIDO es una afirmación.\n"
            "  VÁLIDO         = está en el catálogo. Afírmalo, con su source. Antes,\n"
            "                   compara el `name` devuelto con lo que preguntaste:\n"
            "                   si es una entrada más larga que solo CONTIENE tu\n"
            "                   término, el acierto es parcial y no lo afirmes.\n"
            "  PISTA          = está en el catálogo, pero preguntaste por una versión\n"
            "                   anterior a la de sus fuentes (GeneXus 18). Es un\n"
            "                   indicio, no un veredicto: confírmalo contra la KB de\n"
            "                   esa versión o compilando antes de usarlo.\n"
            "  INVÁLIDO       = NO coincidió exacto Y existen entradas PARECIDAS. Es\n"
            "                   un detector de TYPO (mira 'sugerencias'), NO una\n"
            "                   prueba de que la construcción no exista. Se calcula\n"
            "                   por similitud contra un catálogo incompleto: un\n"
            "                   nombre válido no catalogado que se parezca a otra\n"
            "                   entrada cae aquí. Tómalo como '¿quisiste decir X?'.\n"
            "  NO_VERIFICABLE = 'NO SÉ'. No es inválido ni inexistente.\n"
            "QUÉ HAY: los métodos de los tipos de dato del lenguaje (DateTime, "
            "Collection, File, BusinessComponent…), extraídos del gxtypes.xml de un "
            "install de GeneXus 18, y comandos, funciones y eventos de la wiki. Son "
            "cientos de entradas, no miles, y el tamaño depende del deployment: "
            "GeneXus es mucho más grande, así que NO_VERIFICABLE es la respuesta "
            "ESPERADA para muchas construcciones perfectamente válidas.\n"
            "Ante NO_VERIFICABLE o INVÁLIDO, verifica por otra vía antes de "
            "afirmar: (1) lee un objeto de la KB real que ya use la construcción — "
            "mirarlo, no recordarlo; (2) la wiki oficial para la semántica. "
            "Es distinto de search_docs, que RECUPERA prosa: aquí hay veredicto "
            "contra catálogo cerrado. Cada token no encontrado se registra en la "
            "cola de curación (con hit_count), así que preguntar sirve incluso "
            "cuando la respuesta es 'no sé'.\n"
            "El catálogo de endpoints Bantotal (`bt_apis`) suele estar vacío, y "
            "mientras lo esté `language='bt_api'` solo puede devolver "
            "NO_VERIFICABLE — usa `list_methods` para ver qué hay."
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
                        "Versión de GeneXus (gx16, gx18). SÍ cambia el veredicto: "
                        "las fuentes del catálogo son de GeneXus 18, así que con una "
                        "versión anterior (gx16, la de las KBs Bantotal) un acierto "
                        "sale PISTA y no VÁLIDO. Ninguna entrada registra desde qué "
                        "versión existe, así que tampoco discrimina upgrades: un "
                        "VÁLIDO con gx18 no prueba que el método exista en tu Upgrade."
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
            "🔴 EL object_type NO ES EL TIPO DE OBJETO GENEXUS (Transaction, "
            "Procedure…). Hay dos familias, y se compara exacto: (1) TIPOS DE DATO "
            "del lenguaje, con sus métodos — DateTime, Date, Character, Varchar, "
            "Collection, File, BusinessComponent, HttpClient, XMLReader…; (2) "
            "categorías de la wiki — Method, Event, Command, Rule, Property. Pedir "
            "'Transaction' o 'Procedure' devuelve vacío porque no son valores del "
            "catálogo, no porque no haya nada. La respuesta vacía lista TODOS los "
            "object_type que existen en este deployment: úsala para elegir."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "object_type": {
                    "type": "string",
                    "description": (
                        "Valor exacto: un tipo de dato del lenguaje (DateTime, "
                        "Collection, File, BusinessComponent…) o una categoría de "
                        "la wiki (Method, Event, Command, Rule, Property)."
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
    Tool(
        name="ingest_kb_objects",
        description=(
            "Indexa fichas YA CONSTRUIDAS de objetos de una Knowledge Base GeneXus "
            f"en la colección '{KB_COLLECTION}'. Idempotente por objeto.\n"
            "EMPEZÁ POR EL DRY RUN, que es el default, porque te dice algo que "
            "necesitás y no sabés: CUÁLES de las fichas que le pasás son realmente "
            "nuevas. Compara el hash de procedencia de cada una contra lo indexado, "
            "con un solo SELECT y sin embeddear nada. Si navegaste 12 objetos y 9 no "
            "cambiaron, el dry run te lo dice y escribís 3.\n"
            "🔴 ESTA TOOL NO LEE LA KB. No abre GeneXus, no le habla al MCP de "
            "GeneXus, y NO verifica que la ficha describa el objeto real: indexa el "
            "texto que le pasás. Si la ficha está mal, queda mal indexada y una "
            "búsqueda posterior la va a devolver como si fuera verdad.\n"
            "🔴 `kb_hash` es la clave de idempotencia y debería ser el hash del "
            "SOURCE del objeto en la KB, no de la ficha. Si lo omitís, cada corrida "
            "con prosa distinta cuenta como cambio y se re-embeddea todo de nuevo.\n"
            "🔴 NO acepta una ruta de archivo, a propósito. La carga masiva inicial "
            "la hace scripts/bootstrap_kb.py, que llama a la misma función de "
            "librería sin pasar por MCP. Esta tool es para el flujo incremental: "
            f"navegás objetos, armás sus fichas y las pasás inline (máximo "
            f"{KB_MAX_INLINE} por llamada; arriba de eso se RECHAZA la llamada "
            "entera, no se trunca).\n"
            "Las fichas van en PROSA ESTRUCTURADA por secciones, no XML ni Source "
            "crudo: el modelo de embeddings es de texto natural, así que un "
            "PatternInstance pegado tal cual no aporta a la mitad densa.\n"
            f"La colección '{KB_COLLECTION}' NO se sirve en el endpoint público de "
            "internet: es la KB interna, no documentación."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "kb_name": {
                    "type": "string",
                    "description": (
                        "KB de origen, ej. MatikaErp_3003. Sale de "
                        "genexus_whoami → kb.active. Obligatorio: sin él no hay "
                        "identidad estable y dos KBs con un objeto homónimo se "
                        "sobreescribirían."
                    ),
                },
                "objects": {
                    "type": "array",
                    "maxItems": KB_MAX_INLINE,
                    "description": (
                        "Fichas a indexar. El article_id se DERIVA de "
                        "kb_name + obj_type + obj_name; no lo mandes."
                    ),
                    "items": {
                        "type": "object",
                        "properties": {
                            "obj_name": {"type": "string"},
                            "obj_type": {
                                "type": "string",
                                "description": (
                                    "Tipo GeneXus. Sugeridos: "
                                    + " | ".join(kb_fichas.KB_OBJ_TYPES_SUGERIDOS)
                                    + ". No es un enum cerrado: la KB tiene decenas "
                                    "de tipos y se acepta cualquiera."
                                ),
                            },
                            "module": {"type": "string"},
                            "kb_hash": {
                                "type": "string",
                                "description": (
                                    "Hash del source del objeto EN LA KB. Es la clave "
                                    "de idempotencia — ver la nota de la tool."
                                ),
                            },
                            "related": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": "Nombres de objetos relacionados.",
                            },
                            "sections": {
                                "type": "array",
                                "minItems": 1,
                                "description": (
                                    "Secciones de la ficha. `kind` alimenta el filtro "
                                    "chunk_kind de search_docs."
                                ),
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "kind": {
                                            "type": "string",
                                            "enum": list(kb_fichas.KB_CHUNK_KINDS),
                                        },
                                        "text": {"type": "string"},
                                    },
                                    "required": ["kind", "text"],
                                },
                            },
                        },
                        "required": ["obj_name", "obj_type", "sections"],
                    },
                },
                "dry_run": {
                    "type": "boolean",
                    "default": True,
                    "description": (
                        "true (default): valida y reporta POR FICHA qué pasaría — "
                        "would_index / would_reindex / skipped / invalid — sin "
                        "escribir ni embeddear. false: escribe."
                    ),
                },
                "on_invalid": {
                    "type": "string",
                    "enum": ["stop", "skip"],
                    "default": "stop",
                    "description": (
                        "Qué hacer con una ficha que no valida. 'stop' (default): no "
                        "escribe NADA y te dice cuál falló — en un lote, seguir de "
                        "largo deja la colección con agujeros que después nadie "
                        "distingue de objetos que la KB no tiene. 'skip': escribe las "
                        "válidas y lista TODAS las salteadas con su motivo."
                    ),
                },
            },
            "required": ["kb_name", "objects"],
        },
    ),
]


# Tool names allowed on the public internet-facing transport (read-only).
PUBLIC_TOOL_NAMES = frozenset(t.name for t in READ_TOOLS)


# Colecciones que NO se sirven en el transporte público, y su complemento.
#
# POR QUE HACE FALTA, y por qué no alcanza PUBLIC_TOOL_NAMES: ese frozenset cubre
# la dimensión TOOL, y del lado write está bien cubierto. Pero `search_docs` YA es
# público y recibe `collection` como PARAMETRO, validado solo contra
# KNOWN_COLLECTIONS. O sea: sumar un nombre a esa tupla alcanzaba para publicar
# una colección en internet, sin tocar http_server.py y sin que ningún test se
# ponga rojo. El contenido de una KB de banca no puede depender de eso.
#
# SE DECLARA POR SUSTRACCION, no por enumeración de lo público: el default seguro
# tiene que ser "no se publica". Con una lista blanca, agregar una colección y
# olvidarse de la lista la deja pública; con esta forma, la deja privada.
#
# gx_kb_objects figura acá ANTES de existir en KNOWN_COLLECTIONS, a propósito: la
# política precede a la capacidad, así no hay una ventana entre el commit que crea
# la colección y el commit que la protege.
NON_PUBLIC_COLLECTIONS = frozenset({"gx_kb_objects"})
PUBLIC_COLLECTIONS = frozenset(KNOWN_COLLECTIONS) - NON_PUBLIC_COLLECTIONS


# Qué significa cada estado de la ingesta de fichas, adjunto a TODA respuesta de
# ingest_kb_objects. Mismo principio que _VERDICT_GUIDANCE: la descripción se lee una
# vez al elegir la tool, esto se lee en el momento de CONCLUIR — que es donde un
# "skipped" se confunde con un fallo y dispara un reintento inútil.
_INGEST_STATUS_GUIDANCE = {
    "indexed": "no había ficha de ese objeto. Se escribió.",
    "reindexed": (
        "había una con otro hash de procedencia. Se borraron sus chunks viejos y se "
        "escribió la nueva, en UNA transacción: el objeto nunca quedó a medias."
    ),
    "skipped": (
        "ya estaba con el MISMO hash. NO es error, NO es que la ficha esté mal, y NO "
        "hace falta repetir la llamada: es idempotencia. Si esperabas un cambio y "
        "salió skipped, lo que no cambió es el kb_hash que MANDASTE — revisá cómo lo "
        "calculás, no reintentes."
    ),
    "would_index": "en una escritura real entraría como nueva.",
    "would_reindex": "en una escritura real reemplazaría la versión indexada.",
    "invalid": (
        "la ficha NO se indexó y NO está en el store. Ver la sección de problemas: es "
        "tu argumento, no el servidor."
    ),
    "error": "falló al escribir. Esa ficha NO está en el store.",
}


def _render_ingest_report(reporte) -> str:
    """Texto del reporte de ingesta. Lo lee un LLM, no un humano.

    Decisiones de forma: totales PRIMERO (una lectura truncada igual se lleva el
    resumen), una ficha por línea con el estado en vocabulario fijo y columna
    alineada, el glosario de estados en la MISMA respuesta, los problemas en sección
    propia y nunca abreviados, y el cierre con la verificación independiente que exige
    la Hard Rule del repo (el retorno de una tool no describe el store).
    """
    modo = "DRY RUN (no se escribió nada)" if reporte.dry_run else "ESCRITO"
    estados = [
        "indexed", "reindexed", "skipped", "would_index", "would_reindex",
        "invalid", "error",
    ]
    conteos = " ".join(
        f"{e}={reporte.contar(e)}" for e in estados if reporte.contar(e)
    )
    lineas = [
        f"INGESTA {KB_COLLECTION} — kb={reporte.kb_name} — {modo}",
        f"total={reporte.total} {conteos}",
        "",
    ]

    for r in reporte.results:
        if r.status in ("invalid", "error"):
            continue
        detalle = ""
        if r.chunks:
            kinds = ",".join(r.kinds)
            detalle = f"  {r.chunks} chunks ({kinds})"
        lineas.append(f"{r.status:14s} {r.article_id}{detalle}")

    problemas = [r for r in reporte.results if r.status in ("invalid", "error")]
    if problemas:
        nada = (
            " NADA se escribió (on_invalid=stop). Corregí y repetí la llamada entera:"
            if reporte.dry_run is False and reporte.contar("indexed") == 0
            else ""
        )
        lineas += ["", f"PROBLEMAS ({len(problemas)}).{nada}"]
        for r in problemas:
            # Identificado por INDICE cuando no hay article_id: una ficha sin
            # obj_name/obj_type no tiene identidad, y el índice en el array es lo único
            # que el llamador puede mapear a lo que mandó.
            quien = r.article_id or f"objects[{r.index}]"
            lineas.append(f"{r.status:14s} {quien}: {r.reason}")

    presentes = {r.status for r in reporte.results}
    glosario = [
        f"  {e:14s} {_INGEST_STATUS_GUIDANCE[e]}"
        for e in estados
        if e in presentes and e in _INGEST_STATUS_GUIDANCE
    ]
    if glosario:
        lineas += ["", "QUÉ SIGNIFICA CADA ESTADO — no los interpretes por el nombre:"]
        lineas += glosario

    if reporte.sin_kb_hash:
        lineas += [
            "",
            f"⚠ {reporte.sin_kb_hash} ficha(s) sin `kb_hash`. La idempotencia de esas "
            "queda colgada de que vuelvas a generar la MISMA prosa: si la redactás "
            "distinto, la próxima corrida las cuenta como cambio y se re-embeddean. "
            "Pasá el hash del source del objeto en la KB.",
        ]

    if not reporte.dry_run and reporte.contar("indexed") + reporte.contar("reindexed"):
        primero = next(
            (r for r in reporte.results if r.status in ("indexed", "reindexed")), None
        )
        lineas += [
            "",
            "VERIFICACIÓN INDEPENDIENTE — este texto es un valor de retorno, no el "
            "estado del store:",
            f'  get_article article_id="{primero.article_id}" '
            f'collection="{KB_COLLECTION}"',
            "  Es una lectura determinista por colección. NO uses search_docs para "
            "comprobar pertenencia: es un ranking, y no encontrar algo ahí no prueba "
            "que no esté.",
        ]

    return "\n".join(lineas)


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
    "PISTA": (
        "→ No es un veredicto definitivo: es un indicio. Figura en el catálogo, "
        "que se nutre de fuentes gx18 y no trae columna de versión. Confirmalo "
        "contra la KB de esa versión (MCP genexus16) o compilando, antes de usarlo."
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


def _reject_missing_kb_name(collection: str, filters: dict | None) -> str | None:
    """Mensaje de rechazo si falta `kb_name` en una colección que lo exige.

    Es la barrera contra el peor modo de falla de una colección multi-KB: recuperar la
    ficha de un objeto de OTRA KB y escribir código con nombres que no existen en la
    KB activa. Un aislamiento que depende de que el llamador se acuerde de filtrar no
    es un aislamiento; esto lo vuelve un error ruidoso.
    """
    if collection not in COLLECTIONS_REQUIRING_KB_NAME:
        return None
    if filters and filters.get("kb_name"):
        return None

    disponibles = ""
    try:
        from src.vectorstore.pg_collections import meta_facets

        facetas = meta_facets(collection, ("kb_name",))
        if facetas.get("kb_name"):
            nombres = " · ".join(f"{v} ({n} chunks)" for v, n in facetas["kb_name"])
            disponibles = f"\nKBs indexadas en esta colección: {nombres}"
    except Exception:  # pragma: no cover - el mensaje no puede depender de la DB
        pass

    return (
        f"Falta el filtro obligatorio 'kb_name' para la colección '{collection}'.\n"
        "Esta colección puede contener objetos de MAS DE UNA Knowledge Base, y un "
        "resultado de la KB equivocada te haría escribir código con nombres de objetos "
        "que no existen en la KB activa — peor que no recuperar nada.\n"
        'Pasá filters={"kb_name": "<nombre>"}. Si no sabés cuál es la KB activa, '
        "consultá genexus_whoami en el MCP de GeneXus (campo kb.active)."
        f"{disponibles}\n"
        "NOTA: esto se rechazó ANTES de consultar el store. No dice nada sobre su "
        "contenido."
    )


def _meta_line(meta: dict | None) -> str:
    """Línea de metadatos del hit, o vacío. Genérica: no ramifica por colección.

    Un hit de KB muestra obj_name/obj_type/module; uno de wiki no muestra nada, porque
    su `meta` es {}. `related` se omite: es un array y ensuciaría la línea.
    """
    if not meta:
        return ""
    pares = sorted(
        (k, v) for k, v in meta.items() if k != "related" and isinstance(v, str)
    )
    if not pares:
        return ""
    return "Meta: " + " · ".join(f"{k}={v}" for k, v in pares) + "\n"


def _empty_search_text(collection: str, filters: dict | None) -> str:
    """Texto de "sin resultados", consciente de si había filtros activos.

    POR QUE DOS RAMAS: sin filtros, la causa más común es haber apuntado a una
    colección vacía, y el hint correcto es qué colecciones tienen contenido. CON
    filtros, ese mismo texto es ENGAÑOSO — la colección sí tiene contenido, el filtro
    es lo que no matcheó — y llevaría al llamador a concluir sobre el corpus cuando el
    problema está en su argumento (un 'Transacion' con una sola c).
    """
    from src.vectorstore.pg_collections import collection_counts, meta_facets

    if filters:
        activos = " · ".join(f"{k}={v}" for k, v in sorted(filters.items()))
        lineas = [
            f"Sin resultados en '{collection}' CON LOS FILTROS APLICADOS.",
            f"Filtros activos: {activos}",
        ]
        try:
            total = dict(collection_counts()).get(collection, 0)
            if total:
                lineas.append(
                    f"La colección NO está vacía ({total} chunks): el filtro es lo que "
                    "no matcheó. La comparación es EXACTA, sin normalizar."
                )
            facetas = meta_facets(collection, tuple(filters.keys()))
            for clave, valores in facetas.items():
                muestra = " · ".join(f"{v} ({n})" for v, n in valores)
                lineas.append(f"  {clave} que EXISTEN: {muestra}")
        except Exception:  # pragma: no cover
            pass
        lineas.append(
            "Si ninguno de esos valores es el que buscabas, el filtro puede ser "
            "correcto y el tema realmente no estar indexado — pero verificalo sin "
            "filtros antes de afirmarlo."
        )
        return "\n".join(lineas)

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
    return f"Sin resultados en '{collection}'.{hint}"


def reject_non_public_collection(collection: str | None) -> str | None:
    """Mensaje de rechazo si la colección no se sirve en público; None si sí.

    Pública (sin `_`) porque la consume http_server: es el gate de la dimensión
    COLECCION, complementario al de la dimensión TOOL que ya hace PUBLIC_TOOL_NAMES.
    Función de módulo y no un chequeo inline en el closure para que sea testeable
    sin levantar el transporte, siguiendo el patrón de _reject_unknown_collection.

    `None` (colección no pasada) NO se rechaza: cae en el default del schema, que
    siempre es una colección de documentación.
    """
    if collection is None or collection in PUBLIC_COLLECTIONS:
        return None
    return (
        f"Colección '{collection}' no disponible en el endpoint público. "
        f"Disponibles: {', '.join(sorted(PUBLIC_COLLECTIONS))}.\n"
        # Sin esta línea un rechazo de POLITICA se lee como una colección vacía, y
        # el agente concluye sobre el corpus a partir de una decisión de acceso.
        "Esto NO dice nada sobre su contenido: se rechazó ANTES de consultar el "
        "store. No concluyas que está vacía ni que el tema no existe."
    )


_CHUNK_ID = re.compile(r"_c\d+$")


def _article_not_found_text(article_id: str, collection: str) -> str:
    """Texto de get_article sin hit: dice POR QUÉ, no solo que no está.

    El default de `collection` es gx_docs, así que el fallo típico es un artículo
    de bt_docs pedido sin la colección: «Artículo no encontrado.» a secas se leía
    como «ese documento no existe». Busca el id en las demás colecciones públicas
    y declara si la consultada está vacía o si el id es de un chunk.
    """
    from src.vectorstore.pg_collections import collection_counts, get_article

    lineas = [f"No hay ningún artículo con id '{article_id}' en '{collection}'."]
    otras = [c for c in sorted(PUBLIC_COLLECTIONS)
             if c != collection and get_article(article_id, collection=c)]
    if otras:
        lineas.append(
            f"SÍ está en: {', '.join(otras)}. Repite la llamada con "
            f"collection='{otras[0]}' (el default es gx_docs)."
        )
    elif dict(collection_counts()).get(collection, 0) == 0:
        lineas.append(f"La colección '{collection}' está vacía en este deployment.")
    if _CHUNK_ID.search(article_id):
        lineas.append(
            "Ese id parece de un CHUNK (termina en _cN): eso va a get_document. "
            "El article_id es el mismo sin el sufijo."
        )
    if not otras:
        lineas.append(
            "No concluyas que el documento no existe: verifica el id contra el "
            "article_id que devolvió search_docs."
        )
    return "\n".join(lineas)


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


def _run_ingest_kb_objects(arguments: dict) -> str:
    """Adaptador MCP delgado sobre kb_loader.load_fichas.

    Toda la lógica vive en la librería, que el bootstrap importa directo sin pasar por
    MCP — mismo reparto que _run_ingest_batch sobre bulk_loader.load_dump.

    Los chequeos de acá NO son redundantes con el inputSchema: el SDK no lo valida
    (ver el comentario de KNOWN_COLLECTIONS), así que `maxItems` es decorativo hasta
    que alguien lo verifica en el handler.
    """
    from src.ingestion.kb_loader import load_fichas

    kb_name = (arguments.get("kb_name") or "").strip()
    if not kb_name:
        raise ValueError(
            "Falta 'kb_name'. Sale de genexus_whoami → kb.active en el MCP de GeneXus. "
            "Sin él no hay identidad estable: dos KBs con un objeto homónimo se "
            "sobreescribirían entre sí."
        )

    objects = arguments.get("objects")
    if not isinstance(objects, list) or not objects:
        raise ValueError("'objects' vacío o ausente: no hay nada que indexar.")

    if len(objects) > KB_MAX_INLINE:
        # Se RECHAZA la llamada entera y no se truncan las primeras N: un lote truncado
        # reportaría éxito sobre un subconjunto mientras el llamador cree que entraron
        # todas — la misma clase de mentira silenciosa que el resto de este archivo
        # existe para prevenir.
        raise ValueError(
            f"{len(objects)} fichas exceden el máximo de {KB_MAX_INLINE} por llamada. "
            "NO se indexó ninguna (no se truncó el lote: un lote truncado reportaría "
            f"éxito parcial como éxito). Partí en tandas de hasta {KB_MAX_INLINE} y "
            "llamá una vez por tanda. Para la carga masiva inicial usá "
            "scripts/bootstrap_kb.py, que no pasa por MCP."
        )

    on_invalid = arguments.get("on_invalid", "stop")
    if on_invalid not in ("stop", "skip"):
        raise ValueError(
            f"on_invalid={on_invalid!r} no reconocido. Válidos: stop | skip."
        )

    reporte = load_fichas(
        objects,
        kb_name=kb_name,
        dry_run=arguments.get("dry_run", True),
        on_invalid=on_invalid,
    )
    return _render_ingest_report(reporte)


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
        k_raw = arguments.get("top_k", 5)
        snippet_chars_raw = arguments.get("snippet_chars", 600)
        try:
            k = min(max(1, int(k_raw)), 50)
        except (ValueError, TypeError):
            k = 5
        try:
            snippet_chars = min(max(100, int(snippet_chars_raw)), 4000)
        except (ValueError, TypeError):
            snippet_chars = 600
        min_score = arguments.get("min_score")
        filters = arguments.get("filters")
        # Antes de embeddear: rechazar acá evita además pagar la carga de e5-large
        # (~2.2 GB en frío) por un argumento que ya sabemos inválido.
        if rejection := _reject_unknown_collection(col):
            return [TextContent(type="text", text=rejection)]
        if rejection := _reject_missing_kb_name(col, filters):
            return [TextContent(type="text", text=rejection)]
        rerank = bool(arguments.get("rerank", False))
        try:
            results = hybrid_search(
                q, collection=col, top_k=k, min_score=min_score, filters=filters, rerank=rerank
            )
            if not results:
                return [TextContent(type="text", text=_empty_search_text(col, filters))]
            lines = [f"## Resultados ({len(results)} found)\n"]
            for r in results:
                content = r["content"] or ""
                snippet = content[:snippet_chars]
                truncated = "…" if len(content) > snippet_chars else ""
                score_label = f"score={r['score']:.3f}"
                if r.get("reranked") and "rrf_score" in r:
                    score_label += f" [reranked, rrf={r['rrf_score']:.3f}]"
                lines.append(
                    f"**{r['title']}** ({score_label})\n"
                    f"doc_id: `{r['doc_id']}`\n"
                    f"article_id: `{r.get('id', '')}`\n"
                    f"Fuente: {r.get('url') or r.get('source', '?')}\n"
                    f"Hash: {r.get('content_hash', '')}\n"
                    f"{_meta_line(r.get('meta'))}"
                    f"{snippet}{truncated}\n"
                )
            return [TextContent(type="text", text="\n".join(lines))]
        except ValueError as exc:
            # filter_params RECHAZA claves desconocidas y listas vacías con un mensaje
            # accionable. Si cayera en el `except Exception` de abajo se convertiría en
            # GENERIC_ERROR y el llamador no sabría que el problema es SU argumento:
            # buscaría la causa en el corpus o en el servidor.
            return [TextContent(type="text", text=str(exc))]
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
                return [TextContent(type="text",
                                    text=_article_not_found_text(article_id, collection))]
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
                TextContent(
                    type="text",
                    # JSON en una línea y no el repr del dict: parseable por el
                    # cliente y sin las comillas simples de Python.
                    text=f"{json.dumps(verdict, ensure_ascii=False, default=str)}\n"
                    f"{_verdict_guidance(verdict)}",
                )
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

    elif name == "ingest_kb_objects":
        try:
            return [TextContent(type="text", text=_run_ingest_kb_objects(arguments))]
        except ValueError as e:
            # Argumentos del llamador: el tope de fichas, kb_name ausente, objects
            # vacío. Van tal cual y NO como GENERIC_ERROR, o el llamador buscaría la
            # causa en el servidor en vez de en su propia llamada.
            return [TextContent(type="text", text=str(e))]
        except Exception:
            log.exception("ingest_kb_objects error")
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
