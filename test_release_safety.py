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
