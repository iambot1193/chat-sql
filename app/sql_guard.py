"""Rejects anything that is not a single read-only statement.

The database is already opened read-only at the OS level (`mode=ro`), so this is
the second of two layers: it exists to give the user a clear error instead of a
driver error, and to block multi-statement payloads before they are sent.

The Postgres entries below are kept so the guard still holds if the app is ever
pointed at a Postgres URL.
"""

import re

_COMMENT = re.compile(r"--[^\n]*|/\*.*?\*/", re.DOTALL)

# Whole-word match: a column named "created_at" must not trip "create".
_FORBIDDEN = re.compile(
    r"\b("
    r"insert|update|delete|drop|alter|create|truncate|merge|replace|"
    r"grant|revoke|copy|call|vacuum|analyze|reindex|cluster|"
    r"listen|notify|lock|set|reset|discard|prepare|execute|explain|"
    # SQLite: load_extension runs native code from inside a valid SELECT.
    r"attach|detach|pragma|load_extension|readfile|writefile|"
    r"pg_read_file|pg_read_binary_file|pg_ls_dir|pg_sleep|dblink|"
    r"lo_import|lo_export|pg_terminate_backend"
    r")\b",
    re.IGNORECASE,
)


class UnsafeQuery(ValueError):
    pass


def check(sql: str) -> str:
    """Return the cleaned statement, or raise UnsafeQuery."""
    stripped = _COMMENT.sub(" ", sql).strip().rstrip(";").strip()

    if not stripped:
        raise UnsafeQuery("consulta vazia")
    if ";" in stripped:
        raise UnsafeQuery("mais de um comando SQL não é permitido")
    if not re.match(r"^\s*(select|with)\b", stripped, re.IGNORECASE):
        raise UnsafeQuery("apenas SELECT / WITH é permitido")

    found = _FORBIDDEN.search(stripped)
    if found:
        raise UnsafeQuery(f"comando não permitido: {found.group(1).upper()}")

    return stripped
