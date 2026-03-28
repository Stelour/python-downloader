from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

ENV_FILE = Path(".env")
CONFIG_FILE = Path(".config")
DEFAULT_OUTPUT_DIR = "download"
DEFAULT_MANUAL_METADATA = "no"


def load_config():
    if not CONFIG_FILE.exists():
        return {}

    config = {}
    for raw_line in CONFIG_FILE.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        config[key.strip()] = value.strip()
    return config


def save_config(config):
    lines = [f"{key}={value}" for key, value in sorted(config.items())]
    text = "\n".join(lines)
    if text:
        text += "\n"
    CONFIG_FILE.write_text(text, encoding="utf-8")


def print_block(title):
    line = "=" * 48
    print(f"\n{line}")
    print(f"  {title}")
    print(line)


def ask_text(prompt, default=""):
    suffix = f" [{default}]" if default else ""
    answer = input(f"{prompt}{suffix}: ").strip()
    if answer:
        return answer
    return default


def ask_choice(prompt, options, default=""):
    hint = "/".join(f"[{item}]" if item == default else item for item in options)
    while True:
        answer = input(f"{prompt} ({hint}): ").strip().lower()
        if not answer and default:
            return default
        if answer in options:
            return answer
        print(f"  Choose one of: {', '.join(options)}")


def ask_yes_no(prompt, default=False):
    default_value = "y" if default else "n"
    answer = ask_choice(prompt, ("y", "n"), default_value)
    return answer == "y"


def ensure_output_dir(config):
    output_dir = Path(config.get("output_dir", DEFAULT_OUTPUT_DIR)).expanduser()
    output_dir.mkdir(parents=True, exist_ok=True)
    return output_dir


def manual_metadata_enabled(config):
    value = config.get("manual_metadata", DEFAULT_MANUAL_METADATA).strip().lower()
    return value in ("1", "true", "yes", "y", "on")


def save_settings(config, output_dir, manual_metadata):
    config["output_dir"] = str(output_dir)
    config["manual_metadata"] = "yes" if manual_metadata else "no"
    config.pop("cookies_browser", None)
    save_config(config)


def setup_settings(config):
    output_dir = ensure_output_dir(config)
    manual_metadata = manual_metadata_enabled(config)

    print_block("Downloader Setup")
    print(f"Current output directory: {output_dir}")
    print(f"Manual metadata after audio download: {'on' if manual_metadata else 'off'}")

    new_dir = input("New output directory (press Enter to keep current): ").strip()
    if new_dir:
        output_dir = Path(new_dir).expanduser()
        output_dir.mkdir(parents=True, exist_ok=True)

    manual_metadata = ask_yes_no("Edit metadata after each audio download", manual_metadata)
    save_settings(config, output_dir, manual_metadata)
    print(f"Saved output directory: {output_dir}")
    print(f"Manual metadata: {'on' if manual_metadata else 'off'}")
    return output_dir, manual_metadata
