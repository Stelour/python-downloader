# Python Downloader

Небольшой CLI-скрипт для загрузки музыки по ссылкам Spotify
(`track`, `album`, `playlist`) через `yt-dlp`, а также для скачивания
аудио и видео с YouTube.

## Установка

```bash
pip install spotipy yt-dlp mutagen python-dotenv
```

На https://developer.spotify.com/dashboard, нужно создать приложение и данные оттуда вставить в `.env`:

```env
SPOTIFY_CLIENT_ID=your_client_id
SPOTIFY_CLIENT_SECRET=your_client_secret
```

Пример уже есть в `env.example`.

Так же нужна предварительная установка утилиты ffmpeg.

## Запуск

```bash
python main.py
```
