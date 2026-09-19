"""Offline integration checks of the real frozen executable; no real keys used."""
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import tempfile


def main():
    root = Path(__file__).resolve().parent
    with tempfile.TemporaryDirectory(prefix="athena-smoke-", dir=root / "build") as temporary:
        base = Path(temporary).resolve()
        assert base.is_relative_to(root / "build"), "Smoke directory must remain inside build"
        app = base / "app"
        app.mkdir()
        exe = app / "Athena.exe"
        shutil.copyfile(root / "dist" / "Athena.exe", exe)
        working = base / "unrelated-working-directory"
        working.mkdir()
        environment = {key: value for key, value in os.environ.items()
                       if not any(word in key.upper() for word in ("KEY", "TOKEN", "SECRET", "CREDENTIAL", "PASSWORD"))
                       and key not in {"MODEL_PROVIDER", "MODEL_NAME", "OLLAMA_BASE_URL", "WEB_SEARCH_ENABLED", "MY_AGENT_DEBUG", "PYTHON_DOTENV_DISABLED"}}
        environment["LOCALAPPDATA"] = str(base / "local")
        environment["PATH"] = str(Path(os.environ["SystemRoot"]) / "System32")
        environment["MY_AGENT_DEBUG"] = "false"
        # Reserve, but never listen on, a local port so connection is refused.
        with socket.socket() as blocked:
            blocked.bind(("127.0.0.1", 0))
            proxy = f"http://127.0.0.1:{blocked.getsockname()[1]}"
            environment.update(HTTP_PROXY=proxy, HTTPS_PROXY=proxy, ALL_PROXY=proxy, NO_PROXY="")
            def run(commands):
                result = subprocess.run([str(exe)], input=commands, text=True,
                                        capture_output=True, cwd=working, env=environment, timeout=90)
                assert result.returncode == 0, "Executable failed"
                assert "Traceback" not in result.stderr + result.stdout, "Executable traceback"
                assert "Athena v0.12" in result.stdout, "Executable version mismatch"
                return result.stdout
            assert "Setup cancelled" in run("")
            (app / ".env").write_text("MODEL_PROVIDER=openrouter\nOPENROUTER_API_KEY=\n")
            assert "Setup cancelled" in run("")
            (app / ".env").write_text("MODEL_PROVIDER=openrouter\nOPENROUTER_API_KEY=offline-placeholder\n")
            output = run("/status\n/help\nexit\n")
            assert "Athena v0.12\nReady." in output and "Provider: OpenRouter" in output
            data = base / "local" / "Athena"
            assert data.is_dir() and not list(data.iterdir())
            # Exercise live packaged storage on an offline provider failure.
            output = run("hello offline\nexit\n")
            assert "Could not connect to the model provider" in output
            records = json.loads((data / "experiences.json").read_text())
            assert len(records) == 1 and records[0]["status"] == "model_failure"
            legacy = b'[{"text":"offline migration fixture"}]'
            (app / "memory.json").write_bytes(legacy)
            (app / "memory.json.bak").write_bytes(b"[]")
            run("exit\n")
            assert (data / "memory.json").read_bytes() == legacy
            assert (data / "memory.json.bak").read_bytes() == b"[]"
            assert (app / "memory.json").read_bytes() == legacy
            (app / "memory.json").unlink()
            (app / "memory.json.bak").unlink()
            assert {file.name for file in app.iterdir()} == {"Athena.exe", ".env"}
            assert not list(working.iterdir())
            (app / ".env").write_text("MODEL_PROVIDER=gemini\nGEMINI_API_KEY=offline-placeholder\n")
            assert "Provider: Gemini" in run("/status\nexit\n")
            assert "Could not connect to the model provider" in run("hello offline\nexit\n")
            output = run("/settings web on\n/status\nexit\n")
            assert "Web search: enabled" in output
            assert "may count toward its API usage or billing" in output
            assert "Web search: enabled" in run("/settings\nexit\n")
            assert "Web search: disabled" in run("/settings web off\nexit\n")
            assert "Web search: disabled" in run("/status\nexit\n")
            config = json.loads((data / "config.json").read_text())
            assert config["model_provider"] == "gemini" and config["web_search_enabled"] is False
            assert "offline-placeholder" not in (data / "config.json").read_text()
            # JSON-only Ollama configuration: no API keys or live server needed.
            # Ollama bypasses proxies, so explicitly use the reserved, closed port.
            (app / ".env").unlink()
            (data / "config.json").write_text(json.dumps({
                "model_provider": "ollama", "model_name": "llama3.2",
                "ollama_base_url": proxy, "web_search_enabled": True}))
            output = run("/status\n/settings web on\nhello offline\n/settings web off\nexit\n")
            for expected in ("Provider: Ollama", "Model: llama3.2", "Web search: unavailable",
                             "Ollama: unavailable", f"Base URL: {proxy}",
                             "Ollama is not reachable", "Install or start Ollama",
                             "Web search is not available with Ollama yet."):
                assert expected in output, "Ollama smoke behavior missing"
            assert "Enter your" not in output and "Web search: enabled" not in output
            config = json.loads((data / "config.json").read_text())
            assert config["model_provider"] == "ollama" and config["web_search_enabled"] is False
            assert config["ollama_base_url"] == proxy
            assert "Provider: Ollama" in run("/settings\nexit\n")
            records = json.loads((data / "experiences.json").read_text())
            assert records[-1]["status"] == "model_failure"
            assert not (data / ".env").exists() and not (app / ".env").exists()
    print("Executable smoke checks passed: v0.12, setup cancellation, OpenRouter/Gemini startup, Ollama JSON-only no-key configuration and friendly failure, unavailable Ollama web search, persistent settings, offline task storage, legacy copy, unrelated CWD, no Python on PATH.")


if __name__ == "__main__":
    main()
