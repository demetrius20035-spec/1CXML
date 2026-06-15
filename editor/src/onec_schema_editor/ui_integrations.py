"""Окно интеграций: подключения к MariaDB / ClickHouse / YDB / S3,
создание таблиц по DDL, загрузка данных с индикацией прогресса."""

from __future__ import annotations

import os
from typing import Callable

from PySide6.QtCore import QObject, QThread, Signal
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QFileDialog, QFormLayout, QGroupBox,
    QHBoxLayout, QLabel, QLineEdit, QMessageBox, QProgressBar, QPushButton,
    QSpinBox, QTabWidget, QTextEdit, QVBoxLayout, QWidget,
)

from .backends import base
from .backends.base import (
    ClickHouseConfig, MariaDBConfig, S3Config, YDBConfig, run_load,
)
from .datafile import summarize
from .sqlgen import DDLOptions, generate_ddl


class Worker(QObject):
    """Универсальный воркер: запускает функцию в потоке, шлёт прогресс/итог."""
    progress = Signal(int, int, str)   # done, total, message
    finished = Signal(str)
    failed = Signal(str)

    def __init__(self, fn: Callable[["Worker"], str]):
        super().__init__()
        self._fn = fn

    def run(self):
        try:
            msg = self._fn(self)
            self.finished.emit(msg or "Готово")
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(f"{type(exc).__name__}: {exc}")


class _BaseTab(QWidget):
    """Общая инфраструктура вкладки: лог, прогресс-бар, запуск воркера."""

    def __init__(self, get_schema, get_opts, parent=None):
        super().__init__(parent)
        self.get_schema = get_schema
        self.get_opts = get_opts
        self._thread = None
        self._worker = None
        self.progress_bar = QProgressBar()
        self.progress_bar.setFormat("%v / %m  (%p%)")
        self.log = QTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumHeight(140)

    def _tail(self):
        box = QVBoxLayout()
        box.addWidget(self.progress_bar)
        box.addWidget(QLabel("Журнал:"))
        box.addWidget(self.log)
        return box

    def _run(self, fn: Callable[[Worker], str]):
        if self._thread and self._thread.isRunning():
            QMessageBox.warning(self, "Занято", "Операция уже выполняется.")
            return
        self.progress_bar.setValue(0)
        self._thread = QThread()
        self._worker = Worker(fn)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.progress.connect(self._on_progress)
        self._worker.finished.connect(self._on_finished)
        self._worker.failed.connect(self._on_failed)
        self._worker.finished.connect(self._thread.quit)
        self._worker.failed.connect(self._thread.quit)
        self._thread.start()
        self.log.append("▶ Старт…")

    def _on_progress(self, done, total, message):
        if total:
            self.progress_bar.setMaximum(total)
        self.progress_bar.setValue(done)
        if message:
            self.log.append(f"  {message}: {done}/{total}")

    def _on_finished(self, message):
        self.log.append(f"✔ {message}")

    def _on_failed(self, message):
        self.log.append(f"✖ Ошибка: {message}")
        QMessageBox.critical(self, "Ошибка", message)


class _SqlTab(_BaseTab):
    """Вкладка СУБД: подключение + создать таблицы + загрузить данные."""

    def __init__(self, dialect: str, get_schema, get_opts, parent=None):
        super().__init__(get_schema, get_opts, parent)
        self.dialect = dialect
        layout = QVBoxLayout(self)
        layout.addWidget(self._connection_box())

        actions = QGroupBox("Действия")
        av = QVBoxLayout(actions)
        data_row = QHBoxLayout()
        self.data_path = QLineEdit()
        self.data_path.setPlaceholderText("Файл/каталог .1cdata")
        browse = QPushButton("Обзор…")
        browse.clicked.connect(self._browse_data)
        data_row.addWidget(QLabel("Данные:"))
        data_row.addWidget(self.data_path)
        data_row.addWidget(browse)
        av.addLayout(data_row)

        self.batch = QSpinBox()
        self.batch.setRange(100, 100000)
        self.batch.setValue(1000)
        av.addWidget(self._labeled("Размер батча вставки:", self.batch))

        btn_row = QHBoxLayout()
        b_test = QPushButton("Проверить подключение")
        b_ddl = QPushButton("Создать таблицы (DDL)")
        b_load = QPushButton("Загрузить данные")
        b_test.clicked.connect(self._test)
        b_ddl.clicked.connect(self._create_tables)
        b_load.clicked.connect(self._load)
        for b in (b_test, b_ddl, b_load):
            btn_row.addWidget(b)
        av.addLayout(btn_row)
        layout.addWidget(actions)
        layout.addLayout(self._tail())

    def _labeled(self, text, widget):
        w = QWidget()
        h = QHBoxLayout(w)
        h.setContentsMargins(0, 0, 0, 0)
        h.addWidget(QLabel(text))
        h.addWidget(widget)
        h.addStretch()
        return w

    def _connection_box(self):
        box = QGroupBox(f"Подключение — {self.dialect}")
        form = QFormLayout(box)
        self.fields = {}
        if self.dialect == "mariadb":
            self._add(form, "host", "Хост", "localhost")
            self._add(form, "port", "Порт", "3306")
            self._add(form, "user", "Пользователь", "root")
            self._add(form, "password", "Пароль", "", password=True)
            self._add(form, "database", "База", "onec")
        elif self.dialect == "clickhouse":
            self._add(form, "host", "Хост (….mdb.yandexcloud.net)", "")
            self._add(form, "port", "Порт", "8443")
            self._add(form, "user", "Пользователь", "admin")
            self._add(form, "password", "Пароль", "", password=True)
            self._add(form, "database", "База", "onec")
            self._add(form, "ca_cert", "CA-сертификат (YandexInternalRootCA.crt)", "")
        elif self.dialect == "ydb":
            self._add(form, "endpoint", "Endpoint (grpcs://…:2135)", "")
            self._add(form, "database", "База (/ru-central1/…)", "")
            self._add(form, "sa_key_file", "Ключ сервис. аккаунта (JSON)", "")
            self._add(form, "token", "IAM-токен (опц.)", "", password=True)
        return box

    def _add(self, form, key, label, default, password=False):
        edit = QLineEdit(default)
        if password:
            edit.setEchoMode(QLineEdit.Password)
        form.addRow(label + ":", edit)
        self.fields[key] = edit

    def _config(self):
        f = {k: w.text() for k, w in self.fields.items()}
        db = self.get_opts().database
        if self.dialect == "mariadb":
            return MariaDBConfig(host=f["host"], port=int(f["port"] or 3306),
                                 user=f["user"], password=f["password"],
                                 database=f["database"] or db)
        if self.dialect == "clickhouse":
            return ClickHouseConfig(host=f["host"], port=int(f["port"] or 8443),
                                    user=f["user"], password=f["password"],
                                    database=f["database"] or db, ca_cert=f["ca_cert"])
        return YDBConfig(endpoint=f["endpoint"], database=f["database"],
                         sa_key_file=f["sa_key_file"], token=f["token"])

    def _browse_data(self):
        path, _ = QFileDialog.getOpenFileName(self, "Файл данных", "",
                                              "Данные 1С (*.1cdata);;Все файлы (*)")
        if path:
            self.data_path.setText(path)

    def _connect(self):
        if self.dialect == "mariadb":
            from .backends import mariadb_backend
            return mariadb_backend.MariaDBConnection(self._config())
        if self.dialect == "clickhouse":
            from .backends import clickhouse_backend
            return clickhouse_backend.ClickHouseConnection(self._config())
        from .backends import ydb_backend
        return ydb_backend.YDBConnection(self._config())

    def _test(self):
        def job(worker):
            if self.dialect == "mariadb":
                from .backends import mariadb_backend as b
            elif self.dialect == "clickhouse":
                from .backends import clickhouse_backend as b
            else:
                from .backends import ydb_backend as b
            return b.test_connection(self._config())
        self._run(job)

    def _create_tables(self):
        opts = self.get_opts()
        ddl = generate_ddl(self.get_schema(), self.dialect, opts)

        def job(worker):
            conn = self._connect()
            conn.execute_script(ddl)
            conn.close()
            return "Таблицы созданы (DDL выполнен)."
        self._run(job)

    def _load(self):
        path = self.data_path.text().strip()
        if not path or not os.path.exists(path):
            QMessageBox.warning(self, "Нет данных", "Укажите существующий файл .1cdata.")
            return
        opts = self.get_opts()
        schema = self.get_schema()
        batch = self.batch.value()

        def job(worker):
            total = summarize(path).rows
            worker.progress.emit(0, total, "Загрузка строк")
            conn = self._connect()
            result = run_load(conn, schema, opts, path,
                              progress=lambda done, tbl: worker.progress.emit(done, total, tbl),
                              batch_size=batch)
            skipped = (", пропущено объектов: " + str(len(result.skipped_objects))) \
                if result.skipped_objects else ""
            return f"Загружено строк: {result.rows}{skipped}"
        self._run(job)


class _S3Tab(_BaseTab):
    def __init__(self, get_schema, get_opts, parent=None):
        super().__init__(get_schema, get_opts, parent)
        layout = QVBoxLayout(self)

        box = QGroupBox("Yandex Object Storage (S3)")
        form = QFormLayout(box)
        self.endpoint = QLineEdit("https://storage.yandexcloud.net")
        self.region = QLineEdit("ru-central1")
        self.bucket = QLineEdit()
        self.access = QLineEdit()
        self.secret = QLineEdit()
        self.secret.setEchoMode(QLineEdit.Password)
        self.prefix = QLineEdit()
        form.addRow("Endpoint:", self.endpoint)
        form.addRow("Регион:", self.region)
        form.addRow("Бакет:", self.bucket)
        form.addRow("Access key:", self.access)
        form.addRow("Secret key:", self.secret)
        form.addRow("Префикс (папка):", self.prefix)
        layout.addWidget(box)

        files = QGroupBox("Архив и загрузка")
        fv = QFormLayout(files)
        self.data_path = QLineEdit()
        dp = QPushButton("Обзор данных…")
        dp.clicked.connect(self._browse_data)
        drow = QHBoxLayout(); drow.addWidget(self.data_path); drow.addWidget(dp)
        dw = QWidget(); dw.setLayout(drow)
        fv.addRow("Данные .1cdata:", dw)
        self.archive_path = QLineEdit()
        fv.addRow("Файл архива (.tar.gz):", self.archive_path)
        layout.addWidget(files)

        btns = QHBoxLayout()
        b_arc = QPushButton("Только архивировать")
        b_up = QPushButton("Архивировать и загрузить в S3")
        b_arc.clicked.connect(self._archive)
        b_up.clicked.connect(self._archive_upload)
        btns.addWidget(b_arc); btns.addWidget(b_up)
        layout.addLayout(btns)
        layout.addLayout(self._tail())

    def _browse_data(self):
        path, _ = QFileDialog.getOpenFileName(self, "Файл данных", "",
                                              "Данные 1С (*.1cdata);;Все файлы (*)")
        if path:
            self.data_path.setText(path)
            if not self.archive_path.text():
                self.archive_path.setText(os.path.splitext(path)[0] + ".tar.gz")

    def _config(self):
        return S3Config(endpoint=self.endpoint.text(), region=self.region.text(),
                        bucket=self.bucket.text(), access_key=self.access.text(),
                        secret_key=self.secret.text(), prefix=self.prefix.text())

    def _archive(self):
        from .backends import s3_backend
        data = self.data_path.text().strip()
        arc = self.archive_path.text().strip()

        def job(worker):
            s3_backend.make_archive(
                data, arc,
                progress=lambda d, t, m: worker.progress.emit(d, t, m))
            return f"Архив создан: {arc}"
        self._run(job)

    def _archive_upload(self):
        from .backends import s3_backend
        data = self.data_path.text().strip()
        arc = self.archive_path.text().strip()
        cfg = self._config()

        def job(worker):
            key = s3_backend.archive_and_upload(
                cfg, data, arc,
                progress=lambda d, t, m: worker.progress.emit(d, t, m))
            return f"Загружено в бакет {cfg.bucket}, ключ: {key}"
        self._run(job)


class IntegrationsDialog(QDialog):
    def __init__(self, get_schema, get_opts, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Интеграции: СУБД и Yandex Cloud")
        self.resize(720, 640)
        layout = QVBoxLayout(self)
        tabs = QTabWidget()
        tabs.addTab(_SqlTab("mariadb", get_schema, get_opts), "MariaDB")
        tabs.addTab(_SqlTab("clickhouse", get_schema, get_opts), "YC ClickHouse")
        tabs.addTab(_SqlTab("ydb", get_schema, get_opts), "YC YDB")
        tabs.addTab(_S3Tab(get_schema, get_opts), "YC Object Storage (S3)")
        layout.addWidget(tabs)
