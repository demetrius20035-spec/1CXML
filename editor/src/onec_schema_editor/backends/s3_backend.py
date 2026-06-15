"""Архивирование выгрузки и отправка в Yandex Cloud Object Storage (S3).

Архивирование (tar.gz) не требует зависимостей. Для загрузки нужен boto3.
"""

from __future__ import annotations

import os
import tarfile
from typing import Callable, Optional

from ..datafile import discover_volumes
from .base import S3Config

ProgressFn = Callable[[int, int, str], None]   # (done, total, message)


def make_archive(data_path: str, archive_path: str,
                 progress: Optional[ProgressFn] = None) -> str:
    """Упаковывает все тома .1cdata набора в один tar.gz."""
    files = discover_volumes(data_path)
    if not files:
        raise FileNotFoundError(f"Не найдены файлы .1cdata по пути: {data_path}")
    total = len(files)
    with tarfile.open(archive_path, "w:gz") as tar:
        for i, fpath in enumerate(files, 1):
            tar.add(fpath, arcname=os.path.basename(fpath))
            if progress:
                progress(i, total, f"Архивирование {os.path.basename(fpath)}")
    return archive_path


def upload_to_s3(config: S3Config, file_path: str,
                 progress: Optional[ProgressFn] = None) -> str:
    """Загружает файл в бакет YC Object Storage. Возвращает ключ объекта."""
    try:
        import boto3  # noqa: F401
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("Не установлен boto3. Установите: pip install boto3") from exc
    import boto3

    session = boto3.session.Session()
    client = session.client(
        service_name="s3",
        endpoint_url=config.endpoint,
        region_name=config.region,
        aws_access_key_id=config.access_key,
        aws_secret_access_key=config.secret_key,
    )
    key = (config.prefix.rstrip("/") + "/" if config.prefix else "") + os.path.basename(file_path)
    total = os.path.getsize(file_path)
    uploaded = {"n": 0}

    def cb(bytes_amount):
        uploaded["n"] += bytes_amount
        if progress:
            progress(uploaded["n"], total, f"Загрузка в {config.bucket}")

    client.upload_file(file_path, config.bucket, key, Callback=cb)
    return key


def archive_and_upload(config: S3Config, data_path: str, archive_path: str,
                       progress: Optional[ProgressFn] = None) -> str:
    make_archive(data_path, archive_path, progress)
    return upload_to_s3(config, archive_path, progress)
