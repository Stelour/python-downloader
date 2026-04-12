import os
import re
from pathlib import Path

import spotipy
from dotenv import set_key
from spotipy.oauth2 import SpotifyClientCredentials

from app_config import ENV_FILE, ask_choice, ask_text, load_config, print_block, setup_settings
from media_tools import (
    AUDIO_FORMATS,
    BITRATES,
    VIDEO_FORMATS,
    build_audio_command,
    build_video_command,
    extract_path_from_stdout,
    finalize_audio_file,
    find_best_youtube_match,
    find_downloaded_audio,
    get_youtube_meta,
    run_with_cookie_fallback,
    sanitize_filename,
    shorten_error,
)

SPOTIFY_URL_RE = re.compile(r"spotify\.com/(track|album|playlist)/([A-Za-z0-9]+)")


def save_spotify_credentials(client_id, client_secret):
    set_key(str(ENV_FILE), "SPOTIFY_CLIENT_ID", client_id, quote_mode="never")
    set_key(str(ENV_FILE), "SPOTIFY_CLIENT_SECRET", client_secret, quote_mode="never")
    os.environ["SPOTIFY_CLIENT_ID"] = client_id
    os.environ["SPOTIFY_CLIENT_SECRET"] = client_secret


def get_spotify_client():
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


def parse_spotify_url(url):
    match = SPOTIFY_URL_RE.search(url)
    if not match:
        raise ValueError("Could not parse Spotify URL.")
    return match.group(1), match.group(2)


def make_spotify_meta(track, album=None):
    album_data = album or track.get("album", {})
    artists = ", ".join(
        artist["name"]
        for artist in track.get("artists", [])
        if isinstance(artist, dict) and artist.get("name")
    )
    images = album_data.get("images") or []

    return {
        "title": track.get("name", "Unknown title"),
        "artists": artists or "Unknown artist",
        "album": album_data.get("name", ""),
        "year": (album_data.get("release_date") or "")[:4],
        "track_number": int(track.get("track_number") or 0),
        "duration_sec": int((track.get("duration_ms") or 0) / 1000),
        "cover_url": images[0]["url"] if images else "",
        "cover_path": "",
    }


def iter_paginated_items(sp, results):
    while True:
        yield from results.get("items", [])
        if not results.get("next"):
            return
        results = sp.next(results)


def get_spotify_tracks(sp, kind, spotify_id):
    if kind == "track":
        return [make_spotify_meta(sp.track(spotify_id))]

    if kind == "album":
        album = sp.album(spotify_id)
        return [make_spotify_meta(track, album) for track in iter_paginated_items(sp, sp.album_tracks(spotify_id))]

    if kind == "playlist":
        tracks = []
        for item in iter_paginated_items(sp, sp.playlist_tracks(spotify_id)):
            track = item.get("track")
            if isinstance(track, dict) and track.get("type") == "track":
                tracks.append(make_spotify_meta(track))
        return tracks

    raise ValueError(f"Unsupported Spotify URL type: {kind}")


def download_spotify(sp, output_dir, fmt, bitrate, manual_metadata):
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
    tracks = get_spotify_tracks(sp, kind, spotify_id)
    total = len(tracks)
    print(f"Found {total} track(s)\n")

    ok = 0
    failed = 0
    failed_items = []

    for index, meta in enumerate(tracks, start=1):
        label = f"{meta['artists']} - {meta['title']}"
        print(f"[{index}/{total}] {label}")
        safe_name = sanitize_filename(label)
        search_target, search_cookies, match_title = find_best_youtube_match(meta)
        if search_cookies:
            print(f"  Search used cookies: {search_cookies}")
        if match_title:
            print(f"  Match: {match_title}")
        command = build_audio_command(
            target=search_target or f"ytsearch1:{label}",
            output_template=output_dir / f"{safe_name}.%(ext)s",
            fmt=fmt,
            bitrate=bitrate,
        )
        command.append("--no-playlist")

        result, used_cookies = run_with_cookie_fallback(command)
        if used_cookies:
            print(f"  Retried with cookies: {used_cookies}")

        if result.returncode != 0:
            reason = f"yt-dlp error: {shorten_error(result.stderr)}"
            print(f"  {reason}")
            failed += 1
            failed_items.append((label, reason))
            continue

        path = extract_path_from_stdout(result.stdout) or find_downloaded_audio(output_dir, safe_name)
        if path is None:
            reason = "download finished, but the output file was not found"
            print(f"  {reason.capitalize()}.")
            failed += 1
            failed_items.append((label, reason))
            continue

        try:
            finalize_audio_file(path, meta, manual_metadata)
        except Exception as error:
            reason = f"tagging failed: {error}"
            print(f"  Saved as {path.name}, but {reason}")
            failed += 1
            failed_items.append((label, reason))
            continue

        print(f"  Saved as {path.name}")
        ok += 1

    print(f"\nDone: {ok} succeeded, {failed} failed out of {total}")
    if failed_items:
        print("\nFailed tracks:")
        for label, reason in failed_items:
            print(f"  - {label}: {reason}")


def download_youtube(output_dir, fmt, bitrate, manual_metadata):
    url = input("\nYouTube URL:\n> ").strip()
    output_template = output_dir / "%(title)s.%(ext)s"

    if fmt in AUDIO_FORMATS:
        meta, meta_cookies, meta_result = get_youtube_meta(url)
        if meta_cookies:
            print(f"Metadata used cookies: {meta_cookies}")
        if meta_result.returncode != 0:
            meta = {
                "title": "",
                "artists": "",
                "album": "",
                "year": "",
                "track_number": 0,
                "cover_url": "",
                "cover_path": "",
            }

        command = build_audio_command(url, output_template, fmt, bitrate)
        result, used_cookies = run_with_cookie_fallback(command)
        if used_cookies:
            print(f"Retried with cookies: {used_cookies}")
        if result.returncode != 0:
            print(f"Error: {shorten_error(result.stderr, 320)}")
            return

        path = extract_path_from_stdout(result.stdout)
        if path is None:
            print("Error: download finished, but the output file was not found.")
            return

        if not meta.get("title"):
            meta["title"] = path.stem

        try:
            finalize_audio_file(path, meta, manual_metadata)
        except Exception as error:
            print(f"Done, but tagging failed: {error}")
            return

        print(f"Done. File saved as {path.name}")
        return

    command = build_video_command(url, output_template, fmt)
    result, used_cookies = run_with_cookie_fallback(command)
    if used_cookies:
        print(f"Retried with cookies: {used_cookies}")
    if result.returncode != 0:
        print(f"Error: {shorten_error(result.stderr, 320)}")
        return

    path = extract_path_from_stdout(result.stdout)
    if path:
        print(f"Done. File saved as {path.name}")
    else:
        print(f"Done. Files were saved to {output_dir}")


def main():
    config = load_config()
    output_dir, manual_metadata = setup_settings(config)

    while True:
        print_block("Downloader")
        print(f"Output directory: {output_dir}")
        print(f"Manual metadata after audio: {'on' if manual_metadata else 'off'}")
        print("1. Spotify (track / album / playlist)")
        print("2. YouTube (audio or video)")
        print("3. Settings")
        print("0. Exit")

        choice = input("> ").strip()

        if choice == "1":
            spotify = get_spotify_client()
            fmt = ask_choice("Format", AUDIO_FORMATS, "mp3")
            bitrate = ask_choice("Bitrate", BITRATES, "320k") if fmt == "mp3" else ""
            download_spotify(spotify, output_dir, fmt, bitrate, manual_metadata)
            continue

        if choice == "2":
            fmt = ask_choice("Format", AUDIO_FORMATS + VIDEO_FORMATS, "mp3")
            bitrate = ask_choice("Bitrate", BITRATES, "320k") if fmt == "mp3" else ""
            download_youtube(output_dir, fmt, bitrate, manual_metadata)
            continue

        if choice == "3":
            output_dir, manual_metadata = setup_settings(config)
            continue

        if choice == "0":
            print("Bye.")
            break

        print("Invalid choice.")


if __name__ == "__main__":
    main()
