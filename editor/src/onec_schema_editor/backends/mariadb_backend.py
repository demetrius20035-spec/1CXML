"""Бэкенд MariaDB/MySQL. Требует пакет PyMySQL (опционально)."""

from __future__ import annotations

from .base import MariaDBConfig


class MariaDBConnection:
    def __init__(self, config: MariaDBConfig):
        try:
            import pymysql  # noqa: F401
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError(
                "Не установлен PyMySQL. Установите: pip install pymysql") from exc
        import pymysql
        self._conn = pymysql.connect(
            host=config.host, port=config.port, user=config.user,
            password=config.password, database=config.database,
            charset="utf8mb4", autocommit=False,
            ssl={"ssl": {}} if config.ssl else None,
        )

    def execute_script(self, script: str) -> None:
        cur = self._conn.cursor()
        for stmt in _split_statements(script):
            cur.execute(stmt)
        self._conn.commit()
        cur.close()

    def insert_batch(self, sql_table: str, columns: list[str], rows: list[list]) -> None:
        cols = ", ".join(f"`{c}`" for c in columns)
        placeholders = ", ".join(["%s"] * len(columns))
        sql = f"INSERT INTO `{sql_table}` ({cols}) VALUES ({placeholders})"
        cur = self._conn.cursor()
        cur.executemany(sql, rows)
        self._conn.commit()
        cur.close()

    def close(self) -> None:
        try:
            self._conn.close()
        except Exception:  # pragma: no cover
            pass


def _split_statements(script: str) -> list[str]:
    return [s.strip() for s in script.split(";") if s.strip() and not s.strip().startswith("--")]


def test_connection(config: MariaDBConfig) -> str:
    conn = MariaDBConnection(config)
    try:
        cur = conn._conn.cursor()
        cur.execute("SELECT VERSION()")
        ver = cur.fetchone()[0]
        cur.close()
        return f"OK, MariaDB/MySQL {ver}"
    finally:
        conn.close()
