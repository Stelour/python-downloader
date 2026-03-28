import os
import re
import subprocess
import sys
import urllib.request
from dataclasses import dataclass
from pathlib import Path

import spotipy
from dotenv import load_dotenv, set_key
from mutagen.flac import FLAC, Picture
from mutagen.id3 import APIC, ID3, ID3NoHeaderError, TALB, TDRC, TIT2, TPE1, TRCK
from mutagen.mp4 import MP4, MP4Cover
from spotipy.oauth2 import SpotifyClientCredentials

load_dotenv()

ENV_FILE = Path(".env")
CONFIG_FILE = Path(".config")
DEFAULT_OUTPUT_DIR = "download"

AUDIO_FORMATS = ("mp3", "flac", "ogg", "opus", "m4a")
VIDEO_FORMATS = ("mp4", "webm", "mkv")
BITRATES = ("128k", "192k", "256k", "320k")
AUDIO_SUFFIXES = {f".{fmt}" for fmt in AUDIO_FORMATS}
SPOTIFY_URL_RE = re.compile(
    r"spotify\.com/(?P<kind>track|album|playlist)/(?P<spotify_id>[A-Za-z0-9]+)"
)

Config = dict[str, str]


@dataclass(frozen=True, slots=True)
class TrackMeta:
    title: str
    artists: str
    album: str
    year: str
    cover_url: str | None
    track_number: int

    @property
    def label(self) -> str:
        return f"{self.artists} - {self.title}"


# --- Config ------------------------------------------------------------------

def load_config() -> Config:
    if not CONFIG_FILE.exists():
        return {}

    config: Config = {}
    for raw_line in CONFIG_FILE.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        config[key.strip()] = value.strip()

    return config


def save_config(config: Config) -> None:
    lines = [f"{key}={value}" for key, value in sorted(config.items())]
    content = "\n".join(lines)
    if content:
        content += "\n"
    CONFIG_FILE.write_text(content, encoding="utf-8")


# --- UI ----------------------------------------------------------------------

def print_block(title: str) -> None:
    line = "=" * 48
    print(f"\n{line}")
    print(f"  {title}")
    print(line)


def ask_text(prompt: str, default: str | None = None) -> str:
    suffix = f" [{default}]" if default else ""
    answer = input(f"{prompt}{suffix}: ").strip()
    if answer:
        return answer
    if default is not None:
        return default
    return ""


def ask_choice(prompt: str, options: tuple[str, ...], default: str | None = None) -> str:
    hint = "/".join(f"[{option}]" if option == default else option for option in options)

    while True:
        answer = input(f"{prompt} ({hint}): ").strip()
        if not answer and default is not None:
            return default
        if answer in options:
            return answer
        print(f"  Choose one of: {', '.join(options)}")


def ensure_output_directory(config: Config) -> Path:
    output_dir = Path(config.get("output_dir", DEFAULT_OUTPUT_DIR)).expanduser()
    output_dir.mkdir(parents=True, exist_ok=True)
    return output_dir


def setup_menu(config: Config) -> Path:
    output_dir = ensure_output_directory(config)

    print_block("Downloader Setup")
    print(f"Current output directory: {output_dir}")

    new_dir = input("New output directory (press Enter to keep current): ").strip()
    if not new_dir:
        return output_dir

    output_dir = Path(new_dir).expanduser()
    output_dir.mkdir(parents=True, exist_ok=True)
    config["output_dir"] = str(output_dir)
    save_config(config)
    print(f"Saved output directory: {output_dir}")
    return output_dir


def change_output_directory(config: Config) -> Path:
    current = ensure_output_directory(config)
    new_dir = ask_text("New output directory", default=str(current))
    output_dir = Path(new_dir).expanduser()
    output_dir.mkdir(parents=True, exist_ok=True)
    config["output_dir"] = str(output_dir)
    save_config(config)
    print(f"Saved output directory: {output_dir}")
    return output_dir


# --- Credentials -------------------------------------------------------------

def save_spotify_credentials(client_id: str, client_secret: str) -> None:
    set_key(str(ENV_FILE), "SPOTIFY_CLIENT_ID", client_id, quote_mode="never")
    set_key(str(ENV_FILE), "SPOTIFY_CLIENT_SECRET", client_secret, quote_mode="never")
    os.environ["SPOTIFY_CLIENT_ID"] = client_id
    os.environ["SPOTIFY_CLIENT_SECRET"] = client_secret


def get_spotify_client() -> spotipy.Spotify:
    client_id = os.getenv("SPOTIFY_CLIENT_ID")
    client_secret = os.getenv("SPOTIFY_CLIENT_SECRET")

    if not client_id or not client_secret:
        print("\nSpotify credentials were not found in .env")
        print("Create them at https://developer.spotify.com/dashboard\n")
        client_id = ask_text("Client ID")
        client_secret = ask_text("Client Secret")
        save_spotify_credentials(client_id, client_secret)
        print(f"Saved credentials to {ENV_FILE}")

    auth = SpotifyClientCredentials(client_id=client_id, client_secret=client_secret)
    return spotipy.Spotify(auth_manager=auth)


# --- Spotify -----------------------------------------------------------------

def parse_spotify_url(url: str) -> tuple[str, str]:
    match = SPOTIFY_URL_RE.search(url)
    if not match:
        raise ValueError("Could not parse Spotify URL.")

    return match.group("kind"), match.group("spotify_id")


def make_meta(track: dict, album: dict | None = None) -> TrackMeta:
    album_data = album or track.get("album", {})
    artists = ", ".join(
        artist["name"]
        for artist in track.get("artists", [])
        if isinstance(artist, dict) and artist.get("name")
    )
    images = album_data.get("images") or []

    return TrackMeta(
        title=track.get("name", "Unknown title"),
        artists=artists or "Unknown artist",
        album=album_data.get("name", ""),
        year=(album_data.get("release_date") or "")[:4],
        cover_url=images[0]["url"] if images else None,
        track_number=int(track.get("track_number") or 0),
    )


def iter_paginated_items(sp: spotipy.Spotify, results: dict):
    while True:
        yield from results.get("items", [])
        if not results.get("next"):
            return
        results = sp.next(results)


def get_tracks(sp: spotipy.Spotify, kind: str, spotify_id: str) -> list[TrackMeta]:
    if kind == "track":
        return [make_meta(sp.track(spotify_id))]

    if kind == "album":
        album = sp.album(spotify_id)
        return [make_meta(track, album) for track in iter_paginated_items(sp, sp.album_tracks(spotify_id))]

    if kind == "playlist":
        tracks: list[TrackMeta] = []
        for item in iter_paginated_items(sp, sp.playlist_tracks(spotify_id)):
            track = item.get("track")
            if isinstance(track, dict) and track.get("type") == "track":
                tracks.append(make_meta(track))
        return tracks

    raise ValueError(f"Unsupported Spotify URL type: {kind}")


def fetch_cover(url: str) -> bytes | None:
    try:
        with urllib.request.urlopen(url, timeout=10) as response:
            return response.read()
    except Exception:
        return None


def tag_mp3(path: Path, meta: TrackMeta, cover: bytes | None) -> None:
    try:
        tags = ID3(path)
    except ID3NoHeaderError:
        tags = ID3()

    for frame_id in ("TIT2", "TPE1", "TALB", "TDRC", "TRCK", "APIC"):
        tags.delall(frame_id)

    tags.add(TIT2(encoding=3, text=meta.title))
    tags.add(TPE1(encoding=3, text=meta.artists))
    tags.add(TALB(encoding=3, text=meta.album))
    if meta.year:
        tags.add(TDRC(encoding=3, text=meta.year))
    if meta.track_number:
        tags.add(TRCK(encoding=3, text=str(meta.track_number)))
    if cover:
        tags.add(APIC(encoding=3, mime="image/jpeg", type=3, desc="Cover", data=cover))

    tags.save(path)


def tag_flac(path: Path, meta: TrackMeta, cover: bytes | None) -> None:
    tags = FLAC(path)
    for key in ("title", "artist", "album", "date", "tracknumber"):
        if key in tags:
            del tags[key]

    tags["title"] = meta.title
    tags["artist"] = meta.artists
    tags["album"] = meta.album
    if meta.year:
        tags["date"] = meta.year
    if meta.track_number:
        tags["tracknumber"] = str(meta.track_number)

    tags.clear_pictures()
    if cover:
        picture = Picture()
        picture.type = 3
        picture.mime = "image/jpeg"
        picture.data = cover
        tags.add_picture(picture)

    tags.save()


def tag_m4a(path: Path, meta: TrackMeta, cover: bytes | None) -> None:
    tags = MP4(path)
    for key in ("\xa9nam", "\xa9ART", "\xa9alb", "\xa9day", "trkn", "covr"):
        if key in tags:
            del tags[key]

    tags["\xa9nam"] = [meta.title]
    tags["\xa9ART"] = [meta.artists]
    tags["\xa9alb"] = [meta.album]
    if meta.year:
        tags["\xa9day"] = [meta.year]
    if meta.track_number:
        tags["trkn"] = [(meta.track_number, 0)]
    if cover:
        tags["covr"] = [MP4Cover(cover, imageformat=MP4Cover.FORMAT_JPEG)]

    tags.save()


def tag_file(path: Path, meta: TrackMeta) -> None:
    cover = fetch_cover(meta.cover_url) if meta.cover_url else None
    suffix = path.suffix.lower()

    if suffix == ".mp3":
        tag_mp3(path, meta, cover)
    elif suffix == ".flac":
        tag_flac(path, meta, cover)
    elif suffix == ".m4a":
        tag_m4a(path, meta, cover)


# --- Download ----------------------------------------------------------------

def sanitize_filename(name: str) -> str:
    cleaned = re.sub(r'[\\/*?:"<>|]', "", name)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" .")
    return cleaned or "download"


def build_audio_command(
    target: str,
    output_template: Path,
    fmt: str,
    bitrate: str | None,
) -> list[str]:
    command = [
        sys.executable,
        "-m",
        "yt_dlp",
        target,
        "--extract-audio",
        "--audio-format",
        fmt,
        "--output",
        str(output_template),
    ]
    if fmt == "mp3" and bitrate:
        command.extend(["--audio-quality", bitrate])
    return command


def build_video_command(url: str, output_template: Path, fmt: str) -> list[str]:
    return [
        sys.executable,
        "-m",
        "yt_dlp",
        url,
        "--format",
        f"bestvideo[ext={fmt}]+bestaudio/best",
        "--merge-output-format",
        fmt,
        "--output",
        str(output_template),
    ]


def run_download(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, capture_output=True, text=True)


def shorten_error(message: str, tail_length: int = 240) -> str:
    compact = " ".join(message.split())
    if len(compact) <= tail_length:
        return compact
    return compact[-tail_length:]


def find_downloaded_audio(output_dir: Path, stem: str) -> Path | None:
    candidates = sorted(
        path for path in output_dir.glob(f"{stem}.*") if path.suffix.lower() in AUDIO_SUFFIXES
    )
    return candidates[0] if candidates else None


def download_spotify(
    sp: spotipy.Spotify,
    output_dir: Path,
    fmt: str,
    bitrate: str | None,
) -> None:
    while True:
        url = input("\nSpotify URL (track / album / playlist):\n> ").strip()
        if "spotify.com" in url:
            break
        print("Not a valid Spotify URL, try again.")

    try:
        kind, spotify_id = parse_spotify_url(url)
    except ValueError as error:
        print(f"Error: {error}")
        return

    print("\nFetching track list...")
    tracks = get_tracks(sp, kind, spotify_id)
    total = len(tracks)
    print(f"Found {total} track(s)\n")

    ok = 0
    failed = 0
    failed_items: list[tuple[str, str]] = []

    for index, meta in enumerate(tracks, start=1):
        print(f"[{index}/{total}] {meta.label}")
        safe_name = sanitize_filename(meta.label)
        command = build_audio_command(
            target=f"ytsearch1:{meta.label}",
            output_template=output_dir / f"{safe_name}.%(ext)s",
            fmt=fmt,
            bitrate=bitrate,
        )
        command.append("--no-playlist")

        result = run_download(command)
        if result.returncode != 0:
            reason = f"yt-dlp error: {shorten_error(result.stderr)}"
            print(f"  {reason}")
            failed += 1
            failed_items.append((meta.label, reason))
            continue

        path = find_downloaded_audio(output_dir, safe_name)
        if path is None:
            reason = "download finished, but the output file was not found"
            print(f"  {reason.capitalize()}.")
            failed += 1
            failed_items.append((meta.label, reason))
            continue

        try:
            tag_file(path, meta)
        except Exception as error:
            reason = f"tagging failed: {error}"
            print(f"  Saved as {path.name}, but {reason}")
            failed += 1
            failed_items.append((meta.label, reason))
            continue

        print(f"  Saved as {path.name}")
        ok += 1

    print(f"\nDone: {ok} succeeded, {failed} failed out of {total}")
    if failed_items:
        print("\nFailed tracks:")
        for label, reason in failed_items:
            print(f"  - {label}: {reason}")

def download_youtube(
    output_dir: Path,
    fmt: str,
    bitrate: str | None,
) -> None:
    url = input("\nYouTube URL:\n> ").strip()
    output_template = output_dir / "%(title)s.%(ext)s"

    if fmt in AUDIO_FORMATS:
        command = build_audio_command(url, output_template, fmt, bitrate)
    else:
        command = build_video_command(url, output_template, fmt)

    result = run_download(command)
    if result.returncode != 0:
        print(f"Error: {shorten_error(result.stderr, tail_length=320)}")
        return

    print(f"Done. Files were saved to {output_dir}")


def main() -> None:
    config = load_config()
    output_dir = setup_menu(config)

    while True:
        print_block("Downloader")
        print(f"Output directory: {output_dir}")
        print("1. Spotify (track / album / playlist)")
        print("2. YouTube (audio or video)")
        print("3. Change output directory")
        print("0. Exit")

        choice = input("> ").strip()

        if choice == "1":
            spotify = get_spotify_client()
            fmt = ask_choice("Format", AUDIO_FORMATS, default="mp3")
            bitrate = ask_choice("Bitrate", BITRATES, default="320k") if fmt == "mp3" else None
            download_spotify(spotify, output_dir, fmt, bitrate)
            continue

        if choice == "2":
            fmt = ask_choice("Format", AUDIO_FORMATS + VIDEO_FORMATS, default="mp3")
            bitrate = ask_choice("Bitrate", BITRATES, default="320k") if fmt == "mp3" else None
            download_youtube(output_dir, fmt, bitrate)
            continue

        if choice == "3":
            output_dir = change_output_directory(config)
            continue

        if choice == "0":
            print("Bye.")
            break

        print("Invalid choice.")


if __name__ == "__main__":
    main()
