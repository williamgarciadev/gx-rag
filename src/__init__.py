"""
gx-rag.

Carga el .env de la raiz del repo apenas se importa cualquier cosa de src.

Hace falta explicitamente: pydantic-settings lee el .env para poblar su propio
modelo (src/config.py), pero NO exporta nada a os.environ. Y GXRAG_DSN se lee
con os.environ.get() desde pg_client.py y syntax_db/models.py, que no pasan por
Config. Sin esto, el .env existe y aun asi el DSN llega vacio.

La ruta es absoluta a proposito: los scripts se corren desde cualquier cwd y un
load_dotenv() relativo fallaria en silencio.
"""

from pathlib import Path

from dotenv import load_dotenv

# override=False: lo que ya este exportado en el entorno gana sobre el .env.
load_dotenv(Path(__file__).resolve().parent.parent / ".env", override=False)
