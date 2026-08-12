#!/usr/bin/env python3
"""Set de evaluación mínimo sobre `bt_docs` — el manual funcional Bantotal.

RAG_SYSTEM_MAP.md §16 declara la evaluación como CRITICAL: sin un set de
relevancia etiquetado, ninguna decisión de ranking (RRF vs alternativas,
prefijos e5, config 'spanish', reranker) se puede validar. Esto es el primer
ladrillo: chico, honesto y reproducible, no una suite completa.

EL GROUND TRUTH sale de los títulos de los 33 capítulos del manual: si la consulta
nombra el tema de un capítulo, ese capítulo tiene que estar arriba. Es débil como
etiquetado —un capítulo puede hablar de temas de otro— pero es verificable y no
requiere un experto marcando chunks a mano.

Qué NO mide: si el fragmento devuelto responde la pregunta. Mide RUTEO: si el
retrieval lleva al documento correcto. "Devolvió algo" no es una métrica, porque
sin umbral SIEMPRE devuelve algo (ver el control negativo).

    .venv/Scripts/python.exe scripts/eval_bt_docs.py
"""

from __future__ import annotations

import sys
import unicodedata
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import dotenv

dotenv.load_dotenv(".env", override=False)

from src.vectorstore import embeddings as emb  # noqa: E402
from src.vectorstore.pg_retriever import RRF_K, hybrid_search  # noqa: E402

COLLECTION = "bt_docs"

# (consulta, fragmento que debe aparecer en el título del capítulo esperado)
CASOS: list[tuple[str, str]] = [
    ("cadena de cierre", "cadena de cierre"),
    ("como se autorizan las excepciones", "autorizacion de excepciones"),
    ("perfiles de autorizacion de usuarios", "perfiles de autorizacion"),
    ("plan de cuentas contable", "plan de cuentas"),
    ("cajas de ahorro y cuentas corrientes", "cuentas corrientes"),
    ("depositos a plazo fijo", "dpf"),
    ("procesos batch nocturnos", "procesos batch"),
    ("rutinas de tratamiento especializado", "rutinas de tratamiento"),
    ("definidor transaccional", "definidor transaccional"),
    ("cierre de ejercicio contable", "cierre de ejercicio"),
    ("cajas de seguridad para clientes", "cajas de seguridad"),
    ("lineas de credito", "lineas de credito"),
    ("garantias otorgadas y recibidas", "garantias otorgadas"),
    ("mesa de dinero", "mesa de dinero"),
    ("bloqueo y cancelacion parcial de fondos", "bloqueo y cancelacion"),
]

# Pares (sin tilde, con tilde). El sql/002 normalizó el vector LÉXICO; el denso no
# se toca con unaccent, así que este par mide si el pipeline COMPLETO es estable.
PARES_TILDE = [
    ("autorizacion de excepciones", "autorización de excepciones"),
    ("garantias", "garantías"),
    ("prestamos", "préstamos"),
]

# Temas que NO están en el corpus. No existe "sin resultados": el denso siempre
# devuelve sus vecinos más cercanos, por lejanos que sean.
CONTROL_NEGATIVO = ["Angular signals reactivity", "kubernetes ingress controller"]

# Piso teórico del RRF: un chunk que aparece en UN solo ranking, en la posición 1.
# Por debajo o igual a esto, el hit no tuvo apoyo de las dos mitades.
SOLO_UN_RETRIEVER = 1.0 / (RRF_K + 1)


def sin_tildes(texto: str) -> str:
    descompuesto = unicodedata.normalize("NFD", texto or "")
    return "".join(c for c in descompuesto if unicodedata.category(c) != "Mn").lower()


def capitulo(titulo: str) -> str:
    """"Manual Funcional Bantotal — Procesos Batch" -> "Procesos Batch"."""
    return (titulo or "").split("—")[-1].strip()


def main() -> int:
    print(f"Set de evaluación sobre '{COLLECTION}' — {len(CASOS)} consultas\n")
    print(f"{'consulta':<42} {'top1':<6} {'top3':<6} capítulo devuelto (top-1)")
    print("-" * 108)

    aciertos_1 = aciertos_3 = 0
    for consulta, esperado in CASOS:
        hits = hybrid_search(consulta, collection=COLLECTION, top_k=3)
        titulos = [sin_tildes(h["title"]) for h in hits]
        en_1 = bool(titulos) and esperado in titulos[0]
        en_3 = any(esperado in t for t in titulos)
        aciertos_1 += en_1
        aciertos_3 += en_3
        devuelto = capitulo(hits[0]["title"])[:42] if hits else "(nada)"
        print(
            f"{consulta:<42} {'OK' if en_1 else '--':<6} {'OK' if en_3 else '--':<6} {devuelto}"
        )

    total = len(CASOS)
    print("-" * 108)
    print(
        f"  ruteo correcto — top-1: {aciertos_1}/{total} "
        f"({100 * aciertos_1 // total}%)   top-3: {aciertos_3}/{total} "
        f"({100 * aciertos_3 // total}%)"
    )

    print("\n=== Estabilidad ante tildes (el sql/002 arregló SÓLO la mitad léxica) ===")
    for sin_acento, con_acento in PARES_TILDE:
        ids_a = [h["doc_id"] for h in hybrid_search(sin_acento, collection=COLLECTION, top_k=3)]
        ids_b = [h["doc_id"] for h in hybrid_search(con_acento, collection=COLLECTION, top_k=3)]
        coseno = sum(x * y for x, y in zip(emb.embed(sin_acento), emb.embed(con_acento)))
        estado = "IDÉNTICO" if ids_a == ids_b else "DIFIERE"
        print(f"  {sin_acento:<30} vs {con_acento:<30} {estado:<9} (coseno {coseno:.4f})")
    print(
        "  El léxico normaliza con unaccent, pero el embedding de 'préstamos' NO es el\n"
        "  de 'prestamos' (coseno < 1), y el RRF fusiona las dos mitades: el ranking\n"
        "  final sigue dependiendo de las tildes."
    )

    print("\n=== Control negativo: temas ajenos al corpus ===")
    for consulta in CONTROL_NEGATIVO:
        hits = hybrid_search(consulta, collection=COLLECTION, top_k=1)
        for h in hits:
            solo_denso = h["score"] <= SOLO_UN_RETRIEVER + 1e-6
            marca = "  <- sin apoyo léxico" if solo_denso else ""
            print(f"  {consulta:<32} score={h['score']:.4f}  {capitulo(h['title'])[:30]}{marca}")
    print(
        f"  Nunca hay 'sin resultados'. Pero el piso {SOLO_UN_RETRIEVER:.4f} = 1/({RRF_K}+1) es\n"
        "  interpretable: significa que el chunk entró por UN solo retriever. Los aciertos\n"
        "  de arriba puntúan ~0.029 porque aparecen en el denso Y en el léxico."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
