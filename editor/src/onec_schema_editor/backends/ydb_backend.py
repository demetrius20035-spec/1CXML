"""Бэкенд Yandex Cloud Managed Service for YDB. Требует пакет ydb.

Загрузка выполняется батч-UPSERT'ами. Для простоты и отсутствия зависимости от
схемы типов в рантайме значения инлайнятся в YQL как литералы (строки —
экранируются, числа/булевы — как есть, даты — как Timestamp).
"""

from __future__ import annotations

import datetime

from .base import YDBConfig


class YDBConnection:
    def __init__(self, config: YDBConfig):
        try:
            import ydb  # noqa: F401
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError("Не установлен ydb. Установите: pip install ydb") from exc
        import ydb

        if config.use_metadata_creds:
            creds = ydb.iam.MetadataUrlCredentials()
        elif config.sa_key_file:
            creds = ydb.iam.ServiceAccountCredentials.from_service_account_file(config.sa_key_file)
        elif config.token:
            creds = ydb.AccessTokenCredentials(config.token)
        else:
            creds = ydb.AnonymousCredentials()

        self._driver = ydb.Driver(endpoint=config.endpoint, database=config.database,
                                  credentials=creds)
        self._driver.wait(timeout=15)
        self._pool = ydb.SessionPool(self._driver)
        self._database = config.database

    def _execute(self, yql: str) -> None:
        def cb(session):
            session.transaction().execute(yql, commit_tx=True)
        self._pool.retry_operation_sync(cb)

    def execute_script(self, script: str) -> None:
        for stmt in _split_statements(script):
            self._scheme_exec(stmt)

    def _scheme_exec(self, ddl: str) -> None:
        def cb(session):
            session.execute_scheme(ddl)
        self._pool.retry_operation_sync(cb)

    def insert_batch(self, sql_table: str, columns: list[str], rows: list[list]) -> None:
        cols = ", ".join(f"`{c}`" for c in columns)
        values = ",\n".join("(" + ", ".join(_lit(v) for v in row) + ")" for row in rows)
        yql = f"UPSERT INTO `{sql_table}` ({cols}) VALUES\n{values};"
        self._execute(yql)

    def close(self) -> None:
        try:
            self._pool.stop()
            self._driver.stop()
        except Exception:  # pragma: no cover
            pass


def _lit(value) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(value)
    if isinstance(value, str):
        # пытаемся распознать ISO-дату -> Timestamp
        if _looks_like_iso(value):
            return f'Timestamp("{value}Z")' if "Z" not in value else f'Timestamp("{value}")'
        escaped = value.replace("\\", "\\\\").replace('"', '\\"')
        return f'Utf8("{escaped}")'
    return f'Utf8("{str(value)}")'


def _looks_like_iso(s: str) -> bool:
    try:
        datetime.datetime.fromisoformat(s.replace("Z", ""))
        return "T" in s
    except (ValueError, AttributeError):
        return False


def _split_statements(script: str) -> list[str]:
    return [s.strip() for s in script.split(";") if s.strip() and not s.strip().startswith("--")]


def test_connection(config: YDBConfig) -> str:
    conn = YDBConnection(config)
    try:
        return f"OK, YDB подключён: {config.database}"
    finally:
        conn.close()
