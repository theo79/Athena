"""External configuration and persistent paths, independent of frozen resources."""
import os
import shutil
import sys
from pathlib import Path
from tempfile import NamedTemporaryFile

from dotenv import load_dotenv


def is_packaged():
    return bool(getattr(sys, "frozen", False))


def application_dir():
    return Path(sys.executable).resolve().parent if is_packaged() else Path(__file__).resolve().parent


def config_path():
    return application_dir() / ".env"


def data_dir():
    if not is_packaged():
        return application_dir()
    return user_config_dir()


def user_config_dir():
    local = os.environ.get("LOCALAPPDATA", "")
    root = Path(local) if local and Path(local).is_absolute() else Path.home() / "AppData" / "Local"
    return root / "Athena"


def memory_path():
    return data_dir() / "memory.json"


def experience_path():
    return data_dir() / "experiences.json"


def load_configuration():
    # Explicit lookup never searches the current directory or extraction directory.
    from settings import load_preferences
    # Explicit process environment > saved preferences > legacy adjacent .env.
    # Keep provider and model paired if the process chooses a different provider.
    preferences = load_preferences()
    if os.getenv("MODEL_PROVIDER", preferences.get("MODEL_PROVIDER")) != preferences.get("MODEL_PROVIDER"):
        preferences.pop("MODEL_NAME", None)
    for name, value in preferences.items():
        os.environ.setdefault(name, value)
    load_dotenv(user_config_dir() / ".env", override=False)
    load_dotenv(config_path(), override=False)


def _copy_legacy(source, destination):
    temporary = None
    try:
        with NamedTemporaryFile(dir=destination.parent, prefix=destination.name + ".",
                                suffix=".tmp", delete=False) as output:
            temporary = Path(output.name)
            with source.open("rb") as original:
                shutil.copyfileobj(original, output)
            output.flush()
            os.fsync(output.fileno())
        # Publish only complete bytes; neither operation replaces an existing file.
        if os.name == "nt":
            temporary.rename(destination)
        else:
            os.link(temporary, destination)
    except FileExistsError:
        pass
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def prepare_data_dir():
    """Copy adjacent legacy stores once; never replace new stores or delete originals."""
    target = data_dir()
    target.mkdir(parents=True, exist_ok=True)
    if is_packaged():
        for name in ("memory.json", "experiences.json"):
            if (target / name).exists():
                continue
            # Preserve recovery backups as well as primary files, including corrupt bytes.
            for suffix in (".bak", ""):
                source = application_dir() / (name + suffix)
                destination = target / source.name
                if source.is_file() and not destination.exists():
                    _copy_legacy(source, destination)
    # Check writability without creating an empty memory/experience record.
    with NamedTemporaryFile(dir=target, prefix=".athena-write-check-"):
        pass
    return target


def packaged_startup_error():
    """Return fixed, secret-free diagnostics before the CLI loop starts."""
    if not is_packaged():
        return None
    try:
        load_configuration()
        prepare_data_dir()
    except (OSError, UnicodeError):
        return "Athena could not read configuration or prepare its user data directory. Check file permissions."
    return None
