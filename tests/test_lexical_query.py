"""La mitad léxica no busca palabras vacías ("de", "la", "para").

EL BUG: `_to_tsquery` armaba un OR con TODOS los tokens, y `ts_rank_cd` no pondera
por rareza (no tiene IDF). En "Cambio de Residencia" la palabra "de" pesaba igual que
"residencia", y ganaban los chunks llenos de términos frecuentes. Medido el
2026-10-08 con scripts/eval_etp_titles.py sobre 451 ETP buscados por su tema:

    OR con todos los tokens    acierto@1 0,501  @5 0,796  @10 0,825  MRR 0,626
    OR sin stopwords           acierto@1 0,663  @5 0,900  @10 0,920  MRR 0,763

Por código no cambia (acierto@10 1,000 en los dos). El set gold de 12 casos queda
con R@5 y R@10 iguales (0,750 y 0,833).
"""

import pytest

from src.vectorstore.pg_retriever import _to_tsquery

pytestmark = pytest.mark.unit


def test_stopwords_are_left_out_of_the_query():
    assert _to_tsquery("Cambio de Residencia") == "cambio | residencia"


def test_accented_stopwords_are_recognised():
    # La lista de PostgreSQL trae "más" y "él" con tilde; tokenizamos sin quitar
    # tildes, así que se comparan tal cual. Pasarlas por unaccent primero las
    # dejaría sin reconocer ("mas" no está en la lista).
    assert _to_tsquery("más tasas para él") == "tasas"


def test_a_query_of_only_stopwords_keeps_its_terms():
    # Sin términos, search_params devuelve None y la búsqueda entera se cae. Una
    # consulta hecha solo de palabras vacías es rara, pero no puede quedar vacía.
    assert _to_tsquery("de la") == "de | la"


def test_program_codes_and_domain_terms_survive():
    assert _to_tsquery("Novación de Préstamos Ppp93051") == "novación | préstamos | ppp93051"
