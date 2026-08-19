#!/usr/bin/env python
"""Set de evaluacion CON RESOLUCION. Sucesor de eval_bt_docs.py, no su reemplazo.

POR QUE EXISTE. `eval_bt_docs.py` mide top-1 14/15 (93%) y top-3 15/15 (100%): esta
SATURADO. Un set saturado no puede medir NADA — el unico margen es un caso, y un caso
no distingue senal de ruido. Cuando se quiso decidir si los prefijos de e5 mejoran el
ranking, el instrumento no tenia resolucion para responder, y esa decision quedo
bloqueada (RAG_SYSTEM_MAP.md §16 la declara CRITICAL desde el 2026-08-11).

POR QUE EL SET VIEJO ACIERTA TANTO: sus consultas NOMBRAN el titulo del capitulo
("plan de cuentas contable" -> capitulo "Plan de Cuentas"). Eso mide que el motor
encuentre una cadena que esta literalmente en el titulo, y el titulo entra al tsvector
(sql/001 indexa title||content). Es casi un test de igualdad de strings con pasos
extra.

QUE HACE ESTE DISTINTO — tres ejes, ninguno saturado por construccion:

  1. PARAFRASIS: las consultas describen el tema SIN usar las palabras del titulo,
     como preguntaria un desarrollador que no conoce el indice. "cupo disponible para
     prestarle a un cliente" en vez de "lineas de credito". Ahi el titulo deja de ser
     un atajo lexico y la mitad densa tiene que trabajar.

  2. PROCEDENCIA (nuevo, habilitado por sql/004): el ground truth es de que FUENTE
     debe venir la respuesta, no que documento. Es objetivo, ya esta en los datos
     (meta.origin) y no necesita etiquetado manual. Mide algo que el set viejo no
     podia ni ver: que en gx_docs los tickets SAC son el 56% del corpus y sepultan la
     documentacion.

  3. DISCRIMINACION: pares de consultas cuyos temas se confunden de verdad
     ("Cambios" vs "Mesa de Dinero" son los dos compraventa de divisas). Un set sin
     ambiguedad real no mide discriminacion, mide recuperacion.

QUE NO MIDE, y hay que decirlo: si el fragmento devuelto RESPONDE la pregunta. Mide
RUTEO. "Devolvio algo" no es una metrica: sin umbral, el motor SIEMPRE devuelve algo
(ver los casos negativos al final).

USO:
    .venv/Scripts/python.exe scripts/eval_corpus.py            # texto legible
    .venv/Scripts/python.exe scripts/eval_corpus.py --json     # para comparar corridas
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import dotenv  # noqa: E402

dotenv.load_dotenv(".env", override=False)

from src.vectorstore.pg_retriever import hybrid_search  # noqa: E402

# ── Eje 1: parafrasis sobre bt_docs ──────────────────────────────────────────
#
# (consulta, fragmento del titulo del capitulo esperado)
#
# REGLA DE CONSTRUCCION, y es lo que le da resolucion al set: la consulta NO puede
# contener ninguna palabra significativa del titulo esperado. Si la contiene, el
# tsvector la encuentra por el titulo y la pregunta se vuelve trivial. Un assert al
# final del archivo verifica esa regla — si alguien agrega una consulta que la rompe,
# el set se degrada solo y nadie lo nota.
PARAFRASIS: list[tuple[str, str]] = [
    ("cupo maximo que se le puede prestar a un cliente", "Lineas de Credito"),
    ("proceso que corre de noche cuando termina el dia operativo", "Cadena de Cierre"),
    ("un funcionario aprueba algo que la norma no permite", "Autorizacion de Excepciones"),
    ("definir una operacion nueva sin escribir programas", "Definidor Transaccional"),
    ("hasta que monto puede firmar cada funcionario", "Perfiles de Autorizacion"),
    ("enganchar logica propia en un punto del proceso del core", "Rutinas de Tratamiento"),
    ("estructura jerarquica de los rubros contables", "Plan de Cuentas"),
    ("retener saldo de una cuenta sin debitarlo todavia", "Bloqueo y Cancelacion"),
    ("tasas y comisiones que se le cobran al cliente", "Precios"),
    ("el empleado que atiende al publico en ventanilla", "Sector Cajas"),
    ("certificado que no se puede rescatar antes del vencimiento", "DPF"),
    ("adelantar el cobro de un documento con fecha futura", "Descuentos"),
    ("aval o respaldo que cubre un prestamo", "Garantias Otorgadas"),
    ("colocar los excedentes en instrumentos financieros", "Inversiones"),
    ("alquiler de un cofre en la boveda del banco", "Cajas de Seguridad"),
    ("registro de la rubrica del cliente para validarla", "Firmas"),
    ("a quien se le permite mover el dinero de un tercero",
     "Facultades por Cuenta"),
    ("topes segun la atribucion del empleado", "Control de Limites"),
    ("que significa que una operacion figure como vigente o vencida",
     "Codigo de Estado"),
    ("informes para que la direccion tome decisiones", "Consultas Gerenciales"),
    ("asiento y mayorizacion de los movimientos", "Sector Contabilidad"),
    ("vincular un titular con los productos que tiene", "Cuentas y Personas"),
    ("saldo disponible y giro al descubierto", "Cuentas Corrientes"),
    ("amortizacion e intereses de un credito a plazo", "Prestamos"),
]

# ── Eje 2: procedencia sobre gx_docs ─────────────────────────────────────────
#
# El ground truth es la FUENTE, no el documento. Objetivo y sin etiquetado manual.
#
# `wwp` importa especialmente: WorkWithPlus (DVelop) es OTRO producto que el pattern
# nativo "Work With for Web" de GeneXus, y es solo el 4% del corpus contra el 56% de
# tickets SAC. Una consulta de WWP que devuelve wiki nativa no es "un resultado
# parecido": es documentacion del producto equivocado.
PROCEDENCIA: list[tuple[str, str]] = [
    ("standard actions disponibles en el grid del work with", "wwp"),
    ("como agregar una accion propia a la barra de un work with", "wwp"),
    ("configurar los filtros avanzados de la grilla", "wwp"),
    ("sintaxis del comando for each con order y where", "gx_wiki"),
    ("metodo insert de un business component", "gx_wiki"),
    ("como se declara un data provider", "gx_wiki"),
    ("error al aplicar el pattern sobre una transaccion", "sac"),
    ("se cae la aplicacion al compilar en .NET", "sac"),
]

# ── Eje 3: discriminacion ────────────────────────────────────────────────────
#
# Temas que se confunden DE VERDAD. Un set sin ambiguedad real mide recuperacion, no
# discriminacion — y discriminar es lo que falla cuando el corpus crece.
DISCRIMINACION: list[tuple[str, str, str]] = [
    # (consulta, capitulo esperado, capitulo con el que se confunde)
    ("compra y venta de moneda extranjera en el mercado", "Mesa de Dinero", "Cambios"),
    ("cotizacion aplicada a una operacion en dolares", "Cambios", "Mesa de Dinero"),
    ("permisos y roles de los usuarios del sistema", "Perfiles de Autorizacion",
     "Consideraciones de Seguridad"),
    ("politicas de seguridad de la instalacion", "Consideraciones de Seguridad",
     "Perfiles de Autorizacion"),
]

# ── Eje 4: control negativo ──────────────────────────────────────────────────
#
# Temas ajenos al corpus. NUNCA hay "sin resultados": el motor siempre devuelve algo.
# Lo que se mide es si el score los separa de los aciertos.
AJENOS = [
    "angular signals y change detection",
    "kubernetes ingress controller con nginx",
    "react server components streaming",
]

TOP_K = 5


def _acierto(hits: list[dict], esperado: str, campo: str = "title") -> int:
    """Posicion 1-based del primer hit que matchea, o 0 si no aparece."""
    for i, h in enumerate(hits, 1):
        valor = h.get(campo) if campo != "origin" else (h.get("meta") or {}).get("origin")
        if valor and esperado.lower() in str(valor).lower():
            return i
    return 0


def evaluar() -> dict:
    r: dict = {"axes": {}}

    # Eje 1
    casos = []
    for consulta, esperado in PARAFRASIS:
        hits = hybrid_search(consulta, collection="bt_docs", top_k=TOP_K)
        pos = _acierto(hits, esperado)
        casos.append({
            "query": consulta, "expected": esperado, "rank": pos,
            "top1": pos == 1, "top3": 1 <= pos <= 3,
            "got": (hits[0]["title"].split("—")[-1].strip() if hits else None),
            "score_max": round(max((h["score"] for h in hits), default=0), 6),
        })
    r["axes"]["paraphrase"] = _resumen(casos)

    # Eje 2
    casos = []
    for consulta, origen in PROCEDENCIA:
        hits = hybrid_search(consulta, collection="gx_docs", top_k=TOP_K)
        pos = _acierto(hits, origen, campo="origin")
        origenes = [(h.get("meta") or {}).get("origin") for h in hits]
        casos.append({
            "query": consulta, "expected_origin": origen, "rank": pos,
            "top1": pos == 1, "top3": 1 <= pos <= 3,
            "origins": origenes,
        })
    r["axes"]["provenance"] = _resumen(casos)

    # Eje 3
    casos = []
    for consulta, esperado, confusor in DISCRIMINACION:
        hits = hybrid_search(consulta, collection="bt_docs", top_k=TOP_K)
        pos_ok = _acierto(hits, esperado)
        pos_mal = _acierto(hits, confusor)
        # Discrimina si el esperado aparece ANTES que el confusor (o el confusor no está)
        discrimina = pos_ok > 0 and (pos_mal == 0 or pos_ok < pos_mal)
        casos.append({
            "query": consulta, "expected": esperado, "confusable_with": confusor,
            "rank_expected": pos_ok, "rank_confusable": pos_mal,
            "top1": pos_ok == 1, "top3": 1 <= pos_ok <= 3,
            "discriminates": discrimina,
        })
    r["axes"]["discrimination"] = _resumen(casos)
    r["axes"]["discrimination"]["discriminates"] = sum(
        1 for c in casos if c["discriminates"]
    )

    # Eje 4
    ajenos = []
    for consulta in AJENOS:
        hits = hybrid_search(consulta, collection="bt_docs", top_k=3)
        ajenos.append({
            "query": consulta,
            "score_max": round(max((h["score"] for h in hits), default=0), 6),
            "returned": len(hits),
        })
    r["axes"]["negative_control"] = {
        "cases": ajenos,
        "note": "Nunca hay 'sin resultados'. El score es lo unico que separa: "
                "1/(60+1)=0.016393 significa que el chunk entro por UNA sola mitad.",
    }

    # Veredicto de resolucion: un set saturado no sirve para decidir nada.
    p, d = r["axes"]["paraphrase"], r["axes"]["discrimination"]
    fallan = (p["total"] - p["top3"]) + (d["total"] - d["top3"])
    r["resolution"] = {
        "failing_cases": fallan,
        "usable_for_ranking_decisions": fallan >= 3,
        "why": ("Con menos de 3 casos fallando, una mejora de 1-2 casos no se "
                "distingue del ruido y el set no puede respaldar una decision de "
                "ranking. Ese es exactamente el estado de eval_bt_docs.py: 14/15 y "
                "15/15."),
    }
    return r


def _resumen(casos: list[dict]) -> dict:
    return {
        "total": len(casos),
        "top1": sum(1 for c in casos if c["top1"]),
        "top3": sum(1 for c in casos if c["top3"]),
        "cases": casos,
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--json", action="store_true", help="salida JSON para comparar")
    args = ap.parse_args(argv)

    r = evaluar()
    if args.json:
        print(json.dumps(r, ensure_ascii=False, indent=1))
        return 0

    for nombre, eje in r["axes"].items():
        if nombre == "negative_control":
            continue
        print(f"\n=== {nombre} ===")
        for c in eje["cases"]:
            marca = "OK " if c["top1"] else ("~3 " if c["top3"] else "-- ")
            esperado = c.get("expected") or c.get("expected_origin")
            extra = ""
            if nombre == "discrimination":
                extra = f"  (confusor en {c['rank_confusable'] or '-'})"
            print(f"  {marca} r={c['rank'] if 'rank' in c else c['rank_expected']}  "
                  f"{c['query'][:52]:52s} -> {esperado}{extra}")
        print(f"  --> top-1 {eje['top1']}/{eje['total']}   top-3 {eje['top3']}/{eje['total']}"
              + (f"   discrimina {eje['discriminates']}/{eje['total']}"
                 if "discriminates" in eje else ""))

    print("\n=== control negativo (temas ajenos) ===")
    for c in r["axes"]["negative_control"]["cases"]:
        print(f"  score={c['score_max']:.6f}  {c['query'][:56]}")

    res = r["resolution"]
    print(f"\n=== RESOLUCION: {res['failing_cases']} casos fallando -> "
          f"{'USABLE' if res['usable_for_ranking_decisions'] else 'SATURADO, no sirve'}"
          " para decidir ranking")
    return 0


# Guarda del set: una parafrasis que repite una palabra del titulo esperado deja de ser
# parafrasis y satura el set en silencio. Se verifica al importar, no en un test, para
# que falle tambien si alguien corre el script a mano tras editar la lista.
_STOP = {"de", "del", "la", "el", "los", "las", "y", "en", "un", "una", "a", "que",
         "para", "por", "con", "se", "al", "no", "su", "sus", "o", "es"}
for _q, _t in PARAFRASIS:
    _pal_t = {w.lower().strip(".,'") for w in _t.split()} - _STOP
    _pal_q = {w.lower().strip(".,'") for w in _q.split()} - _STOP
    _solapa = _pal_t & _pal_q
    assert not _solapa, (
        f"la parafrasis {_q!r} repite {_solapa} del titulo {_t!r}: el tsvector la "
        "encontraria por el titulo y el caso se vuelve trivial"
    )


if __name__ == "__main__":
    raise SystemExit(main())
