"""Text-to-SQL chatbot: question in, SQL + result grid out."""

import json
import os
import sqlite3
import time
from contextlib import closing
from pathlib import Path

import ollama
from fastapi import FastAPI
from fastapi.responses import FileResponse
from pydantic import BaseModel

MODEL = os.environ.get("OLLAMA_MODEL", "qwen2.5-coder:7b")
MAX_ROWS = 500
MAX_TRIES = 3       # first attempt + 2 corrections
HISTORY = 6         # turns kept; a local model reprocesses the whole context each call
QUERY_TIMEOUT = 5   # seconds a single SELECT may run

# A hung model would otherwise hold the request open forever.
client = ollama.Client(
    host=os.environ.get("OLLAMA_HOST", "http://localhost:11434"), timeout=120
)

from app.sql_guard import UnsafeQuery, check  # noqa: E402

DB_PATH = Path(os.environ.get("DB_PATH", Path(__file__).parent.parent / "db" / "analytics.db"))
# mode=ro: the OS file itself refuses writes, independent of sql_guard.
RO_URI = f"file:{DB_PATH.as_posix()}?mode=ro"

app = FastAPI(title="SQL Chatbot")

STATIC = Path(__file__).parent / "static"

RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "sql": {
            "type": "string",
            "description": "Consulta SELECT em SQLite, ou string vazia se a pergunta não exigir consulta.",
        },
        "note": {
            "type": "string",
            "description": "Uma ou duas frases em português explicando o que a consulta retorna.",
        },
    },
    "required": ["sql", "note"],
    "additionalProperties": False,
}

SYSTEM = """Você é um analista de dados que traduz perguntas em português para SQL SQLite.

Regras:
- Gere exatamente uma instrução SELECT (CTEs com WITH são permitidos). Nunca INSERT, UPDATE, DELETE ou DDL.
- Use apenas as tabelas e colunas do esquema abaixo. Nunca invente nomes.
- Sempre inclua LIMIT quando a pergunta não pedir um agregado.
- Dê nomes legíveis às colunas com AS.
- Se a pergunta não puder ser respondida com esse esquema, devolva sql vazio e explique em note.

O banco tem dados reais de veículos vendidos nos EUA (marca, modelo, ano, tipo).

Esquema:
{schema}

Exemplos:

P: quantos modelos a Toyota tem?
sql: SELECT COUNT(*) AS total FROM vehicles WHERE make = 'Toyota'
note: Total de modelos cadastrados da Toyota.

P: quais as 5 marcas com mais modelos?
sql: SELECT make AS marca, COUNT(*) AS modelos FROM vehicles GROUP BY make ORDER BY modelos DESC LIMIT 5
note: As cinco marcas com maior número de modelos no catálogo.

P: me mostre picapes da Ford depois de 2020
sql: SELECT make AS marca, model AS modelo, model_year AS ano FROM vehicles WHERE make = 'Ford' AND vehicle_type LIKE '%Truck%' AND model_year > 2020 ORDER BY model_year DESC LIMIT 50
note: Picapes da Ford com ano de modelo posterior a 2020.

P: quantos tipos de veículo existem?
sql: SELECT vehicle_type AS tipo, COUNT(*) AS quantidade FROM vehicles GROUP BY tipo ORDER BY quantidade DESC
note: Distribuição de modelos por tipo de veículo.

P: qual o faturamento do mês passado?
sql:
note: O banco tem apenas o catálogo de veículos (marca, modelo, ano, tipo). Não há dados de vendas ou valores.
"""


class Ask(BaseModel):
    messages: list[dict]  # [{"role": "user"|"assistant", "content": str}]


def _connect() -> sqlite3.Connection:
    return sqlite3.connect(RO_URI, uri=True)


def _schema() -> str:
    """Introspect the live schema. ponytail: rebuilt per request — it's one
    fast pragma per table; cache it if the DB ever gets hundreds of tables."""
    with closing(_connect()) as conn:
        tables = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        )]
        lines = []
        for table in tables:
            lines.append(f"\n{table}:")
            for _, col, dtype, *_rest in conn.execute(f"PRAGMA table_info({table})"):
                lines.append(f"  - {col} ({dtype})")
    return "\n".join(lines)


def _run(sql: str) -> dict:
    with closing(_connect()) as conn:
        # A full scan the model did not bound would otherwise hold the request.
        # Returning true from the handler aborts with sqlite3.OperationalError,
        # which the retry loop feeds back to the model.
        deadline = time.monotonic() + QUERY_TIMEOUT
        conn.set_progress_handler(lambda: time.monotonic() > deadline, 10_000)
        cur = conn.execute(sql)
        columns = [d[0] for d in cur.description or []]
        rows = cur.fetchmany(MAX_ROWS)
        truncated = cur.fetchone() is not None
    return {"columns": columns, "rows": [list(r) for r in rows], "truncated": truncated}


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


def answer(messages: list[dict]) -> dict:
    """Ask the model for SQL, run it, and hand any failure back for a rewrite.

    The model cannot see the guard or the database, so an error it never reads is
    an error it repeats. Each failed attempt goes back in as a turn.
    """
    sql, error = "", ""

    for _ in range(MAX_TRIES):
        reply = json.loads(
            client.chat(model=MODEL, messages=messages, format=RESPONSE_SCHEMA)["message"]["content"]
        )
        note, sql = reply["note"], reply["sql"].strip()

        if not sql:  # model says the schema cannot answer this
            return {"note": note, "sql": "", "columns": [], "rows": []}

        try:
            sql = check(sql)
            return {"note": note, "sql": sql, **_run(sql)}
        except (UnsafeQuery, sqlite3.Error) as e:
            error = str(e).strip()
            messages = messages + [
                {"role": "assistant", "content": json.dumps(reply)},
                {"role": "user", "content":
                    f"Essa consulta falhou: {error}. Reescreva a SQL corrigindo o erro."},
            ]

    return {"note": "", "sql": sql, "columns": [], "rows": [],
            "error": f"Não consegui gerar uma consulta válida após {MAX_TRIES} tentativas: {error}"}


@app.post("/chat")
def chat(ask: Ask):
    return answer(
        [{"role": "system", "content": SYSTEM.format(schema=_schema())}, *ask.messages[-HISTORY:]]
    )
