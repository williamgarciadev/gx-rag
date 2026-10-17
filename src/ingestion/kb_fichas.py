"""Ficha de un objeto de Knowledge Base GeneXus -> chunks indexables.

Funcion PURA: sin base de datos, sin embeddings, sin red. Se puede testear sin
Postgres levantado, lo que importa porque los tests de integracion se saltean solos
cuando no hay GXRAG_DSN.

QUE ES UNA FICHA. No es el source crudo del objeto: es una descripcion en PROSA
ESTRUCTURADA, por secciones. El motivo es el modelo de embeddings: e5 es un modelo de
texto natural multilingue, asi que un XML de PatternInstance o un Source de GeneXus
crudo aportan poco a la mitad densa y todo el peso queda en la lexica. Quien arma la
ficha es el agente (que entiende el objeto), no este modulo.

    {
      "obj_name": "PrcMayorizar",
      "obj_type": "Procedure",
      "module": "CentralizacionContable",     # opcional
      "kb_hash": "<hash del source del objeto en la KB>",
      "related": ["TrnAsiento", "PrcCerrarEjercicio"],
      "sections": [
        {"kind": "structure", "text": "Recibe &Ejercicio (in, N4) y ..."},
        {"kind": "logic",     "text": "Recorre FSD011 por ejercicio y ..."},
      ],
    }
"""

from __future__ import annotations

import hashlib
import json

from langchain_text_splitters import RecursiveCharacterTextSplitter

# Bumpear esto REINDEXA TODO, a proposito: es la forma de decir "el generador de
# fichas cambio, lo indexado quedo viejo". Entra al content_hash.
FICHA_SCHEMA_VERSION = 1

KB_CHUNK_SIZE = 1500
KB_CHUNK_OVERLAP = 150

# Constantes PROPIAS: no se toca CHUNK_SIZE=800 de pg_indexer. Dos razones concretas:
#  1) cambiar el 800 alteraria en silencio el chunking de toda ingesta futura de
#     gx_docs, y dejaria los chunks ya indexados con otra granularidad que los nuevos
#     DENTRO DE LA MISMA coleccion.
#  2) 1500 es lo correcto para prosa estructurada y corta: sigue holgadamente dentro
#     de la ventana de e5 (~512 tokens ~ 2.000 chars) y menos chunks por objeto es
#     menos objeto ocupando el pool de candidatos del RRF.
_kb_splitter = RecursiveCharacterTextSplitter(
    chunk_size=KB_CHUNK_SIZE,
    chunk_overlap=KB_CHUNK_OVERLAP,
    separators=["\n## ", "\n### ", "\n\n", "\n", " "],
    keep_separator=True,
)

# Secciones reconocidas. Es un enum CERRADO porque alimenta el filtro chunk_kind: un
# valor nuevo no declarado seria invisible a toda busqueda filtrada.
KB_CHUNK_KINDS = ("structure", "logic", "rule", "pattern", "summary")

# Tipos SUGERIDOS para guiar al agente en el schema de la tool.
#
# OJO: NO se valida contra esta lista. La KB medida tiene 42 tipos distintos de objeto
# y la lista crece con cada release de GeneXus; rechazar un tipo legitimo bloquearia la
# ingesta de objetos reales, que es un fallo peor que aceptar un tipo raro. La
# validacion solo exige que sea un string no vacio.
KB_OBJ_TYPES_SUGERIDOS = (
    "Transaction",
    "Procedure",
    "WebPanel",
    "WebComponent",
    "WorkWithPlus",
    "SDT",
    "Domain",
    "DataProvider",
    "Table",
    "SDPanel",
    "API",
    "Query",
    "DataSelector",
    "ExternalObject",
    "MasterPage",
)


def ficha_article_id(ficha: dict, *, kb_name: str) -> str:
    """Identidad estable del objeto dentro de la coleccion.

    Lleva kb_name Y obj_type: sin la KB, dos Knowledge Bases con un objeto homonimo se
    sobreescribirian entre si; sin el tipo, un Procedure y una Transaction con el mismo
    nombre (que en GeneXus es legal) colisionarian.
    """
    return f"kb:{kb_name}:{ficha['obj_type']}:{ficha['obj_name']}"


def ficha_content_hash(ficha: dict, *, kb_name: str) -> str:
    """Clave de idempotencia = de que depende que la ficha DEBA cambiar.

    Se hashea la PROCEDENCIA (kb_hash del objeto en la KB + version del generador),
    NO el texto de la ficha. Es la decision mas importante de este modulo.

    Si se hasheara la prosa y el generador no fuera deterministico —y no lo es, porque
    la redacta un LLM— cada corrida produciria un hash distinto, los objetos saldrian
    todos "reindexed" SIEMPRE, y cada resincronizacion costaria el re-embedding
    completo. Peor: "skipped" dejaria de significar nada.

    Asi: objeto sin cambios + generador sin cambios -> skipped. Generador mejorado ->
    se bumpea FICHA_SCHEMA_VERSION y se reindexa todo, deliberadamente.

    Degradacion honesta: si el llamador no puede calcular kb_hash y manda "", la
    idempotencia queda colgada de que el generador sea deterministico. La tool avisa
    cuando eso pasa en vez de dejarlo silencioso.
    """
    payload = json.dumps(
        {
            "v": FICHA_SCHEMA_VERSION,
            "kb": kb_name,
            "type": ficha["obj_type"],
            "name": ficha["obj_name"],
            "src": ficha.get("kb_hash", ""),
        },
        sort_keys=True,
        ensure_ascii=False,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def validate_ficha(ficha: object) -> list[str]:
    """Lista de problemas; vacia si la ficha sirve. No lanza.

    Devuelve TODOS los problemas y no solo el primero: para un lote, que el llamador
    tenga que descubrirlos de a uno por reintento es fricción pura.
    """
    problemas: list[str] = []
    if not isinstance(ficha, dict):
        return ["la ficha no es un objeto"]

    for campo in ("obj_name", "obj_type"):
        valor = ficha.get(campo)
        if not isinstance(valor, str) or not valor.strip():
            problemas.append(f"falta '{campo}' (string no vacio)")

    secciones = ficha.get("sections")
    if not isinstance(secciones, list) or not secciones:
        # Una ficha sin secciones NO se indexa como vacia: se rechaza. pg_indexer
        # devuelve [] con un warning cuando no hay chunks, o sea que entraria
        # "exitosamente" sin indexar nada, y despues nadie distingue eso de un objeto
        # que la KB no tiene.
        problemas.append(
            "'sections' vacio o ausente: una ficha sin secciones no tiene contenido "
            "que indexar. Si el objeto existe pero no pudiste leerlo, omítelo en vez "
            "de mandar una ficha vacia"
        )
        return problemas

    for i, seccion in enumerate(secciones):
        if not isinstance(seccion, dict):
            problemas.append(f"sections[{i}] no es un objeto")
            continue
        kind = seccion.get("kind")
        if kind not in KB_CHUNK_KINDS:
            problemas.append(
                f"sections[{i}].kind = {kind!r} no reconocido. "
                f"Validos: {' | '.join(KB_CHUNK_KINDS)}"
            )
        texto = seccion.get("text")
        if not isinstance(texto, str) or not texto.strip():
            problemas.append(f"sections[{i}].text vacio o ausente")

    return problemas


def ficha_meta(ficha: dict, *, kb_name: str) -> dict:
    """Metadatos que van a chunks.meta. El chunk_kind lo agrega pg_indexer por chunk.

    Las cuatro claves que exige el CHECK de sql/003 son obj_name, obj_type,
    chunk_kind y kb_name; las tres primeras salen de aca y el chunk_kind lo pone
    _write_chunks, que es el unico que sabe a que seccion pertenece cada chunk.
    """
    meta = {
        "obj_name": ficha["obj_name"],
        "obj_type": ficha["obj_type"],
        "kb_name": kb_name,
    }
    if ficha.get("module"):
        meta["module"] = ficha["module"]
    if ficha.get("kb_hash"):
        meta["kb_hash"] = ficha["kb_hash"]
    related = ficha.get("related")
    if related:
        meta["related"] = list(related)
    return meta


def ficha_to_chunks(ficha: dict, *, kb_name: str = "") -> list[tuple[str, str]]:
    """[(chunk_kind, texto)] en orden. La SECCION es la unidad; se parte si excede.

    POR QUE NO "1 objeto = 1 chunk": e5 trunca a ~512 tokens (~2.000 chars). Una ficha
    de Transaction con estructura + reglas + eventos pasa eso facil, y el vector denso
    perderia mas de la mitad del contenido EN SILENCIO, mientras el tsvector (que no
    tiene limite) si ve todo. Las dos mitades del RRF quedarian en desacuerdo sobre
    que dice el documento, sin ningun error. Y ademas mataria chunk_kind como
    dimension de filtro.

    POR QUE TAMPOCO EL SPLITTER SOLO: una seccion de 400 chars no se debe partir, y el
    splitter de 800 la dejaria entera igual — pero perderia el limite semantico entre
    secciones, que es exactamente lo que queremos preservar.

    kb_name no se usa acá; se acepta para que la firma sea uniforme con el resto.
    """
    salida: list[tuple[str, str]] = []
    for seccion in ficha["sections"]:
        texto = seccion["text"]
        kind = seccion["kind"]
        partes = (
            [texto] if len(texto) <= KB_CHUNK_SIZE else _kb_splitter.split_text(texto)
        )
        for parte in partes:
            if parte.strip():
                # El kind viaja en TODOS los sub-chunks: si una seccion larga se
                # partio, el filtro por chunk_kind tiene que seguir encontrandolos a
                # todos, no solo al primero.
                salida.append((kind, parte))
    return salida


def ficha_title(ficha: dict) -> str:
    """Titulo del chunk. Va al tsvector (title||content), asi que es senal lexica.

    "PrcMayorizar (Procedure)" hace que buscar el nombre exacto del objeto matchee por
    la mitad lexica aunque la prosa no lo repita.
    """
    return f"{ficha['obj_name']} ({ficha['obj_type']})"
