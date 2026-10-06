"""
Importa el catálogo de clases y métodos del lenguaje GeneXus a gx_syntax.

La fuente es Definitions/gxtypes.xml del propio install de GeneXus, que es
autoritativo y exacto a la versión instalada — a diferencia de la wiki, que
describe "GeneXus" sin decir cuál. Cada <CExternalClassDefinition> es un tipo
y cada <CMethodDefinition> un método con su tipo de retorno y sus parámetros.

Uso:
    python -m scripts.import_gxtypes --xml /ruta/a/gxtypes.xml --version 18.0.15
"""

from __future__ import annotations

import argparse
import logging
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

from src.syntax_db.models import Base, GxSyntax, get_engine, get_session

log = logging.getLogger(__name__)

SOURCE = "gxtypes"
DEFAULT_DOC_ID = "GeneXus/Definitions/gxtypes.xml"


@dataclass(frozen=True)
class MethodEntry:
    """Un método ya extraído del XML, listo para persistir."""

    class_name: str
    method_name: str
    returns: str | None
    params: tuple[tuple[str, str], ...]

    @property
    def qualified_name(self) -> str:
        return f"{self.class_name}.{self.method_name}"

    def signature(self) -> dict:
        return {
            "qualified": self.qualified_name,
            "returns": self.returns,
            "params": [{"name": n, "type": t} for n, t in self.params],
        }

    def description(self) -> str:
        rendered = ", ".join(f"{n}: {t}" for n, t in self.params)
        suffix = f" -> {self.returns}" if self.returns else ""
        return f"{self.qualified_name}({rendered}){suffix}"


def _text(node: ET.Element | None) -> str | None:
    """Texto normalizado de un nodo; None si está vacío o ausente."""
    if node is None or node.text is None:
        return None
    value = node.text.strip()
    return value or None


def _parse_params(method: ET.Element) -> tuple[tuple[str, str], ...]:
    params: list[tuple[str, str]] = []
    for param in method.findall("./Parameters/CParamDefinition"):
        name = _text(param.find("Name"))
        if name is None:
            continue
        params.append((name, _text(param.find("Type")) or "unknown"))
    return tuple(params)


def parse_gxtypes(xml_path: Path) -> list[MethodEntry]:
    """Extrae todos los métodos del XML. No toca la base."""
    root = ET.parse(xml_path).getroot()
    entries: list[MethodEntry] = []

    for klass in root.findall("./Classes/CExternalClassDefinition"):
        class_name = _text(klass.find("Name"))
        if class_name is None:
            continue
        for method in klass.findall("./Methods/CMethodDefinition"):
            method_name = _text(method.find("Name"))
            if method_name is None:
                continue
            entries.append(
                MethodEntry(
                    class_name=class_name,
                    method_name=method_name,
                    returns=_text(method.find("Type")),
                    params=_parse_params(method),
                )
            )

    return entries


def import_entries(
    entries: list[MethodEntry],
    version: str,
    doc_id: str,
    db_path: str | Path = "data/syntax.db",
) -> int:
    """
    Persiste las entradas, reemplazando las de esta misma fuente.

    Borrar por `source` mantiene la importación idempotente sin pisar filas
    provenientes de la wiki o del minado de la KB, que son otras capas del
    catálogo y tienen su propia procedencia.
    """
    Base.metadata.create_all(get_engine(db_path))
    session = get_session(db_path)
    try:
        session.query(GxSyntax).filter(GxSyntax.source == SOURCE).delete()
        session.add_all(
            GxSyntax(
                name=entry.method_name,
                object_type=entry.class_name,
                category="method",
                signature=entry.signature(),
                description=entry.description(),
                # `version` es la del install del que salió el XML, no desde cuándo
                # existe el método: guardada en version_since, verify_syntax la leía
                # como «disponible a partir de» y daba INVÁLIDO a todo método por gx16.
                # Va a la procedencia, y sin versión el veredicto para gx16 es PISTA.
                version_since=None,
                source=SOURCE,
                source_doc_id=f"GeneXus {version}/{doc_id}",
                kb_scope="global",
            )
            for entry in entries
        )
        session.commit()
        return len(entries)
    finally:
        session.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--xml", required=True, type=Path, help="Ruta a gxtypes.xml")
    parser.add_argument("--version", required=True,
                        help="Versión de GeneXus del install (va a la procedencia, no a version_since)")
    parser.add_argument("--doc-id", default=DEFAULT_DOC_ID, help="Procedencia a registrar")
    parser.add_argument("--db", default="data/syntax.db", help="Ruta a syntax.db")
    parser.add_argument("--dry-run", action="store_true", help="Parsea y reporta sin escribir")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    entries = parse_gxtypes(args.xml)
    classes = {entry.class_name for entry in entries}
    log.info(f"parseado: {len(classes)} clases, {len(entries)} métodos")

    if args.dry_run:
        log.info("dry-run: no se escribió nada")
        return

    written = import_entries(entries, args.version, args.doc_id, args.db)
    log.info(f"escrito en {args.db}: {written} filas (source={SOURCE})")


if __name__ == "__main__":
    main()
