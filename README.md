# SkyViDub — Система автоматичного дубляжу відео зі збереженням голосу

Локальна програма для автоматичного дубляжу відео на базі `faster-whisper`, `Gemma`, `Qwen3-TTS`, `Demucs` та `FFmpeg`.

## Етапи роботи

```text
Відео MP4 -> 1. Витягання аудіо (FFmpeg, 24 кГц, моно)
          -> 2. Розпізнавання мовлення та об'єднання фраз у речення (faster-whisper small, int8)
          -> 3. Переклад з урахуванням тривалості реплік (Gemma API)
          -> 4. Озвучення зі збереженням голосу диктора або пресетом (Qwen3-TTS-12Hz-1.7B-Base)
          -> 5. Вирівнювання по часу, збереження фонової музики (Demucs) і склейка у MP4 (FFmpeg)
```

| Модуль | Технологія | Призначення | Режим виконання |
|---|---|---|---|
| **ASR** | `faster-whisper small` + VAD | Розпізнавання мовлення з таймкодами та об'єднанням речень | CPU, `int8` |
| **MT** | `Gemma` (`gemma-4-26b-a4b-it`) | Пакетний переклад під ліміт символів та автоскорочення | Хмара (лише текст) |
| **TTS** | `Qwen3-TTS-12Hz-1.7B-Base` | Клонування голосу диктора з перших ~20 с відео або пресету | GPU 8 ГБ, `bfloat16` |
| **Фон** | `Demucs` (опційно) | Відокремлення музики та фонового звуку без старого голосу | GPU / CPU |
| **Зведення** | `FFmpeg` | Підгонка темпу (`atempo <= 1.4x`, `apad`, `adelay`, `amix`) і збірка MP4 | CPU |

## Системні вимоги

- **ОС**: Windows 10/11 (64-bit)
- **Python**: 3.12
- **GPU**: NVIDIA від 8 ГБ VRAM
- **Мультимедіа**: `ffmpeg` у системному `PATH`

## Встановлення та запуск

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install torch torchaudio --index-url https://download.pytorch.org/whl/cu130
.\.venv\Scripts\python -m pip install -U "triton-windows<3.7"
git clone https://github.com/dffdeeq/Qwen3-TTS-streaming.git
.\.venv\Scripts\python -m pip install -e ./Qwen3-TTS-streaming --no-deps
.\.venv\Scripts\python -m pip install -r requirements.txt
copy .env.example .env
.\.venv\Scripts\python dub\gui.py
```

У файлі `.env` потрібно вказати `GOOGLE_API_KEY`.

## Підтримувані мови

- **Мова оригіналу (`src`)**: `auto`, `uk` (українська), `en` (англійська)
- **Мова дубляжу (`dst`)**: `en`, `de`, `fr`, `es`, `it`, `pt`, `zh`, `ja`, `ko`

## Основні опції

- **Голос (`orig` або пресет)**: клонування голосу диктора з перших ~20 с відео або вибір одного з 4 готових голосів із папки `voices/` (3 чоловічі, 1 жіночий).
- **Об'єднання фраз (`--semantic`)**: склеювання коротких уривків у цілі речення за знаками пунктуації.
- **Автоскорочення (`--resynth`)**: повторне скорочення та переозвучення фраз, які не вмістилися у свій часовий слот.
- **Збереження фонової музики (`--music`)**: відділення музики й ефектів від голосу диктора через `Demucs`.
