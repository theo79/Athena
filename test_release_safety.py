import pytest

from package_release import check_content, forbidden_name


@pytest.mark.parametrize("name", [".env", "data/.env", "config.json", "data/config.json", "memory.json", "experiences.json",
                                "memory.json.bak", "data/experiences.json.corrupt-123",
                                "private.quarantine"])
def test_private_files_are_rejected(name):
    assert forbidden_name(name)


def test_template_name_is_allowed():
    assert not forbidden_name(".env.example")


def test_secret_check_does_not_echo_values():
    sentinel = b"synthetic-credential-for-testing"
    with pytest.raises(ValueError) as error:
        check_content(b"before " + sentinel + b" after", [sentinel])
    assert sentinel.decode() not in str(error.value)
    with pytest.raises(ValueError):
        check_content(b"sk-" + b"x" * 32, [])
    check_content(b"OPENROUTER_API_KEY=\n", [])


@pytest.mark.parametrize("name", [
    "athena.db", "nested/athena.sqlite", "athena.sqlite3", "ATHENA.DB-WAL",
    "athena.db-shm", "athena.sqlite3-journal", "athena.db.bak",
    "athena.sqlite3.backup", "athena.sql", "athena.sql.gz", "athena.dump",
    "athena.export.json", "athena.backup.zip",
])
def test_sqlite_artifact_names_are_rejected(name):
    assert forbidden_name(name)


@pytest.mark.parametrize("header", [b"SQLite format 3\x00", b"\x37\x7f\x06\x82",
    b"\x37\x7f\x06\x83", b"\xd9\xd5\x05\xf9\x20\xa1\x63\xd7"])
def test_renamed_sqlite_contents_are_rejected(header):
    with pytest.raises(ValueError, match="SQLite runtime data"):
        check_content(header + b"synthetic data", [])


@pytest.mark.parametrize("name", ["sqlite_store.py", "sqlite3/__init__.py", "_sqlite3.pyd", "sqlite3.dll"])
def test_sqlite_code_is_allowed(name):
    assert not forbidden_name(name)
