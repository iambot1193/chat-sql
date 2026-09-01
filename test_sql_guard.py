from app.sql_guard import check, UnsafeQuery


def _rejects(sql):
    try:
        check(sql)
    except UnsafeQuery:
        return True
    return False


def test_guard():
    assert check("SELECT 1;") == "SELECT 1"
    assert check("  select a from t  ") == "select a from t"
    assert check("WITH x AS (SELECT 1) SELECT * FROM x") .startswith("WITH")
    # a column whose name contains a keyword must still pass
    assert check("SELECT created_at, updated_by FROM orders")

    assert _rejects("DROP TABLE orders")
    assert _rejects("SELECT 1; DROP TABLE orders")
    assert _rejects("WITH x AS (INSERT INTO t VALUES (1) RETURNING *) SELECT * FROM x")
    assert _rejects("SELECT 1 -- ok\n; DELETE FROM orders")
    assert _rejects("/* SELECT */ UPDATE orders SET status='paid'")
    assert _rejects("")
    assert _rejects("SELECT pg_sleep(60)")
    # valid SELECT, but SQLite would run native code
    assert _rejects("SELECT load_extension('evil.so')")
    assert _rejects("ATTACH DATABASE '/etc/passwd' AS x")
    print("ok")


if __name__ == "__main__":
    test_guard()
