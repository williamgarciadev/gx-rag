#!/usr/bin/env python
"""Extraccion masiva de objetos de una KB GeneXus para el corpus del RAG.

POR QUE EXISTE ESTE SCRIPT Y NO LO HACE UN AGENTE. Medido sobre la KB real
(MatikaErp_3003, 15.018 objetos): un `read` cuesta 2,2 s de mediana contra un worker
single-threaded que RECHAZA en vez de encolar, y el batch read es secuencial con un
techo de ~25 objetos por llamada (60 s de timeout / 2,2 s). Extraer el nucleo de codigo
—Transaction + Procedure + WebPanel + WorkWithPlus, 3.157 objetos— son ~126 lotes y del
orden de 2 horas. Ningun agente sostiene 126 lotes en contexto.

QUE NO HACE, tambien a proposito: NO redacta las fichas. Una ficha es prosa
estructurada que describe el objeto, y eso es juicio, no mecanica — lo hace el agente
leyendo lo extraido. Este script produce el JSONL crudo y, con `ingest --mechanical`,
una ficha de PISO derivada mecanicamente para que el corpus sirva desde el dia uno.
La skill puede despues pedirle al agente que mejore las mecanicas bajo demanda.

REPARTO COMPLETO:
    inventario (list_objects paginado, ~7 llamadas)  -> el AGENTE, cabe en una sesion
    lectura de sources (126 lotes, ~2 h)             -> ESTE script, con checkpoint
    redaccion de fichas en prosa                     -> el AGENTE, por tandas
    ingesta                                          -> este script o el agente

EL CONSUMIDOR DE ESTE SCRIPT ES UN LLM, NO UNA PERSONA. Consecuencias de diseño:
  * stdout es SIEMPRE un unico objeto JSON, parseable sin heuristicas.
  * el progreso va a stderr, para no contaminar ese JSON.
  * toda salida trae `next` con el comando literal del paso siguiente, y `hint`
    cuando hay algo que decidir. Un LLM no puede "mirar la barra de progreso": o el
    estado esta en el JSON, o no existe.
  * un fallo devuelve exit != 0 Y un JSON con `error` + `hint` accionable. Nunca un
    traceback como unica salida.

USO:
    # 1. El agente arma el inventario y lo deja en un JSON:
    #    [{"name":"TrnFactura","type":"Transaction","path":"..."}, ...]
    python scripts/bootstrap_kb.py extract --inventory inv.json --out crudo.jsonl

    # 2. Reanudable: volver a correrlo salta lo ya extraido.
    python scripts/bootstrap_kb.py status --out crudo.jsonl

    # 3. Ficha de piso derivada mecanicamente, e ingesta idempotente:
    python scripts/bootstrap_kb.py ingest --in crudo.jsonl --kb-name MatikaErp_3003 \
        --mechanical --dry-run
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import subprocess
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

# ── Limites medidos, no elegidos al azar ─────────────────────────────────────
# 60 s de timeout de gateway / 2,2 s de mediana por read. Con Source grandes el
# margen se come rapido, asi que 20 y no 25.
BATCH_SIZE = 20
# El gateway reemplaza TODA la respuesta por un centinela al pasar 220.000 bytes: no
# trunca, borra. Se chequea el tamano y se parte el lote si hace falta.
RESPONSE_CAP_BYTES = 220_000
# WorkerBusy rechaza sin cola. Ante rechazo se espera y se reintenta.
BUSY_RETRIES = 4
BUSY_WAIT_S = 10


# ── Cliente MCP stdio minimo ─────────────────────────────────────────────────


class McpStdio:
    """Cliente MCP por stdio que lanza su propio gateway.

    Stdio y no HTTP a proposito: no exige que el usuario tenga un gateway levantado
    con el puerto abierto, y es el transporte que el gateway sirve por default.
    """

    def __init__(self, exe: str, config: str | None = None, verbose: bool = False):
        env = dict(os.environ)
        if config:
            env["GX_CONFIG_PATH"] = config
        self.verbose = verbose
        self.proc = subprocess.Popen(
            [exe],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            env=env,
            text=True,
            encoding="utf-8",
            bufsize=1,
        )
        self._id = 0
        self._rpc(
            "initialize",
            {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "bootstrap_kb", "version": "1"},
            },
        )
        self._notify("notifications/initialized")

    def _send(self, payload: dict) -> None:
        self.proc.stdin.write(json.dumps(payload, ensure_ascii=False) + "\n")
        self.proc.stdin.flush()

    def _notify(self, method: str) -> None:
        self._send({"jsonrpc": "2.0", "method": method})

    def _rpc(self, method: str, params: dict) -> dict:
        self._id += 1
        mid = self._id
        self._send({"jsonrpc": "2.0", "id": mid, "method": method, "params": params})
        while True:
            linea = self.proc.stdout.readline()
            if not linea:
                raise RuntimeError("el gateway cerro stdout")
            try:
                msg = json.loads(linea)
            except json.JSONDecodeError:
                continue  # log ruido en stdout: se ignora
            if msg.get("id") == mid:
                if "error" in msg:
                    raise RuntimeError(f"{method}: {msg['error']}")
                return msg.get("result", {})

    def call(self, tool: str, args: dict) -> dict:
        """Invoca una tool y devuelve su payload YA parseado.

        Lee structuredContent con FALLBACK a content[0].text: el primero se omite en
        los errores, asi que el fallback no es opcional. Y el texto es JSON-in-JSON.
        """
        res = self._rpc("tools/call", {"name": tool, "arguments": args})
        if isinstance(res.get("structuredContent"), dict):
            return res["structuredContent"]
        contenido = res.get("content") or []
        if contenido and contenido[0].get("text"):
            try:
                return json.loads(contenido[0]["text"])
            except json.JSONDecodeError:
                return {"_raw": contenido[0]["text"]}
        return {}

    def call_resiliente(self, tool: str, args: dict) -> dict:
        """call() con la politica de WorkerBusy y del cap de respuesta.

        El worker RECHAZA cuando esta ocupado (no encola), asi que un rechazo no es un
        error del argumento: es "volve a preguntar". Y una respuesta que paso el cap
        vuelve como centinela SIN datos, no truncada.
        """
        for intento in range(BUSY_RETRIES):
            payload = self.call(tool, args)
            texto = json.dumps(payload, ensure_ascii=False)
            ocupado = "WorkerBusy" in texto or payload.get("code") == "WorkerBusy"
            if ocupado and intento < BUSY_RETRIES - 1:
                if self.verbose:
                    print(f"    WorkerBusy, espero {BUSY_WAIT_S}s "
                          f"(intento {intento + 1}/{BUSY_RETRIES})", flush=True)
                self.call("genexus_lifecycle", {"action": "status", "wait": BUSY_WAIT_S})
                continue
            meta = payload.get("_meta") or {}
            truncado = (meta.get("truncated") or {}).get("reason")
            if truncado:
                raise ResponseTooLarge(
                    f"la respuesta paso el cap ({truncado}): el payload NO viene "
                    "truncado, viene VACIO. Hay que partir el lote."
                )
            return payload
        return payload

    def close(self) -> None:
        try:
            self.proc.stdin.close()
        except Exception:
            pass
        try:
            self.proc.wait(timeout=10)
        except Exception:
            self.proc.kill()


class ResponseTooLarge(RuntimeError):
    """La respuesta excedio el cap del gateway y volvio como centinela vacio."""


# ── Salida: un JSON en stdout, progreso en stderr ────────────────────────────


def _log(mensaje: str) -> None:
    """Progreso a stderr. NO va a stdout para no romper el JSON que lee el LLM."""
    print(mensaje, file=sys.stderr, flush=True)


def _emit(payload: dict) -> int:
    """Escribe el unico objeto JSON de stdout. Devuelve el exit code.

    Contrato con el llamador: `ok` dice si la corrida sirvio, `next` es el comando
    literal del paso siguiente (o None si terminó), y `hint` aparece solo cuando hay
    algo que decidir. Un LLM no tiene que inferir nada del texto.
    """
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload.get("ok", True) else 1


# ── Checkpoint: el estado vive en el JSONL de salida ─────────────────────────
#
# No hay archivo de checkpoint aparte, a proposito. Un archivo separado puede
# desincronizarse del resultado real (se escribio el checkpoint y fallo el write, o al
# revés). El JSONL de salida ES el checkpoint: se relee para saber que falta, asi que
# no puede mentir sobre lo que hay.


def ya_extraidos(salida: pathlib.Path) -> set[str]:
    if not salida.exists():
        return set()
    hechos: set[str] = set()
    with salida.open(encoding="utf-8") as fh:
        for linea in fh:
            linea = linea.strip()
            if not linea:
                continue
            try:
                hechos.add(json.loads(linea)["name"])
            except (json.JSONDecodeError, KeyError):
                # Una linea corrupta (proceso muerto a mitad de un write) no puede
                # abortar la reanudacion: se ignora y ese objeto se re-extrae.
                continue
    return hechos


def _append(salida: pathlib.Path, registro: dict) -> None:
    """Un objeto por linea, flusheado. El flush es lo que hace al JSONL un
    checkpoint: si el proceso muere, lo escrito hasta ahi ya esta en disco."""
    with salida.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(registro, ensure_ascii=False) + "\n")
        fh.flush()
        os.fsync(fh.fileno())


# ── extract ──────────────────────────────────────────────────────────────────


def seleccionar(inventario: list[dict], select: str | None) -> tuple[list[dict], dict]:
    """Filtra el inventario con un mini-DSL. Devuelve (seleccionados, informe).

    GRAMATICA, tomada del `Names` de GeneXus Next (export_kb_to_text), que es la mejor
    parte de su diseño:

        [all]              todo el inventario
        type:Transaction   por tipo de objeto
        TrnFactura         por nombre exacto
        Mod/Sub            por prefijo de parentPath (contiene, case-insensitive)

    Los terminos se separan con `;` y se UNEN (OR): `type:Transaction;type:WebPanel`.

    POR QUE UN DSL Y NO MAS FLAGS: el llamador es un LLM que ya tiene el inventario
    completo en un JSON. Con esto pide subconjuntos sin rearmarlo y sin que el script
    tenga que resolver nada contra la KB. Un inventario se arma UNA vez y se explota
    muchas.

    El informe dice CUANTO matcheo cada termino, y eso no es cosmetico: un termino que
    matchea 0 casi siempre es un typo de tipo ('Transacion'), y sin el desglose el
    llamador ve "seleccionados: 0" y concluye sobre la KB en vez de sobre su argumento.
    """
    terminos = [t.strip() for t in (select or "").split(";") if t.strip()]

    # Un selector SIN TERMINOS EFECTIVOS no filtra: equivale a [all].
    #
    # Se chequea DESPUES de parsear, no antes, porque el caso que importa no es `None`
    # sino `"  "` o `";;"` — un argumento que un llamador puede construir por accidente
    # (una interpolacion vacia). Chequeando solo `not select`, esos pasaban al filtro,
    # matcheaban nada y devolvian CERO objetos con un mensaje de "el selector no
    # matcheo": el llamador buscaria el problema en el inventario o en la KB, cuando lo
    # unico que paso es que no pidio nada.
    if not terminos or set(terminos) <= {"[all]", "*"}:
        return inventario, {"terms": {"[all]": len(inventario)}, "unmatched": []}
    por_termino: dict[str, int] = {}
    elegidos: dict[str, dict] = {}   # por nombre: dedupe si dos terminos coinciden

    for termino in terminos:
        if termino.lower().startswith("type:"):
            valor = termino[5:].strip().lower()
            matches = [o for o in inventario
                       if (o.get("type") or "").lower() == valor]
        elif "/" in termino:
            valor = termino.lower()
            matches = [o for o in inventario
                       if valor in (o.get("parentPath") or "").lower()]
        else:
            valor = termino.lower()
            matches = [o for o in inventario
                       if (o.get("name") or "").lower() == valor]
        por_termino[termino] = len(matches)
        for o in matches:
            elegidos[o["name"]] = o

    informe = {
        "terms": por_termino,
        "unmatched": [t for t, n in por_termino.items() if n == 0],
    }
    return list(elegidos.values()), informe


PARTES_POR_TIPO = {
    "Transaction": ["Structure", "Rules", "Events"],
    "Procedure": ["Source", "Rules", "Variables"],
    "WebPanel": ["Events", "Rules", "Variables"],
    "WorkWithPlus": ["PatternInstance"],
    "DataProvider": ["Source"],
    "SDT": ["Structure"],
}
PARTES_DEFAULT = ["Source"]


def extract(args) -> int:
    ruta_inv = pathlib.Path(args.inventory)
    if not ruta_inv.exists():
        return _emit({
            "ok": False, "command": "extract",
            "error": f"no existe el inventario: {ruta_inv}",
            "hint": "El inventario lo produce el AGENTE, no este script: "
                    "genexus_list_objects con typeFilter y limit<=500, paginando con "
                    "nextOffset, y volcando [{name,type,path,parentPath,lastUpdate}] "
                    "a un JSON.",
        })
    inventario = json.loads(ruta_inv.read_text(encoding="utf-8"))
    if isinstance(inventario, dict):
        # Tolera el envelope de genexus_list_objects tal cual lo devuelve la tool.
        inventario = inventario.get("objects") or inventario.get("results") or []

    total_inv = len(inventario)
    inventario, sel = seleccionar(inventario, args.select)
    if args.select and not inventario:
        return _emit({
            "ok": False, "command": "extract", "inventory": total_inv,
            "select": args.select, "selection": sel, "selected": 0,
            "error": "el selector no matcheó ningún objeto del inventario",
            "hint": "La comparación de `type:` y de nombre es EXACTA (case-insensitive). "
                    "Revisá `selection.terms`: un término con 0 casi siempre es un typo "
                    "del tipo. Los tipos que EXISTEN en el inventario están en "
                    "`available_types`.",
            "available_types": sorted({o.get("type") or "?" for o in
                                       json.loads(ruta_inv.read_text(encoding="utf-8"))
                                       if isinstance(o, dict)}),
        })

    salida = pathlib.Path(args.out)
    hechos = ya_extraidos(salida)
    pendientes = [o for o in inventario if o.get("name") not in hechos]

    # `skip` es el punto de control explícito, tomado de GeneXus Next ("useful for
    # resuming large exports that failed partway through"). Acá es redundante con el
    # checkpoint del JSONL —que reanuda solo— pero sirve para el caso que el checkpoint
    # NO cubre: saltear a mano un tramo que falla siempre, sin tener que editar el
    # inventario.
    if args.skip:
        pendientes = pendientes[args.skip:]

    if not pendientes:
        return _emit({
            "ok": True, "command": "extract", "inventory": total_inv,
            "already_done": len(hechos), "pending": 0, "processed": 0,
            "out": str(salida), "interrupted": False,
            "next": f"python scripts/bootstrap_kb.py ingest --in {salida} "
                    f"--kb-name <KB> --mechanical --dry-run",
        })

    if args.limit:
        pendientes = pendientes[: args.limit]
    _log(f"inventario={total_inv} ya_extraidos={len(hechos)} "
         f"a_procesar={len(pendientes)}")

    cliente = McpStdio(args.exe, args.config, verbose=True)
    t0 = time.perf_counter()
    ok = fallidos = vacios = 0
    interrumpido = False
    errores_muestra: list[dict] = []
    try:
        for i in range(0, len(pendientes), args.batch):
            lote = pendientes[i : i + args.batch]
            for obj in lote:
                try:
                    registro = _extraer_uno(cliente, obj)
                    _append(salida, registro)
                    if registro.get("parts"):
                        ok += 1
                    else:
                        # NINGUNA parte con texto. No es un exito: o el objeto no existe
                        # en la KB, o el tipo no expone las partes que se le pidieron
                        # (SDPanel y las partes proyectadas por patterns devuelven vacio
                        # POR DISEÑO). Contarlo como `extracted` seria decirle al
                        # llamador que tiene contenido que no tiene — y el sintoma
                        # recien aparecería en el ingest, lejos de la causa.
                        vacios += 1
                        if len(errores_muestra) < 10:
                            errores_muestra.append({
                                "name": obj["name"], "type": obj.get("type"),
                                "error": "sin partes con texto: el objeto no existe en "
                                         "la KB, o su tipo no expone las partes pedidas",
                            })
                except (ResponseTooLarge, Exception) as exc:
                    # Un objeto que falla NO aborta el barrido de 2 horas: se registra
                    # el fallo en el JSONL (que es el checkpoint) y se sigue. Perder 1
                    # de 3.157 es aceptable; perder las 2 horas por 1, no.
                    detalle = (str(exc) if isinstance(exc, ResponseTooLarge)
                               else f"{type(exc).__name__}: {exc}")
                    _append(salida, {"name": obj["name"], "type": obj.get("type"),
                                     "error": detalle})
                    fallidos += 1
                    if len(errores_muestra) < 10:
                        errores_muestra.append({"name": obj["name"],
                                                "error": detalle[:200]})
            hechos_ahora = ok + fallidos + vacios
            transcurrido = time.perf_counter() - t0
            resta = (len(pendientes) - hechos_ahora) * (transcurrido / hechos_ahora)
            _log(f"[{hechos_ahora}/{len(pendientes)}] ok={ok} vacios={vacios} fallidos={fallidos} "
                 f"{transcurrido / hechos_ahora:.1f}s/obj resta~{resta / 60:.0f}min")
    except KeyboardInterrupt:
        interrumpido = True
    finally:
        cliente.close()

    restantes = len(inventario) - len(hechos) - ok - fallidos - vacios
    payload = {
        "ok": True, "command": "extract",
        "inventory": total_inv, "selected": len(inventario),
        "selection": sel if args.select else None,
        "already_done": len(hechos),
        "processed": ok + fallidos + vacios, "extracted": ok,
        "empty": vacios, "failed": fallidos,
        "still_pending": max(0, restantes),
        "elapsed_s": round(time.perf_counter() - t0, 1),
        "out": str(salida), "interrupted": interrumpido,
    }
    if errores_muestra:
        payload["error_sample"] = errores_muestra
        payload["hint"] = (
            "`empty` NO es lo mismo que `failed`: son objetos que respondieron pero sin "
            "texto en ninguna parte pedida — o no existen en la KB, o su tipo no expone "
            "esas partes (SDPanel y las partes proyectadas por patterns devuelven vacío "
            "por diseño). NO se pueden ingestar, y `ingest` los saltea. "
            "Todo lo registrado en el JSONL cuenta como hecho, así que una re-corrida "
            "no los reintenta. Para reintentar, borrar esas líneas del JSONL."
        )
    if restantes > 0 or interrumpido:
        payload["next"] = (
            f"python scripts/bootstrap_kb.py extract --inventory {ruta_inv} "
            f"--out {salida}"
        )
        payload.setdefault("hint", "Quedan pendientes: el JSONL es el checkpoint, "
                                   "volver a correr el mismo comando continúa.")
    else:
        payload["next"] = (
            f"python scripts/bootstrap_kb.py ingest --in {salida} --kb-name <KB> "
            f"--mechanical --dry-run"
        )
    return _emit(payload)


def _extraer_uno(cliente: McpStdio, obj: dict) -> dict:
    """Lee las partes que corresponden al tipo del objeto. Datos CRUDOS."""
    tipo = obj.get("type") or ""
    partes = PARTES_POR_TIPO.get(tipo, PARTES_DEFAULT)
    registro: dict = {
        "name": obj["name"],
        "type": tipo,
        "path": obj.get("path"),
        "parentPath": obj.get("parentPath"),
        "lastUpdate": obj.get("lastUpdate"),
        "parts": {},
    }
    for parte in partes:
        payload = cliente.call_resiliente(
            "genexus_read", {"name": obj["name"], "part": parte}
        )
        texto = _texto_de_parte(payload)
        if texto:
            registro["parts"][parte] = texto
    return registro


def _texto_de_parte(payload: dict) -> str:
    """El contenido de una parte, del campo donde el gateway lo ponga.

    Se prueban varias claves porque el envelope no es uniforme entre partes y no vale
    la pena acoplar el script a una forma exacta: si ninguna trae texto, se devuelve
    vacio y el objeto queda registrado sin esa parte.
    """
    for clave in ("content", "source", "text", "value", "part"):
        valor = payload.get(clave)
        if isinstance(valor, str) and valor.strip():
            return valor
    resultado = payload.get("result")
    if isinstance(resultado, dict):
        return _texto_de_parte(resultado)
    if isinstance(resultado, str) and resultado.strip():
        return resultado
    return ""


# ── status ───────────────────────────────────────────────────────────────────


def status(args) -> int:
    salida = pathlib.Path(args.out)
    if not salida.exists():
        return _emit({
            "ok": True, "command": "status", "out": str(salida), "exists": False,
            "total": 0,
            "hint": "Nada extraído todavía. El inventario lo arma el agente con "
                    "genexus_list_objects paginado; después corré extract.",
            "next": "python scripts/bootstrap_kb.py extract --inventory <inv.json> "
                    f"--out {salida}",
        })
    total = con_error = sin_partes = usables = 0
    por_tipo: dict[str, int] = {}
    corruptas = 0
    with salida.open(encoding="utf-8") as fh:
        for linea in fh:
            if not linea.strip():
                continue
            try:
                r = json.loads(linea)
            except json.JSONDecodeError:
                corruptas += 1
                continue
            total += 1
            if r.get("error"):
                con_error += 1
            elif not r.get("parts"):
                sin_partes += 1
            else:
                usables += 1
            clave = r.get("type") or "?"
            por_tipo[clave] = por_tipo.get(clave, 0) + 1
    payload = {
        "ok": True, "command": "status", "out": str(salida), "exists": True,
        "total": total, "usable": usables, "with_error": con_error,
        "without_parts": sin_partes, "corrupt_lines": corruptas,
        "by_type": dict(sorted(por_tipo.items(), key=lambda kv: -kv[1])),
        "next": f"python scripts/bootstrap_kb.py ingest --in {salida} "
                f"--kb-name <KB> --mechanical --dry-run",
    }
    if sin_partes:
        payload["hint_without_parts"] = (
            "`without_parts` NO significa que el objeto esté vacío en la KB: significa "
            "que la parte pedida no devolvió texto. Para SDPanel y algunas partes "
            "proyectadas por patterns, el SDK devuelve vacío por diseño."
        )
    return _emit(payload)


# ── ingest ───────────────────────────────────────────────────────────────────


# Tope por parte en una ficha mecanica. MEDIDO: sin tope, 9 Transactions dieron 67
# chunks (7,4 por ficha, una llego a 22) porque la ficha pega el Source crudo entero.
# A 2 s de embedding por chunk, el nucleo de 3.157 objetos serian ~13 HORAS.
#
# Y el costo no es solo tiempo: pegar codigo crudo en un modelo de texto natural
# (e5 multilingue) aporta poco a la mitad densa, asi que se paga el embedding para
# obtener sobre todo senal lexica — que el tsvector ya da sin costo de vector.
#
# Con el tope, una ficha entra en ~2 chunks y el bootstrap baja a horas, no a media
# jornada. Lo que se pierde es la cola del Source, y eso se recupera cuando el agente
# redacta la ficha en prosa: ESE es el camino a una ficha buena, no un extracto mas
# largo.
MAX_CHARS_POR_PARTE = 1200


def _ficha_mecanica(registro: dict) -> dict | None:
    """Ficha de PISO derivada mecanicamente del crudo.

    NO es prosa: es un EXTRACTO del texto de las partes con un encabezado. Sirve para
    que el corpus sea buscable desde el dia uno; la version redactada la produce el
    agente despues. Se marca con `ficha_kind=mechanical` para poder distinguirlas y
    priorizar su mejora — un hit mecanico es peor que uno redactado, y quien lo lea
    tiene que poder saberlo.
    """
    partes = registro.get("parts") or {}
    if not partes:
        return None

    # Structure/Variables describen la forma; Source/Events/Rules, el comportamiento.
    forma = ("Structure", "Variables", "PatternInstance")
    secciones = []
    for nombre, texto in partes.items():
        if not texto.strip():
            continue
        kind = "structure" if nombre in forma else "logic"
        cuerpo = texto[:MAX_CHARS_POR_PARTE]
        if len(texto) > MAX_CHARS_POR_PARTE:
            # Se DICE que esta truncada. Una ficha que parece completa y no lo esta
            # hace que el agente responda con confianza sobre la mitad de un objeto.
            cuerpo += (
                f"\n[... TRUNCADO: la parte {nombre} tiene {len(texto)} chars y esta "
                f"ficha guarda los primeros {MAX_CHARS_POR_PARTE}. Es un extracto "
                "mecanico, no el objeto completo: para lo que falta, leer el objeto "
                "con genexus_read.]"
            )
        secciones.append({"kind": kind, "text": f"[{nombre}]\n{cuerpo}"})
    if not secciones:
        return None

    import hashlib

    crudo = json.dumps(partes, sort_keys=True, ensure_ascii=False)
    return {
        "obj_name": registro["name"],
        "obj_type": registro.get("type") or "Other",
        "module": (registro.get("parentPath") or "").split("/")[-1] or None,
        # kb_hash sobre el CRUDO de la KB, no sobre la ficha: es lo que hace que
        # reingestar sin cambios en la KB salga "skipped".
        "kb_hash": hashlib.sha256(crudo.encode("utf-8")).hexdigest(),
        "sections": secciones,
        "_ficha_kind": "mechanical",
    }


def ingest(args) -> int:
    from src.ingestion.kb_loader import load_fichas

    entrada = pathlib.Path(args.infile)
    fichas: list[dict] = []
    saltados = 0
    with entrada.open(encoding="utf-8") as fh:
        for linea in fh:
            if not linea.strip():
                continue
            try:
                registro = json.loads(linea)
            except json.JSONDecodeError:
                saltados += 1
                continue
            if registro.get("error"):
                saltados += 1
                continue
            ficha = _ficha_mecanica(registro) if args.mechanical else registro
            if ficha is None:
                saltados += 1
                continue
            ficha.pop("_ficha_kind", None)
            fichas.append(ficha)

    if not fichas:
        return _emit({
            "ok": False, "command": "ingest", "in": str(entrada),
            "fichas": 0, "skipped_lines": saltados,
            "error": "no hay fichas indexables en el archivo",
            "hint": "Si el JSONL viene de `extract`, hay que pasar --mechanical para "
                    "derivar la ficha del crudo. Sin ese flag se espera que cada línea "
                    "YA sea una ficha con obj_name/obj_type/sections.",
        })

    if args.limit:
        fichas = fichas[: args.limit]
    _log(f"fichas={len(fichas)} lineas_saltadas={saltados} dry_run={args.dry_run}")

    # Lotes chicos aunque no pase por MCP: cada lote es un punto de control real. La
    # idempotencia la da el content_hash, así que una corrida interrumpida se retoma
    # corriendo el mismo comando — lo ya escrito sale `skipped`.
    total = {"indexed": 0, "reindexed": 0, "skipped": 0, "invalid": 0, "error": 0,
             "would_index": 0, "would_reindex": 0}
    problemas: list[dict] = []
    t0 = time.perf_counter()
    for i in range(0, len(fichas), args.batch):
        lote = fichas[i : i + args.batch]
        reporte = load_fichas(
            lote, kb_name=args.kb_name, dry_run=args.dry_run, on_invalid="skip"
        )
        for clave in total:
            total[clave] += reporte.contar(clave)
        for r in reporte.results:
            if r.status in ("invalid", "error") and len(problemas) < 25:
                problemas.append({
                    "who": r.article_id or f"objects[{r.index}]",
                    "status": r.status, "reason": r.reason[:300],
                })
        hechas = min(i + args.batch, len(fichas))
        _log(f"[{hechas}/{len(fichas)}] " +
             " ".join(f"{k}={v}" for k, v in total.items() if v))

    escritos = total["indexed"] + total["reindexed"]
    payload = {
        "ok": True, "command": "ingest", "in": str(entrada),
        "kb_name": args.kb_name, "dry_run": args.dry_run,
        "fichas": len(fichas), "skipped_lines": saltados,
        "counts": {k: v for k, v in total.items() if v},
        "elapsed_s": round(time.perf_counter() - t0, 1),
    }
    if problemas:
        payload["problems"] = problemas
    if args.mechanical:
        payload["ficha_quality"] = "mechanical"
        payload["hint_quality"] = (
            "Estas fichas son un PISO derivado del crudo, no prosa redactada: el texto "
            "es el contenido de las partes con un encabezado. Sirven para que el corpus "
            "sea buscable ya, pero un hit mecánico es peor que uno redactado. Para "
            "mejorar un objeto: leelo con genexus_read, redactá la ficha en prosa por "
            "secciones, y reingestala con ingest_kb_objects — el mismo article_id la "
            "reemplaza."
        )
    if args.dry_run:
        payload["next"] = (
            f"python scripts/bootstrap_kb.py ingest --in {entrada} "
            f"--kb-name {args.kb_name}"
            + (" --mechanical" if args.mechanical else "")
        )
        payload["hint"] = (
            "Esto fue un DRY RUN: no se escribió nada. `would_index` y `would_reindex` "
            "son lo que entraría."
        )
    elif escritos:
        payload["next"] = None
        payload["verify"] = (
            "Verificación independiente del valor de retorno: get_article con un "
            "article_id concreto y collection=gx_kb_objects. NO uses search_docs para "
            "comprobar pertenencia: es un ranking."
        )
    return _emit(payload)


# ── CLI ──────────────────────────────────────────────────────────────────────


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    exe_default = (r"D:\Development\Matika\projects\Genexus18MCP_Local\publish"
                   r"\GxMcp18.Gateway.exe")

    pe = sub.add_parser("extract", help="lee objetos de la KB al JSONL (reanudable)")
    pe.add_argument("--inventory", required=True,
                    help="JSON con [{name,type,path,...}] (lo produce el agente con "
                         "genexus_list_objects paginado)")
    pe.add_argument("--out", required=True, help="JSONL de salida = checkpoint")
    pe.add_argument("--exe", default=exe_default, help="gateway del MCP GeneXus")
    pe.add_argument("--config", default=None, help="GX_CONFIG_PATH del gateway")
    pe.add_argument("--batch", type=int, default=BATCH_SIZE)
    pe.add_argument("--limit", type=int, default=0, help="procesar solo N pendientes")
    pe.add_argument("--select", default=None,
                    help="mini-DSL de seleccion sobre el inventario, terminos "
                         "separados por ';': [all] | type:Transaction | TrnFactura | "
                         "Mod/Sub (prefijo de parentPath). Se unen con OR.")
    pe.add_argument("--skip", type=int, default=0,
                    help="saltear los primeros N pendientes (punto de control manual; "
                         "el checkpoint del JSONL ya reanuda solo)")
    pe.set_defaults(func=extract)

    ps = sub.add_parser("status", help="avance de la extraccion")
    ps.add_argument("--out", required=True)
    ps.set_defaults(func=status)

    pi = sub.add_parser("ingest", help="indexa fichas desde el JSONL")
    pi.add_argument("--in", dest="infile", required=True)
    pi.add_argument("--kb-name", required=True)
    pi.add_argument("--mechanical", action="store_true",
                    help="derivar la ficha del crudo (piso, no prosa redactada)")
    pi.add_argument("--batch", type=int, default=25)
    pi.add_argument("--limit", type=int, default=0)
    pi.add_argument("--dry-run", action="store_true", default=False)
    pi.set_defaults(func=ingest)

    args = p.parse_args(argv)
    try:
        return args.func(args)
    except Exception as exc:
        # Un traceback pelado en stdout rompe el contrato: el llamador es un LLM que
        # parsea JSON. El traceback igual va a stderr para poder depurar.
        import traceback

        traceback.print_exc(file=sys.stderr)
        return _emit({
            "ok": False, "command": args.cmd,
            "error": f"{type(exc).__name__}: {exc}",
            "hint": _hint_para(exc),
        })


def _hint_para(exc: Exception) -> str:
    """Qué hacer ante cada falla conocida. Sin esto, el LLM reintenta a ciegas."""
    texto = str(exc)
    if isinstance(exc, FileNotFoundError) or "No such file" in texto:
        return ("Falta un archivo. Si es el gateway, pasá --exe con la ruta a "
                "GxMcp18.Gateway.exe. Si es el inventario o el JSONL, revisá la ruta.")
    if "el gateway cerro stdout" in texto:
        return ("El gateway murió. Causas medidas: no encuentra su config (pasá "
                "--config con el GX_CONFIG_PATH correcto), o la KB no está accesible. "
                "El stderr del gateway está suprimido: para verlo, corré el .exe a "
                "mano una vez.")
    if "WorkerBusy" in texto:
        return ("El worker del SDK rechaza en vez de encolar. Esperá y volvé a correr "
                "el mismo comando: el JSONL es el checkpoint y no se repite trabajo.")
    if isinstance(exc, ResponseTooLarge) or "response_exceeded_cap" in texto:
        return ("Un objeto excedió el cap de 220 KB del gateway y su payload vuelve "
                "VACÍO, no truncado. Bajá --batch, o salteá ese objeto.")
    if "does not exist" in texto and "meta" in texto:
        return ("La base no tiene la columna `meta`: falta aplicar sql/003-chunk-meta"
                ".sql. Ver docs/deploy-vps.md, sección de migración obligatoria.")
    return "Sin remedio conocido para esta falla; ver el traceback en stderr."


if __name__ == "__main__":
    raise SystemExit(main())
