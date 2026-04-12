# Python Downloader

Небольшой CLI-скрипт для установки медиа из Spotify или YouTube.
Spotify-ссылки (`track`, `album`, `playlist`) ищутся через `yt-dlp`.

## Возможности

- скачивать медиа со Spotify или YouTube
- после каждой аудиозагрузки ручное редактирование метаданных

## Установка

Отдельно нужно установить:

- `ffmpeg`

Зависимости:

```bash
pip install spotipy yt-dlp yt-dlp-ejs mutagen python-dotenv secretstorage pillow
```

Для Spotify нужно создать приложение на https://developer.spotify.com/dashboard и
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
