import io
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path

from PIL import Image, ImageOps
from mutagen.flac import FLAC, Picture
from mutagen.id3 import APIC, ID3, ID3NoHeaderError, TALB, TDRC, TIT2, TPE1, TRCK
from mutagen.mp4 import MP4, MP4Cover

from app_config import ask_text

AUDIO_FORMATS = ("mp3", "flac", "ogg", "opus", "m4a")
VIDEO_FORMATS = ("mp4", "webm", "mkv")
BITRATES = ("128k", "192k", "256k", "320k")
AUDIO_SUFFIXES = {f".{fmt}" for fmt in AUDIO_FORMATS}
YT_DLP_JS_RUNTIME = "node" if shutil.which("node") else ""


def sanitize_filename(name):
    cleaned = re.sub(r'[\\/*?:"<>|]', "", name)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" .")
    return cleaned or "download"


def shorten_error(message, tail_length=240):
    compact = " ".join(message.split())
    if len(compact) <= tail_length:
        return compact
    edge = max(40, tail_length // 2 - 3)
    return f"{compact[:edge]} ... {compact[-edge:]}"


def fetch_bytes(url):
    try:
        with urllib.request.urlopen(url, timeout=10) as response:
            return response.read()
    except Exception:
        return None


def read_local_bytes(path):
    try:
        return Path(path).expanduser().read_bytes()
    except Exception:
        return None


def make_square_cover(raw_bytes):
    if not raw_bytes:
        return None

    image = Image.open(io.BytesIO(raw_bytes)).convert("RGB")
    square = ImageOps.fit(image, (1000, 1000), method=Image.Resampling.LANCZOS, centering=(0.5, 0.5))
    buffer = io.BytesIO()
    square.save(buffer, format="JPEG", quality=95)
    return buffer.getvalue()


def get_cover_bytes(meta):
    cover_path = meta.get("cover_path", "").strip()
    if cover_path:
        return make_square_cover(read_local_bytes(cover_path))

    cover_url = meta.get("cover_url", "").strip()
    if cover_url:
        return make_square_cover(fetch_bytes(cover_url))

    return None


def tag_mp3(path, meta, cover):
    try:
        tags = ID3(path)
    except ID3NoHeaderError:
        tags = ID3()

    for frame_id in ("TIT2", "TPE1", "TALB", "TDRC", "TRCK", "APIC"):
        tags.delall(frame_id)

    tags.add(TIT2(encoding=3, text=meta.get("title", "")))
    tags.add(TPE1(encoding=3, text=meta.get("artists", "")))
    tags.add(TALB(encoding=3, text=meta.get("album", "")))
    if meta.get("year"):
        tags.add(TDRC(encoding=3, text=meta["year"]))
    if meta.get("track_number"):
        tags.add(TRCK(encoding=3, text=str(meta["track_number"])))
    if cover:
        tags.add(APIC(encoding=3, mime="image/jpeg", type=3, desc="Cover", data=cover))

    tags.save(path)


def tag_flac(path, meta, cover):
    tags = FLAC(path)
    for key in ("title", "artist", "album", "date", "tracknumber"):
        if key in tags:
            del tags[key]

    tags["title"] = meta.get("title", "")
    tags["artist"] = meta.get("artists", "")
    tags["album"] = meta.get("album", "")
    if meta.get("year"):
        tags["date"] = meta["year"]
    if meta.get("track_number"):
        tags["tracknumber"] = str(meta["track_number"])

    tags.clear_pictures()
    if cover:
        picture = Picture()
        picture.type = 3
        picture.mime = "image/jpeg"
        picture.data = cover
        tags.add_picture(picture)

    tags.save()


def tag_m4a(path, meta, cover):
    tags = MP4(path)
    for key in ("\xa9nam", "\xa9ART", "\xa9alb", "\xa9day", "trkn", "covr"):
        if key in tags:
            del tags[key]

    tags["\xa9nam"] = [meta.get("title", "")]
    tags["\xa9ART"] = [meta.get("artists", "")]
    tags["\xa9alb"] = [meta.get("album", "")]
    if meta.get("year"):
        tags["\xa9day"] = [meta["year"]]
    if meta.get("track_number"):
        tags["trkn"] = [(int(meta["track_number"]), 0)]
    if cover:
        tags["covr"] = [MP4Cover(cover, imageformat=MP4Cover.FORMAT_JPEG)]

    tags.save()


def tag_audio_file(path, meta):
    cover = get_cover_bytes(meta)
    suffix = path.suffix.lower()

    if suffix == ".mp3":
        tag_mp3(path, meta, cover)
    elif suffix == ".flac":
        tag_flac(path, meta, cover)
    elif suffix == ".m4a":
        tag_m4a(path, meta, cover)


def prompt_manual_metadata(meta):
    edited = dict(meta)
    edited["title"] = ask_text("Title", edited.get("title", ""))
    edited["artists"] = ask_text("Artist", edited.get("artists", ""))
    edited["album"] = ask_text("Album", edited.get("album", ""))
    edited["year"] = ask_text("Year", edited.get("year", ""))
    edited["track_number"] = ask_text("Track number", str(edited.get("track_number", ""))).strip()
    edited["cover_path"] = ask_text("Cover image path (empty = use default/remote)", "")

    track_number = edited.get("track_number", "").strip()
    edited["track_number"] = int(track_number) if track_number.isdigit() else 0
    return edited


def finalize_audio_file(path, meta, manual_metadata):
    final_meta = dict(meta)
    if manual_metadata:
        print(f"\nEdit metadata for {path.name}")
        final_meta = prompt_manual_metadata(final_meta)
    tag_audio_file(path, final_meta)


def add_common_ytdlp_args(command, print_path=True):
    if print_path:
        command.extend(["--print", "after_move:filepath"])
    if YT_DLP_JS_RUNTIME:
        command.extend(["--js-runtimes", YT_DLP_JS_RUNTIME])
    return command


def build_audio_command(target, output_template, fmt, bitrate):
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
    return add_common_ytdlp_args(command)


def build_video_command(url, output_template, fmt):
    command = [
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
    return add_common_ytdlp_args(command)


def build_info_command(url):
    command = [
        sys.executable,
        "-m",
        "yt_dlp",
        url,
        "--skip-download",
        "--print",
        "title",
        "--print",
        "thumbnail",
    ]
    return add_common_ytdlp_args(command, print_path=False)


def build_search_command(query):
    command = [
        sys.executable,
        "-m",
        "yt_dlp",
        f"ytsearch8:{query}",
        "--dump-single-json",
    ]
    return add_common_ytdlp_args(command, print_path=False)


def run_command(command):
    return subprocess.run(command, capture_output=True, text=True)


def needs_cookies(stderr):
    error_text = stderr.lower()
    patterns = (
        "cookies",
        "sign in to confirm",
        "confirm you’re not a bot",
        "confirm you're not a bot",
        "use --cookies-from-browser",
    )
    return any(pattern in error_text for pattern in patterns)


def get_browser_candidates():
    system_name = platform.system().lower()
    home = Path.home()
    local_app_data = os.getenv("LOCALAPPDATA", "")
    app_data = os.getenv("APPDATA", "")

    firefox_paths = [
        home / ".mozilla/firefox",
        home / ".var/app/org.mozilla.firefox/.mozilla/firefox",
        home / "Library/Application Support/Firefox/Profiles",
    ]
    chrome_paths = [
        home / ".config/google-chrome",
        home / "Library/Application Support/Google/Chrome",
        Path(local_app_data) / "Google/Chrome/User Data" if local_app_data else None,
    ]
    chromium_paths = [
        home / ".config/chromium",
        home / "Library/Application Support/Chromium",
        Path(local_app_data) / "Chromium/User Data" if local_app_data else None,
    ]
    edge_paths = [
        home / "Library/Application Support/Microsoft Edge",
        Path(local_app_data) / "Microsoft/Edge/User Data" if local_app_data else None,
    ]
    safari_paths = [home / "Library/Safari"]
    firefox_paths.append(Path(app_data) / "Mozilla/Firefox/Profiles" if app_data else None)

    def exists(paths):
        return any(path and path.exists() for path in paths)

    browsers = []

    if system_name == "linux":
        if exists(chrome_paths):
            browsers.extend(["chrome+gnomekeyring", "chrome"])
        if exists(chromium_paths):
            browsers.extend(["chromium+gnomekeyring", "chromium"])
        if exists(firefox_paths):
            browsers.append("firefox")
    elif system_name == "darwin":
        if exists(chrome_paths):
            browsers.append("chrome")
        if exists(edge_paths):
            browsers.append("edge")
        if exists(chromium_paths):
            browsers.append("chromium")
        if exists(firefox_paths):
            browsers.append("firefox")
        if exists(safari_paths):
            browsers.append("safari")
    else:
        if exists(chrome_paths):
            browsers.append("chrome")
        if exists(edge_paths):
            browsers.append("edge")
        if exists(chromium_paths):
            browsers.append("chromium")
        if exists(firefox_paths):
            browsers.append("firefox")

    deduped = []
    for browser in browsers:
        if browser not in deduped:
            deduped.append(browser)
    return deduped


def run_with_cookie_fallback(command):
    result = run_command(command)
    if result.returncode == 0 or not needs_cookies(result.stderr):
        return result, ""

    for browser in get_browser_candidates():
        retry_command = [*command, "--cookies-from-browser", browser]
        retry_result = run_command(retry_command)
        if retry_result.returncode == 0:
            return retry_result, browser
        result = retry_result

    return result, ""


def extract_path_from_stdout(stdout):
    for line in reversed([line.strip() for line in stdout.splitlines() if line.strip()]):
        path = Path(line).expanduser()
        if path.exists():
            return path
    return None


def find_downloaded_audio(output_dir, stem):
    candidates = sorted(
        path for path in output_dir.glob(f"{stem}.*") if path.suffix.lower() in AUDIO_SUFFIXES
    )
    return candidates[0] if candidates else None


def get_youtube_meta(url):
    result, used_cookies = run_with_cookie_fallback(build_info_command(url))
    lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]

    meta = {
        "title": lines[0] if lines else "",
        "artists": "",
        "album": "",
        "year": "",
        "track_number": 0,
        "cover_url": lines[1] if len(lines) > 1 else "",
        "cover_path": "",
    }
    return meta, used_cookies, result


def normalize_text(text):
    return re.sub(r"[^a-z0-9]+", " ", str(text).lower()).strip()


def contains_any(text, terms):
    return any(term in text for term in terms)


def is_hard_negative_candidate(entry):
    combined = normalize_text(
        f"{entry.get('title', '')} {entry.get('uploader', '')} {entry.get('channel', '')}"
    )
    hard_negative_terms = (
        "live",
        "concert",
        "performance",
        "acoustic",
        "session",
        "clip",
        "official video",
        "music video",
        "video oficial",
        "visualizer",
        "recorded live",
        "on tour",
    )
    return contains_any(combined, hard_negative_terms)


def score_search_candidate(entry, meta):
    title = normalize_text(entry.get("title", ""))
    uploader = normalize_text(entry.get("uploader", ""))
    channel = normalize_text(entry.get("channel", ""))
    album = normalize_text(meta.get("album", ""))
    artist = normalize_text(meta.get("artists", ""))
    song = normalize_text(meta.get("title", ""))

    score = 0

    if song and song in title:
        score += 45
    if artist and (artist in title or artist in uploader or artist in channel):
        score += 25
    if album and album in title:
        score += 10

    target_duration = int(meta.get("duration_sec") or 0)
    candidate_duration = int(entry.get("duration") or 0)
    if target_duration and candidate_duration:
        diff = abs(target_duration - candidate_duration)
        if diff <= 3:
            score += 30
        elif diff <= 8:
            score += 20
        elif diff <= 15:
            score += 10
        elif diff >= 45:
            score -= 30

    positive_terms = (
        "official audio",
        "audio",
        "topic",
        "provided to youtube",
        "auto generated",
        "audio only",
    )
    soft_negative_terms = (
        "lyrics",
        "lyric video",
        "remix",
        "sped up",
        "slowed",
        "nightcore",
        "cover",
        "karaoke",
        "fan cam",
    )
    combined = f"{title} {uploader} {channel}".strip()

    for term in positive_terms:
        if term in combined:
            score += 12

    if is_hard_negative_candidate(entry):
        score -= 120

    if contains_any(combined, soft_negative_terms):
        score -= 35

    return score


def find_best_youtube_match(meta):
    query = f"{meta.get('artists', '')} {meta.get('title', '')} {meta.get('album', '')} audio".strip()
    if not query:
        return "", "", ""

    result, used_cookies = run_with_cookie_fallback(build_search_command(query))
    if result.returncode != 0 or not result.stdout.strip():
        return "", used_cookies, ""

    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        return "", used_cookies, ""

    entries = data.get("entries") or []
    if not entries:
        return "", used_cookies, ""

    safe_entries = [entry for entry in entries if not is_hard_negative_candidate(entry)]
    candidate_pool = safe_entries or entries
    scored_entries = sorted(candidate_pool, key=lambda entry: score_search_candidate(entry, meta), reverse=True)
    best_entry = scored_entries[0]
    best_url = best_entry.get("webpage_url") or ""
    if not best_url and best_entry.get("id"):
        best_url = f"https://www.youtube.com/watch?v={best_entry['id']}"

    return best_url, used_cookies, best_entry.get("title", "")
