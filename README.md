# call-analyzer — локальный анализ звонков

[![CI](https://github.com/vov4ig/call-analyzer/actions/workflows/ci.yml/badge.svg)](../../actions)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Python 3.11](https://img.shields.io/badge/python-3.11-3776AB.svg)](https://www.python.org)

**Language / Язык:** [English](README.en.md) · **Русский**

> **EN:** call-analyzer is an on-premise call-center analytics system:
> speaker-labeled transcription and structured quality reports, produced entirely
> on your own infrastructure with no internet connection after setup.

`call-analyzer` — локальная система анализа звонков контакт-центра. Сервис
принимает аудиозапись через веб-интерфейс и формирует расшифровку с разделением
по говорящим, а также структурированный отчёт о качестве взаимодействия
оператора с клиентом.

Обработка выполняется полностью на инфраструктуре организации: записи не
передаются во внешние сервисы, а после установки не требуется подключение к
интернету. Решение предназначено для использования в контурах с ограниченным
доступом к сети и при работе с персональными данными, когда важно сохранить
аудио внутри внутреннего контура.

Основные сценарии — выборочный контроль качества, разбор обращений и подготовка
материалов для обучения операторов.

```
mp3/wav/m4a/ogg/видео
        │
        ▼
  Whisper large-v3  ──►  wav2vec2 (выравнивание слов)
        │
        ▼
  pyannote 3.3 (диаризация)  ──►  диалог «кто что сказал»
        │
        ▼
  qwen2.5:7b через Ollama  ──►  отчёт: 9 разделов + рекомендации
```

## Что внутри

| Этап | Модель | Где считается |
|---|---|---|
| Распознавание речи | faster-whisper large-v3 (fp16) | GPU, CTranslate2 |
| Выравнивание слов | wav2vec2 XLSR-53 (ru), torchaudio 960H (en) | GPU |
| Диаризация | pyannote.audio 3.3.2 (segmentation-3.0 + WeSpeaker) | GPU |
| Отчёт | qwen2.5:7b-instruct | Ollama, GPU |

Отчёт содержит: краткое содержание, тему обращения, кто оператор и кто клиент,
проблему клиента, как решили, эмоции и тон, соблюдение скрипта, ошибки оператора,
рекомендации. Рядом с отчётом сохраняются `.json` (сегменты + слова + говорящие),
`.txt`, `.srt` и `.md`.

Веб-интерфейс написан на стандартной библиотеке Python (`http.server`): нет
FastAPI, нет node, нет CDN. Очередь задач последовательная — 8 ГБ VRAM не
позволяют держать два пайплайна одновременно.

## Требования

- Windows 10/11 x64, PowerShell 5.1+
- NVIDIA GPU: проверено на **RTX 5060 Laptop, 8 ГБ, драйвер 591.44**
- 6 ГБ VRAM минимум (large-v3 fp16 ≈ 4.7 ГБ + диаризация)
- ~15 ГБ на диске: 8.9 ГБ модели, ~3 ГБ колёса CUDA, остальное окружение
- Права администратора **не нужны**: всё ставится в `C:\tools` и `C:\cc`

### Подбор сборки PyTorch

| Видеокарта | Архитектура | Ключ установки |
|---|---|---|
| RTX 50xx | Blackwell (sm_120) | `-Torch cu128` (по умолчанию) |
| RTX 40xx, 30xx | Ada / Ampere | `-Torch cu128` |
| RTX 20xx, GTX 16xx | Turing | `-Torch cu128` |
| GTX 10xx | Pascal | `-Torch cu118` |

Без NVIDIA всё тоже запустится (CUDA-код откатывается на CPU), но в 10–20 раз
медленнее: минутный звонок будет обрабатываться несколько минут.

## Установка

```powershell
git clone https://github.com/vov4ig/call-analyzer.git
cd call-analyzer
powershell -NoProfile -ExecutionPolicy Bypass -File install\install.ps1
```

Скрипт проходит шесть шагов и пишет лог в `C:\cc\logs\install.log`:

0. **Бинарники** — PortableGit, Miniconda, FFmpeg в `C:\tools`, PATH и переменные окружения для текущего пользователя.
1. **Окружение** — conda env `wx` (Python 3.11), PyTorch cu128, whisperX-форк, точные пины из `requirements.txt`.
2. **Совместимость** — `sitecustomize.py`, возвращающий `torch.load(weights_only=False)` для старых чекпоинтов pyannote.
3. **Модели** — 8.9 ГБ: whisper large-v3, два выравнивателя, pyannote, nltk.
4. **Ollama** — установка, отдельный каталог моделей, `qwen2.5:7b`.
5. **Smoke-тест** — синтетический двухголосый звонок через весь пайплайн.
6. **Самопроверка** — `verify.ps1`.

Другие каталоги и старая видеокарта:

```powershell
powershell ... -File install\install.ps1 -Home D:\calls -Tools D:\tools -Torch cu121
```

### Токен Hugging Face (обязательно для pyannote)

Две модели диаризации лежат на Hugging Face за подтверждением условий:

1. Войдите в аккаунт и примите условия на страницах
   [pyannote/segmentation-3.0](https://huggingface.co/pyannote/segmentation-3.0) и
   [pyannote/wespeaker-voxceleb-resnet34-LM](https://huggingface.co/pyannote/wespeaker-voxceleb-resnet34-LM).
2. Создайте read-токен: <https://huggingface.co/settings/tokens>.
3. Передайте его установщику:

```powershell
$env:HF_TOKEN = "hf_xxx"
powershell ... -File install\install.ps1
# или
powershell ... -File install\install.ps1 -HfToken hf_xxx
```

Токен нужен только при скачивании. В рантайме модели читаются с диска, сеть не
используется (`HF_HUB_OFFLINE=1`).

Если сеть не пускает на gated-эндпоинт, есть `-Mirror` — те же файлы из
публичных зеркал, с проверкой SHA-256. Юридически это не равно принятию условий,
подробности в [THIRD_PARTY.md](THIRD_PARTY.md).

## Запуск

Веб-интерфейс:

```bat
web.bat
```

Откроется <http://127.0.0.1:8080>. Перетащите файл, выберите язык (ru/en/auto),
при необходимости укажите число говорящих — через 1–2 минуты появится отчёт,
диалог с таймкодами и четыре файла для скачивания.

Из командной строки:

```bat
transcribe.bat "D:\calls\call_001.mp3" --lang ru --num-speakers 2
analyze.bat "D:\calls\call_001.json"
```

Обе программы можно вызывать напрямую из окружения `wx`, если удобнее:

```powershell
C:\tools\miniconda3\envs\wx\python.exe run_call.py call.mp3 --lang ru
C:\tools\miniconda3\envs\wx\python.exe analyze_call.py call.json --out report.md
```

### Доступ из локальной сети

```bat
web-lan.bat
```

Сервер слушает `0.0.0.0:8080`, в консоли печатаются адреса вида
`http://192.168.1.233:8080/`. В локальной сети без пароля может зайти любой —
включите токен:

```bat
set CALL_ANALYZER_TOKEN=мой-секрет
web-lan.bat
```

Первый вход — по ссылке `http://192.168.1.233:8080/?token=мой-секрет`, дальше
токен живёт в cookie. Тот же токен принимается в заголовке `X-Token`.

Windows спросит про доступ к сети при первом запуске; если окно пропустили,
правило можно добавить вручную (без прав администратора оно не всегда
применяется — тогда разрешите `python.exe` через «Брандмауэр Защитника Windows →
Разрешить приложение»):

```powershell
netsh advfirewall firewall add rule name="call-analyzer 8080" dir=in action=allow protocol=TCP localport=8080
```

## Docker (Linux / NAS)

Контейнер — дополнительный путь, нативная установка остаётся основной. Нужны
драйвер NVIDIA и [NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html).

```bash
git clone https://github.com/vov4ig/call-analyzer.git
cd call-analyzer
docker compose up -d --build
docker compose exec app python install/stage2_models.py --home /data
```

- `app` — пайплайн и веб-интерфейс на <http://localhost:8080>
- `ollama` — официальный образ, своя GPU и свой том
- модели и задания — в томе `call-analyzer_data`, в образ не попадают
- на Windows это работает только через WSL2 (установка WSL требует прав администратора)

Подробности: [docker/README.md](docker/README.md). Путь проверен статически, но
на машине автора Docker не установлен — если что-то не заведётся, issue
приветствуется.

## Настройки

| Переменная | По умолчанию | Что делает |
|---|---|---|
| `CALL_ANALYZER_HOME` | `C:\cc` | данные: модели, задания, логи, временные файлы |
| `CALL_ANALYZER_PY` | автопоиск | путь к `python.exe` окружения `wx` (для `.bat`) |
| `CALL_ANALYZER_MODEL` | `qwen2.5:7b` | модель Ollama для отчётов |
| `CALL_ANALYZER_TOKEN` | нет | включает проверку токена в веб-интерфейсе |
| `FFMPEG` | автопоиск | путь к `ffmpeg.exe` или его каталогу |
| `OLLAMA_HOST` | `http://127.0.0.1:11434` | адрес Ollama (можно указать другую машину) |
| `OLLAMA_MODELS` | `<home>\models\ollama` | каталог моделей Ollama |

Полный список — в [cc_paths.py](cc_paths.py), там же поиск ffmpeg и офлайн-флаги.

## Производительность

Замеры на RTX 5060 Laptop 8 ГБ, 26-секундный тестовый звонок:

| Этап | Время |
|---|---|
| ASR + выравнивание + диаризация | ~17 с (включая ~8 с загрузки моделей) |
| Отчёт qwen2.5:7b | ~9 с |
| Итого через веб-интерфейс | ~25 с |

Ориентир для реальных записей: в 2–4 раза быстрее реального времени, часовой
звонок — 15–30 минут. Диаризация добавляет примерно 5–10 % к времени ASR.

Ускорить: `--batch-size` побольше (если хватает VRAM), `--no-align` для
черновой расшифровки, модель `qwen2.5:3b` для отчётов на слабой видеокарте.

## Как это работает

- **Офлайн по умолчанию.** `cc_paths.configure_offline_env()` выставляет
  `HF_HUB_OFFLINE`, `TRANSFORMERS_OFFLINE`, `HF_HOME`, `TORCH_HOME`, `NLTK_DATA`
  до импорта тяжёлых библиотек. Ни один этап не ходит в сеть.
- **Модели в простом layout.** Установщик раскладывает файлы в
  `models--org--name/snapshots/<sha>`, поэтому `local_files_only=True` работает
  без метаданных `huggingface_hub`.
- **Конфиг pyannote генерируется.** `models/pyannote/config.yaml` собирается
  установщиком с абсолютными путями — в репозитории его нет, потому что он
  машинно-зависимый.
- **`sitecustomize.py`.** PyTorch ≥ 2.6 загружает чекпоинты с
  `weights_only=True`, и pyannote 3.3 падает на `omegaconf`-объектах. Шим
  возвращает старое поведение; все чекпоинты локальные и проверены по SHA-256.
- **Последовательная очередь.** Один воркер, одна GPU: параллельные задачи
  просто подерутся за VRAM.

## Структура

```
call-analyzer/
├─ webapp.py            # веб-интерфейс + очередь задач (stdlib)
├─ run_call.py          # ASR → выравнивание → диаризация → json/txt/srt
├─ analyze_call.py      # расшифровка → отчёт через Ollama
├─ cc_paths.py          # все пути и переменные окружения
├─ verify.ps1           # самопроверка (-Full: реальный звонок)
├─ requirements.txt     # точные пины (112 пакетов + torch cu128)
├─ install/
│  ├─ install.ps1       # оркестратор
│  ├─ stage0.ps1        # Git, Miniconda, FFmpeg
│  ├─ stage1.ps1        # env wx, torch, whisperX, зависимости
│  ├─ stage2_models.py  # скачивание моделей (токен / зеркало)
│  ├─ stage2_test.py    # прогон всех моделей
│  ├─ stage3.ps1        # Ollama + qwen2.5:7b
│  └─ make_test_audio.ps1
├─ shim/sitecustomize.py
├─ docker/              # Dockerfile + compose (Linux/NVIDIA)
└─ tests/               # unittest без GPU и сети
```

## Диагностика

Первый шаг всегда один:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File verify.ps1
```

| Симптом | Причина | Решение |
|---|---|---|
| `CUDA not available`, `sm_120 is not supported` | сборка PyTorch не для этой карты | переустановить: `stage1.ps1 -Torch cu128` (для GTX 10xx — `cu118`) |
| `UnpicklingError`, `WeightsUnpickler`, падение в pyannote | нет шима `torch.load` | скопировать `shim/sitecustomize.py` в `site-packages` (это делает `install.ps1`) |
| `ffmpeg not found` | FFmpeg не в PATH | задать `FFMPEG=C:\tools\ffmpeg\bin\ffmpeg.exe` |
| `Ollama is not reachable` | сервер не запущен | `"%LOCALAPPDATA%\Programs\Ollama\ollama.exe" serve` с `OLLAMA_MODELS` |
| `CUDA out of memory` | мало VRAM под batch | `--batch-size 4`, закрыть браузер/игры, для длинных звонков резать файл |
| Модели «не найдены», хотя скачаны | другой `CALL_ANALYZER_HOME` | запускать из того же окружения/пользователя |
| Кириллица в пути к данным | ограничение некоторых сборок | ставить в `C:\cc` и `C:\tools` |
| Диаризация путает голоса | похожие голоса, шум, перекрытия | `--num-speakers 2`, чистый моно-звук 16 кГц, без музыки |
| `running scripts is disabled on this system` | ExecutionPolicy | запускать с `-ExecutionPolicy Bypass` |
| Порт 8080 занят | другая программа | `webapp.py --port 8090` |

Логи: `C:\cc\logs\install.log`, `stage0-3.log`, `webapp.log`, `ollama.*.log`.

## Ограничения

- **Это помощник, а не замена прослушиванию.** LLM может уверенно придумать
  деталь; отчёт всегда стоит сверять с расшифровкой.
- Аудио обрабатывается целиком в память модели: очень длинные файлы (2+ часа)
  лучше резать по 20–30 минут.
- Один язык на файл. Смешанная речь (ru + en в одном звонке) распознаётся хуже.
- Диаризация ошибается на перекрывающейся речи и одинаковых голосах; количество
  говорящих лучше указывать явно.
- Модели и их лицензии — не часть этого репозитория. Перед коммерческим
  использованием прочитайте [THIRD_PARTY.md](THIRD_PARTY.md).
- Записи звонков и отчёты — персональные данные. Они остаются на вашей машине,
  но ответственность за их хранение и обработку лежит на вас.

## Разработка

```powershell
C:\tools\miniconda3\envs\wx\python.exe -m unittest discover -s tests -v
```

Тесты не требуют GPU, моделей и сети: проверяются markdown-рендерер, разбор
multipart, имена файлов, склейка реплик и логика путей. CI гоняет их на Windows
и Ubuntu при каждом push.

Обновление зависимостей: поставьте нужные версии в окружении `wx`, затем
`pip freeze > requirements.txt` и поправьте маркеры `sys_platform` для
Windows-only пакетов. Перевод на другую модель Ollama — `--model` или
`CALL_ANALYZER_MODEL`.

## Лицензия

Код — [MIT](LICENSE). Модели и сторонние компоненты — под своими лицензиями,
см. [THIRD_PARTY.md](THIRD_PARTY.md). English README: [README.en.md](README.en.md).
