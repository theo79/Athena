# Windows packaging — Athena v0.12

Source execution remains `python agent.py`. It supports guided setup, saved
preferences and legacy `.env` (see README), and retains `memory.json`
and `experiences.json` beside the source. Existing source stores are not moved.
Configuration lookup no longer searches parent folders or the working directory.
Source callers can still supply configuration through environment variables alone.

Build on Windows with 64-bit Python 3.12 or newer (tested locally with Python 3.14):

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install ".[dev,build]"
.\.venv\Scripts\python.exe -m pytest -q
.\build_windows.ps1
```

The script uses `Athena.spec`: a one-file **console** build with
PyInstaller 6.22.0, no project data collection, and no broad hidden-import list.
PyInstaller's standard dependency hooks collect SDK resources. The builder must
have Python; users running the executable do not need it. Build dependencies are
not runtime requirements. This is a repeatable build recipe, not a claim of
byte-for-byte reproducibility across dependency or Python versions.

The current source includes OpenRouter, Gemini and Ollama. Ollama needs a running
local server and model, but no API key; only the cloud providers support Athena
web search. The v0.12 executable includes all three provider adapters.

Outputs:

- `dist/Athena-release/Athena.exe`
- `dist/Athena-release/.env.example` (empty credential placeholders)
- `dist/Athena-release/README.md` and `LICENSE`
- `dist/Athena-v0.12-windows.zip`

`package_release.py` checks decompressed executable archive entries for private
runtime filenames, common credential formats, and known environment/local `.env`
secret values without printing them. It validates template placeholders and copies
only the four allowlisted release files. Unexpected files in an existing release
directory stop packaging. This heuristic audit is not a universal secret detector.
Build output, virtual environments, runtime files and ZIPs must not be committed.

Packaged setup saves `config.json` and private `.env` in `%LOCALAPPDATA%\Athena\`.
Adjacent `.env` remains supported. Missing provider configuration starts setup;
data-directory failures produce concise diagnostics.
The executable folder remains the document-tool workspace. Details of the
non-destructive legacy copy and fallback path are in [WINDOWS_README.md](WINDOWS_README.md).
No administrator rights are needed for normal operation.

For executable smoke verification, run:

```powershell
.\.venv\Scripts\python.exe smoke_windows.py
```

This copies the built executable into an isolated temporary workspace, uses fake
configuration and a closed loopback proxy (no provider request can leave the
machine), and verifies startup, local commands, persistent storage and migration.
It does not validate live provider authentication or model quality. The smoke
script checks both cloud adapters through a blocked proxy and Ollama through an
unreachable loopback endpoint, including no-key configuration and unavailable
web search. It does not install Ollama or download models.

Runtime path handling follows PyInstaller's
[documented frozen executable path](https://pyinstaller.org/en/stable/runtime-information.html).
