# Python Downloader

Небольшой CLI-скрипт для установки медиа из Spotify или YouTube.
Spotify-ссылки (`track`, `album`, `playlist`) ищутся через `yt-dlp`.

## Что умеет

- скачивать со Spotify или YouTube
- автоматически подбирать cookies браузера без ручной настройки
- после каждой аудиозагрузки предлагать ручное редактирование метаданных

## Установка

Нужны:

- `ffmpeg`
- `node`

Установи зависимости:

```bash
pip install spotipy yt-dlp yt-dlp-ejs mutagen python-dotenv secretstorage pillow
```

Для Spotify создай приложение на https://developer.spotify.com/dashboard и
заполнить `.env` по примеру из `env.example`:

```env
SPOTIFY_CLIENT_ID=your_client_id
SPOTIFY_CLIENT_SECRET=your_client_secret
```

## Запуск

```bash
python main.py
```

## Использование

1. При старте выбрать папку загрузки.
2. Включить или выключить ручное редактирование метаданных после аудиозагрузки.
3. Выбрать Spotify или YouTube.
4. Вставить ссылку.

Если ручное редактирование включено, после каждой аудиозагрузки можно вручную
задать:

- title
- artist
- album
- year
- track number
- путь до своей обложки
