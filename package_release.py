"""Assemble an allowlisted release and inspect the executable's decompressed archive."""
import os
from pathlib import Path
import re
import shutil
import zipfile

from dotenv import dotenv_values
import runtime_paths

ROOT = Path(__file__).resolve().parent
ALLOWED = {"Athena.exe", ".env.example", "README.md", "LICENSE"}
KEY_PATTERN = re.compile(rb"\b(?:sk-(?:or-v1-)?[A-Za-z0-9_-]{24,}|AIza[A-Za-z0-9_-]{30,}|tvly-[A-Za-z0-9_-]{20,}|gh[pousr]_[A-Za-z0-9_]{30,})")


def check_content(raw, known_secrets):
    if KEY_PATTERN.search(raw) or any(secret in raw for secret in known_secrets):
        raise ValueError("Potential credential detected; release aborted (values withheld).")


def forbidden_name(name):
    parts = name.replace("\\", "/").lower().split("/")
    return any(part in (".env", "config.json") or part.startswith(("memory.json", "experiences.json"))
               or part.endswith((".bak", ".corrupt", ".quarantine")) for part in parts)


def audit_executable(path, known_secrets):
    from PyInstaller.archive.readers import CArchiveReader
    check_content(path.read_bytes(), known_secrets)
    archive = CArchiveReader(str(path))
    count = 0
    for name in archive.toc:
        if forbidden_name(name):
            raise ValueError("Runtime configuration/data found in executable; release aborted.")
        raw = archive.extract(name)
        if raw:
            check_content(raw, known_secrets)
        count += 1
        if name.endswith(".pyz"):
            pyz = archive.open_embedded_archive(name)
            for module in pyz.toc:
                if module.startswith("test_"):
                    raise ValueError("Project tests found in executable.")
                payload = pyz.extract(module, raw=True)
                if payload:
                    check_content(payload, known_secrets)
                count += 1
    return count


def main():
    # Known values are compared in memory only and never printed or copied.
    values = dict(os.environ)
    if (ROOT / ".env").is_file():
        values.update(dotenv_values(ROOT / ".env"))
    # Include private legacy backups and the new user key store in in-memory
    # comparisons. Never copy these files or reveal their contents.
    private_files = list((ROOT / ".v011-private-config").glob("**/*"))
    private_files.append(runtime_paths.user_config_dir() / ".env")
    private_values = []
    for path in private_files:
        if path.is_file():
            private_values.extend(value for name, value in dotenv_values(path).items()
                                  if re.search(r"API_KEY|TOKEN|SECRET|PASSWORD|CREDENTIAL", name, re.I)
                                  and value and len(value) >= 8)
    secrets = [value.encode() for name, value in values.items()
               if re.search(r"API_KEY|TOKEN|SECRET|PASSWORD|CREDENTIAL", name, re.I)
               and value and len(value) >= 16]
    secrets.extend(value.encode() for value in private_values)
    template = dotenv_values(ROOT / ".env.example")
    if any(value for name, value in template.items()
           if re.search(r"KEY|TOKEN|SECRET|PASSWORD|CREDENTIAL", name, re.I)):
        raise ValueError(".env.example must have empty credential placeholders.")
    executable = ROOT / "dist" / "Athena.exe"
    count = audit_executable(executable, secrets)
    release = ROOT / "dist" / "Athena-release"
    release.mkdir(parents=True, exist_ok=True)
    if any(entry.name not in ALLOWED or not entry.is_file() for entry in release.iterdir()):
        raise ValueError("Release directory contains unexpected files; move them out before rebuilding.")
    for source, name in ((executable, "Athena.exe"), (ROOT / ".env.example", ".env.example"),
                         (ROOT / "WINDOWS_README.md", "README.md"), (ROOT / "LICENSE", "LICENSE")):
        check_content(source.read_bytes(), secrets)
        shutil.copyfile(source, release / name)
    archive_path = ROOT / "dist" / "Athena-v0.12-windows.zip"
    with zipfile.ZipFile(archive_path, "w", zipfile.ZIP_DEFLATED) as archive:
        for name in sorted(ALLOWED):
            archive.write(release / name, "Athena-release/" + name)
    with zipfile.ZipFile(archive_path) as archive:
        if set(archive.namelist()) != {"Athena-release/" + name for name in ALLOWED}:
            raise ValueError("Unexpected ZIP contents; release aborted.")
        for name in archive.namelist():
            check_content(archive.read(name), secrets)
    print(f"Release verified: {count} archive entries scanned; no likely credentials or runtime stores found.")
    print(f"Executable: {release / 'Athena.exe'}")
    print(f"ZIP: {archive_path}")


if __name__ == "__main__":
    main()
