# Подготовка релиза

Проект предназначен для локального запуска и публикации исходников.
Архив не содержит готового окружения, пользовательских загрузок или API-ключей.
Фактические результаты проверок приведены в `qa/release/REPORT.md`.

## Структура публикации

```text
semantic-contract-review/
├── README.md
├── app.py
├── pyproject.toml
├── requirements.txt / requirements-dev.txt
├── Start Demo.cmd
├── .gitignore / .gitattributes
├── .streamlit/config.toml
├── documents/                      # 6 исходных учебных TXT
├── src/contract_review/            # parser, extractor, resolver, модели, представление
├── tests/                          # полный набор pytest
├── scripts/                        # запуск, smoke, benchmark, сборка, QA
├── docs/
│   ├── requirements.md / release.md
│   └── images/demo.png             # реальный браузерный снимок
└── qa/
    ├── adversarial/
    │   ├── documents/              # 28 компактных синтетических TXT
    │   ├── MATRIX.json             # 193 независимых сценария
    │   ├── INPUT_SHA256.json        # неизменяемые контрольные суммы
    │   ├── UI_EXPECTATIONS.json    # исходные ожидания QA
    │   └── BASELINE.json           # 478 прежних test IDs и исходов, включая 6 failures
    └── release/REPORT.md            # краткий фактический отчёт
```

Runtime использует `pathlib` и не импортирует Windows API. `Start Demo.cmd`,
`start_demo.py` и отдельный launcher smoke предназначены для Windows;
Linux/macOS запускаются обычной командой `python -m streamlit run app.py`.
Полный запуск на Linux/macOS пока не проверялся.

## GitHub и Streamlit Community Cloud

Для будущего облачного запуска: входной файл **`app.py`**, корень репозитория,
**Python 3.11**, зависимости **`requirements.txt`**. В общей конфигурации
нет `server.address`; не переносите туда локальную привязку из команды запуска.
Папки `documents/` и `src/` обязательно должны присутствовать в репозитории.
Кнопка примера читает файлы относительно приложения, а не рабочего каталога.

Cloud устанавливает зависимости из манифеста проекта; Python выбирается в
Advanced settings при создании приложения. Сверено с официальными инструкциями
[зависимостей](https://docs.streamlit.io/deploy/streamlit-community-cloud/deploy-your-app/app-dependencies)
и [развёртывания](https://docs.streamlit.io/deploy/streamlit-community-cloud/deploy-your-app/deploy).
Локальная установка проверяет пакет и зависимости; она не доказывает успешность
облачного развёртывания. Python 3.11 поддерживается проектом, автоматическое
переключение на новую версию перед сдачей не проверено.

Перед публичным запуском:

1. Выполнить пользовательскую приёмку. Редакция README утверждена автором;
   в ней указана его оценка — около 3 часов активной работы.
2. Проверить право на публикацию учебных материалов и условия лицензирования проекта.
3. Проверить явно выбранные файлы и staged diff перед коммитом и отправкой
   в `nmarkin/semantic-contract-review`. Окружения, логи и ZIP не добавлять.
4. При решении опубликовать демо выбрать Python 3.11 и `app.py`, выполнить облачный
   smoke с шестью TXT, затем добавить в README реальную ссылку на демо.

Оригинальный `PO_vibecoding_task.pdf` сверен: для сдачи достаточно архива исходников
или доступного репозитория и локального запуска. GitHub и онлайн-демо необязательны.

После появления публичного репозитория откройте `share.streamlit.io`, выберите
Create app и укажите `nmarkin/semantic-contract-review`, ветку `main`, файл `app.py`.
В Advanced settings выберите Python 3.11; секреты приложению не нужны.
После Deploy проверьте «Открыть пример», четыре вопроса задания, переход к источнику
и скачивание HTML. Только затем добавьте фактический URL приложения в README.
GitHub Release с ZIP создаётся отдельно после окончательного обновления README.

В UI и README есть предупреждение: публичное демо — только для учебных,
не конфиденциальных документов. Никакой корпоративной защиты в этом MVP не заявлено.

## Компоненты обработки

| Задача | Компоненты |
| --- | --- |
| Прочитать TXT, сохранить оригинал и абзацы | `encoding.py`, `parser.py` |
| Найти факты, роли и признаки изменения с цитатами | `extractor.py`, `change_intent.py` |
| Определить применимость, историю и конфликты | `resolver.py`, типизированные модели |
| Подготовить объяснения, предложения и выводы | Модули представления в `src/contract_review/` |
| Показать таблицу, источники и автономный отчёт | `app.py`, `table_view.py`, `report.py` |

## План демонстрации

Первые 2 минуты — загрузка примера и обзор; следующие 4 — четыре вопроса задания
с источниками; ещё 2 — изменение поля и новый договор; последние 2 минуты —
ограничения. Далее в рамках 30-минутной встречи — обсуждение и небольшое изменение
с ИИ. Это план, а не запись проведённой встречи. Извлечение ставки неустойки —
возможная задача встречи из исходного задания; заранее она не реализована.

## Воспроизведение проверок

После установки `requirements-dev.txt` (команды ниже используют активированное venv):

```text
python -m pytest -q
python -m pip check
python scripts/start_demo.py --check
```

Последняя команда — Windows-проверка launcher, импорта и пустой сессии.
Для полного браузерного smoke дополнительно нужны Microsoft Edge и Playwright
(опциональный инструмент QA, не runtime-зависимость):

```text
python -m pip install playwright==1.64.1
python scripts/smoke_cold_start.py
python scripts/smoke_adversarial.py
```

Smoke запускают собственные локальные серверы и останавливают только их.
`smoke_cold_start.py` включает `smoke_ui.py`: реальные файлы, пример, переход к
источнику, фильтр, ошибки кодировки и автономный HTML. Артефакты сохраняются в
`.pytest_cache/` и `qa/regression42b/browser/`, но в публикацию не входят.

Для воспроизведения сводки независимого QA:

```text
python -m pytest -q --junitxml=qa/regression42b/pytest-results.xml
python scripts/summarize_regression.py
```

Скрипт сравнит прежние идентификаторы с компактным `BASELINE.json` и проверит
все 193 фактических снимка из `.pytest_cache/adversarial_results/`. Прежние
шесть failures должны пройти. `MATRIX.json` и TXT не генерируются из ответов
Resolver; не перезаписывайте их ради зелёных тестов. `build_adversarial_fixtures.py`
сохранён как исторический генератор независимого корпуса, для запуска он не нужен.
`summarize_adversarial.py` относится к историческому этапу 4.2A и требует его
локального JUnit; для актуальной сводки используйте `summarize_regression.py`.

Большие старые снимки JSON, JUnit, логи, PNG и скачанные HTML остались локально,
но исключены из публикации. Сохранены все входы, ожидания, тесты и скрипты;
`BASELINE.json` содержит только прежние test IDs и исходы без персональных путей
и traceback. `.gitattributes` сохраняет байты хешируемых TXT/JSON при Git checkout.

## Чистый архив

Финальный ZIP предпочтительно собирать из проверенного коммита:

```text
git archive --format=zip --prefix=semantic-contract-review/ --output=dist/semantic-contract-review.zip HEAD
```

Папка `dist/` должна существовать. После сборки проверьте состав, SHA-256,
распаковку и установку. Сам архив в Git не добавляйте.
Для предварительной сборки из рабочей папки без Git-коммита доступен сборщик:

```text
python scripts/build_release.py
```

Результат — `dist/semantic-contract-review.zip`. Сборщик использует разрешённый
список файлов, проверяет относительные пути и отсутствие подозрительных секретов
и локальных абсолютных Windows-путей в текстах. Содержимое не берётся целиком
из рабочей папки. ZIP включает один корневой каталог; вложенных архивов нет.
Хеши всех упакованных файлов и архива записываются рядом в `dist/release-manifest.json`.
Автоматический поиск подозрительных строк не доказывает отсутствие любых секретов;
для отправки используйте именно проверенный архив.

## Локальный Git и состав публикации

Основная ветка — `main`. Утверждённый README устанавливается без самостоятельного
переписывания продуктовых разделов. Публикация выполняется только после проверки
явно выбранных файлов и staged diff. Команды ниже позволяют проверить состояние
конкретной локальной копии без изменения индекса.

Проверен состав из 93 файлов: он совпадает с разрешённым составом
`scripts/build_release.py`. Все исходники, тесты, 6 учебных TXT и 28 синтетических
QA-файлов сохранены. Расширены исключения для дополнительных окружений, кэшей,
локальных настроек, ключей, архивов и временных файлов; 30 проверок исключения
прошли. Локальные журналы и копии проекта не удалялись.

В кандидатах не найдено абсолютных Windows-путей, приватных ключей,
типовых токенов, паролей в URL и явных строк присваивания секретов по проверенным
шаблонам. Это проверка текущего состава, не гарантия обнаружения любых секретов.
Единственный бинарный файл публикации — реальный снимок учебного интерфейса.

Проверить список повторно без изменения индекса:

```text
git status --short --untracked-files=all
git ls-files --others --exclude-standard
git ls-files
git diff --cached --name-only
git remote -v
```

Полный проверенный список файлов:

```text
.gitattributes
.gitignore
.streamlit/config.toml
README.md
Start Demo.cmd
app.py
docs/images/demo.png
docs/release.md
docs/requirements.md
documents/01_contract.txt
documents/02_amendment_signed.txt
documents/03_contract.txt
documents/04_contract.txt
documents/05_supplier_email.txt
documents/06_amendment_draft.txt
pyproject.toml
qa/adversarial/BASELINE.json
qa/adversarial/INPUT_SHA256.json
qa/adversarial/MATRIX.json
qa/adversarial/UI_EXPECTATIONS.json
qa/adversarial/documents/A.txt
qa/adversarial/documents/B.txt
qa/adversarial/documents/C.txt
qa/adversarial/documents/D.txt
qa/adversarial/documents/E.txt
qa/adversarial/documents/F.txt
qa/adversarial/documents/G.txt
qa/adversarial/documents/H.txt
qa/adversarial/documents/base.txt
qa/adversarial/documents/conflicting-amounts.txt
qa/adversarial/documents/contradictory_signing.txt
qa/adversarial/documents/date_only.txt
qa/adversarial/documents/equal.txt
qa/adversarial/documents/future.txt
qa/adversarial/documents/late.txt
qa/adversarial/documents/many_proposals.txt
qa/adversarial/documents/missing_spec.txt
qa/adversarial/documents/mixed.txt
qa/adversarial/documents/other_base.txt
qa/adversarial/documents/price-increment.txt
qa/adversarial/documents/relative-deadline.txt
qa/adversarial/documents/same1.txt
qa/adversarial/documents/same2.txt
qa/adversarial/documents/second_base.txt
qa/adversarial/documents/two_links.txt
qa/adversarial/documents/unknown-completion.txt
qa/adversarial/documents/unknown-dispatch.txt
qa/adversarial/documents/wrong_supplier.txt
qa/release/REPORT.md
requirements-dev.txt
requirements.txt
scripts/benchmark.py
scripts/build_adversarial_fixtures.py
scripts/build_release.py
scripts/smoke_adversarial.py
scripts/smoke_cold_start.py
scripts/smoke_launcher_windows.py
scripts/smoke_ui.py
scripts/start_demo.py
scripts/summarize_adversarial.py
scripts/summarize_regression.py
src/contract_review/__init__.py
src/contract_review/change_intent.py
src/contract_review/encoding.py
src/contract_review/extractor.py
src/contract_review/insights.py
src/contract_review/models.py
src/contract_review/parser.py
src/contract_review/patterns.py
src/contract_review/presentation.py
src/contract_review/proposal_view.py
src/contract_review/report.py
src/contract_review/resolution_models.py
src/contract_review/resolver.py
src/contract_review/status_context.py
src/contract_review/table_view.py
tests/adversarial_support.py
tests/conftest.py
tests/test_adversarial.py
tests/test_adversarial_ui.py
tests/test_app.py
tests/test_critical_regression.py
tests/test_encoding.py
tests/test_examples.py
tests/test_extractor.py
tests/test_insights_report.py
tests/test_launcher.py
tests/test_parser.py
tests/test_presentation.py
tests/test_reliability.py
tests/test_resolver.py
tests/test_semantics.py
tests/test_status_ui.py
```
