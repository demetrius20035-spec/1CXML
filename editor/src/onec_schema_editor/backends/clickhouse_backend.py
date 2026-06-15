"""Бэкенд Yandex Cloud Managed ClickHouse. Требует пакет clickhouse-connect."""

from __future__ import annotations

from .base import ClickHouseConfig


class ClickHouseConnection:
    def __init__(self, config: ClickHouseConfig):
        try:
            import clickhouse_connect  # noqa: F401
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError(
                "Не установлен clickhouse-connect. "
                "Установите: pip install clickhouse-connect") from exc
        import clickhouse_connect
        kwargs = dict(
            host=config.host, port=config.port, username=config.user,
            password=config.password, database=config.database,
            secure=config.secure,
        )
        if config.ca_cert:
            kwargs["ca_cert"] = config.ca_cert
        self._client = clickhouse_connect.get_client(**kwargs)

    def execute_script(self, script: str) -> None:
        for stmt in _split_statements(script):
            self._client.command(stmt)

    def insert_batch(self, sql_table: str, columns: list[str], rows: list[list]) -> None:
        self._client.insert(sql_table, rows, column_names=columns)

    def close(self) -> None:
        try:
            self._client.close()
        except Exception:  # pragma: no cover
            pass


def _split_statements(script: str) -> list[str]:
    return [s.strip() for s in script.split(";") if s.strip() and not s.strip().startswith("--")]


def test_connection(config: ClickHouseConfig) -> str:
    conn = ClickHouseConnection(config)
    try:
        ver = conn._client.command("SELECT version()")
        return f"OK, ClickHouse {ver}"
    finally:
        conn.close()
