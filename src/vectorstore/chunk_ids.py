"""
Formato del id de chunk: `{article_id}_c{i}`. Un solo dueño.

Lo construye el indexer y lo reconoce server.py (para avisar que se pasó un
id de chunk donde iba un article_id). Antes cada lado tenía su copia del
formato, y cambiar uno rompía al otro en silencio.

Sin dependencias a propósito: server.py lo importa a nivel de módulo, y cargar
ahí algo que arrastre torch reabriría el deadlock de imports nativos dentro
del event loop (ver PENDIENTES.md, "Deadlock del MCP").
"""

import re

_CHUNK_SUFFIX = re.compile(r"_c\d+$")


def chunk_id(article_id: str, index: int) -> str:
    return f"{article_id}_c{index}"


def is_chunk_id(value: str) -> bool:
    return bool(_CHUNK_SUFFIX.search(value))
