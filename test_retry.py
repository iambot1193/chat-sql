"""The retry loop is the only non-trivial branch in main; this pins its behaviour.

Stubs the model so no Ollama server is needed.
"""

import json

from app import main


def _stub(replies):
    """Feed canned model replies; record what the model was shown each call."""
    seen = []

    def chat(model, messages, format):
        seen.append(messages)
        return {"message": {"content": json.dumps(replies[len(seen) - 1])}}

    main.client.chat = chat
    return seen


def test_good_sql_runs_once():
    seen = _stub([{"sql": "SELECT COUNT(*) AS n FROM vehicles", "note": "ok"}])
    out = main.answer([{"role": "user", "content": "quantos veículos?"}])

    assert len(seen) == 1, "valid SQL must not be retried"
    assert "error" not in out
    assert out["rows"][0][0] > 0


def test_bad_sql_is_fed_back_and_retried():
    seen = _stub([
        {"sql": "SELECT * FROM carros", "note": "erro"},          # no such table
        {"sql": "SELECT make FROM vehicles LIMIT 1", "note": "ok"},
    ])
    out = main.answer([{"role": "user", "content": "marcas"}])

    assert len(seen) == 2, "a failed query must trigger exactly one retry"
    assert "carros" in seen[1][-1]["content"], "the model must be shown the error text"
    assert "error" not in out
    assert out["rows"]


def test_blocked_sql_gives_up_after_max_tries():
    seen = _stub([{"sql": "DROP TABLE vehicles", "note": "x"}] * main.MAX_TRIES)
    out = main.answer([{"role": "user", "content": "apague tudo"}])

    assert len(seen) == main.MAX_TRIES, "must stop at MAX_TRIES, not loop forever"
    assert "error" in out
    assert out["rows"] == []


def test_empty_sql_returns_the_note():
    seen = _stub([{"sql": "", "note": "Não há dados de faturamento."}])
    out = main.answer([{"role": "user", "content": "qual o faturamento?"}])

    assert len(seen) == 1, "an unanswerable question must not burn retries"
    assert out["note"] == "Não há dados de faturamento."
    assert out["columns"] == []


if __name__ == "__main__":
    test_good_sql_runs_once()
    test_bad_sql_is_fed_back_and_retried()
    test_blocked_sql_gives_up_after_max_tries()
    test_empty_sql_returns_the_note()
    print("ok")
