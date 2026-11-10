"""Small SQL helpers shared by the API and the importer."""
from __future__ import annotations

from collections.abc import Iterable

from sqlalchemy import any_, bindparam
from sqlalchemy.types import ARRAY


def in_ids(column, values: Iterable):
    """`column IN (...)` as ONE array parameter (`column = ANY($1)`).

    SQLAlchemy's in_() sends every value as its own query parameter, and asyncpg refuses a statement
    with more than 32,767 of them: "Select all" on a large folder, a bulk face review or a first scan
    would fail with a 500 once the list was long enough. An empty list matches nothing, as with in_().
    """
    return column == any_(bindparam("in_ids", list(values), type_=ARRAY(column.type), unique=True))
