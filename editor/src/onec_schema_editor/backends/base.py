"""Базовый слой бэкендов загрузки: конфиги подключений и движок батч-вставки.

Драйверы СУБД импортируются лениво внутри конкретных бэкендов, поэтому модуль
(и весь редактор) работает даже без установленных mariadb/clickhouse/ydb/boto3.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Optional, Protocol

from ..datafile import iter_rows_multi
from ..model import Schema
from ..rowmap import build_mappers
from ..sqlgen import DDLOptions

ProgressFn = Callable[[int, str], None]


# ---------------------------------------------------------------- конфиги
@dataclass
class MariaDBConfig:
    host: str = "localhost"
    port: int = 3306
    user: str = "root"
    password: str = ""
    database: str = "onec"
    ssl: bool = False


@dataclass
class ClickHouseConfig:
    host: str = ""               # <id>.mdb.yandexcloud.net
    port: int = 8443             # HTTPS-интерфейс YC Managed ClickHouse
    user: str = "admin"
    password: str = ""
    database: str = "onec"
    secure: bool = True
    ca_cert: str = ""            # путь к YandexInternalRootCA.crt


@dataclass
class YDBConfig:
    endpoint: str = ""           # grpcs://ydb.serverless.yandexcloud.net:2135
    database: str = ""           # /ru-central1/<folder>/<db>
    sa_key_file: str = ""        # ключ сервисного аккаунта (JSON)
    token: str = ""              # либо IAM-токен
    use_metadata_creds: bool = False  # внутри YC (ВМ/Cloud Functions)


@dataclass
class S3Config:
    endpoint: str = "https://storage.yandexcloud.net"
    region: str = "ru-central1"
    bucket: str = ""
    access_key: str = ""
    secret_key: str = ""
    prefix: str = ""             # префикс ключа (папка) в бакете


@dataclass
class LoadResult:
    rows: int = 0
    tables: dict = field(default_factory=dict)
    skipped_objects: list = field(default_factory=list)


class LoaderConnection(Protocol):
    def insert_batch(self, sql_table: str, columns: list[str], rows: list[list]) -> None: ...
    def close(self) -> None: ...


def run_load(conn: LoaderConnection, schema: Schema, opts: DDLOptions,
             data_path: str, progress: Optional[ProgressFn] = None,
             batch_size: int = 1000) -> LoadResult:
    """Общий движок: читает .1cdata и батчами вставляет строки через ``conn``.

    Прогресс сообщается коллбэком (обработано_строк, имя_таблицы) — для отображения
    в UI и индикации «живости».
    """
    mappers = build_mappers(schema, opts)
    result = LoadResult()
    buffers: dict[str, list[list]] = {}
    done = 0

    def flush(full_name: str) -> None:
        mapper = mappers[full_name]
        rows = buffers.get(full_name)
        if rows:
            conn.insert_batch(mapper.sql_table, mapper.columns, rows)
            result.tables[full_name] = result.tables.get(full_name, 0) + len(rows)
            rows.clear()

    for obj, data in iter_rows_multi(data_path):
        mapper = mappers.get(obj.full_name)
        if mapper is None:
            if obj.full_name not in result.skipped_objects:
                result.skipped_objects.append(obj.full_name)
            continue
        buffers.setdefault(obj.full_name, []).append(mapper.extract(data))
        done += 1
        result.rows = done
        if len(buffers[obj.full_name]) >= batch_size:
            flush(obj.full_name)
            if progress:
                progress(done, obj.full_name)

    for full_name in list(buffers):
        flush(full_name)
    if progress:
        progress(done, "")
    conn.close()
    return result
