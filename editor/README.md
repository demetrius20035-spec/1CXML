# GUI-редактор схемы `.1cmeta`

Десктоп-редактор структуры метаданных 1С на Python + PySide6 (Qt).

## Установка и запуск

```bash
cd editor
python -m pip install -r requirements.txt          # PySide6
python -m onec_schema_editor ../examples/sample.1cmeta
```

или через установку пакета:

```bash
python -m pip install -e .
onec-schema-editor ../examples/sample.1cmeta
```

> Для запуска GUI нужны системные библиотеки Qt (на headless-Linux:
> `libegl1 libgl1 libxkbcommon0 libdbus-1-3`).

## Тесты

Логика модели/операций покрыта тестами и не требует GUI:

```bash
python -m pytest tests -q
```

## Архитектура

| Модуль            | Ответственность                                                    |
|-------------------|--------------------------------------------------------------------|
| `model.py`        | Потоковая загрузка/сохранение NDJSON, дерево `SchemaNode`, метки типов |
| `tree_model.py`   | `QAbstractItemModel` для `QTreeView`: чекбоксы, колонки, цвета      |
| `operations.py`   | Статистика, поиск/замена, высушивание составных типов (чистая логика) |
| `dialogs.py`      | Диалоги редактирования типа и высушивания                          |
| `app.py`          | Главное окно, доки, тулбар, контекстное меню                       |

`model.py` и `operations.py` не зависят от Qt — их можно использовать как
библиотеку для скриптовой обработки схем.

## Горячие клавиши

| Клавиша        | Действие                |
|----------------|-------------------------|
| `Ctrl+O`       | Открыть схему           |
| `Ctrl+S`       | Сохранить               |
| `Ctrl+Shift+S` | Сохранить как           |
| `Ctrl+T`       | Редактировать тип       |
| `Ctrl+D`       | Высушить составной тип  |
| `Del`          | Удалить узел            |

## Формат

См. [../docs/FORMAT.md](../docs/FORMAT.md).
