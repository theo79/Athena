# Athena v0.12 for Windows

Extract the entire ZIP to a folder. Python is not required.

1. Run `.\Athena.exe` in a terminal.
2. Choose one provider: OpenRouter, Gemini or Ollama.
3. For a cloud provider, enter its one API key using hidden input. For Ollama, choose an installed model; no key is needed.
4. For cloud providers, choose whether to enable optional web search. Ollama web search is unavailable.
5. Start working. Use `/help` for commands and `exit` to quit.

Ollama must be installed and running with a local model. Start it with
`ollama serve` if needed and install a model yourself, for example
`ollama pull llama3.2`. Athena never downloads models automatically.

Existing valid configuration skips setup. `/status` and `/settings` show provider,
model and web access. `/settings web on` or `/settings web off` saves a change.
Web search defaults to disabled. OpenRouter uses its web plugin; Gemini uses
Google Search grounding. Both reuse your chat key. No separate search account
is needed. Web searches use your selected provider and may count toward its API
usage or billing. Model/API failures return friendly messages.

Preferences live in `%LOCALAPPDATA%\Athena\config.json`; keys are saved to
`%LOCALAPPDATA%\Athena\.env` (plaintext; keep private). No keys are shipped.
Advanced users can copy `.env.example` to `.env` beside the executable.
Precedence: explicit process environment, JSON preferences, user-folder `.env`,
then adjacent `.env`. Process variables win again after restarting.
Set `MODEL_PROVIDER=openrouter` or `gemini` and supply its key, or choose `ollama`
without a key. Ollama accepts `OLLAMA_BASE_URL` (default `http://localhost:11434`)
and defaults to model `llama3.2`; JSON-only configuration is supported. Remove `MODEL_NAME`
to use the default (`openrouter/free` or `gemini-3.1-flash-lite`). Provider switching
is manual. Set `WEB_SEARCH_ENABLED=true` only to opt in. Hidden input requires an
interactive terminal; manual `.env` configuration is available otherwise.
Setup checks key presence, not live authentication.

Memory and experience records, their backups, and recovery files are stored in
`%LOCALAPPDATA%\Athena\`, created automatically with your user permissions.
If LOCALAPPDATA is unavailable, Athena uses `AppData\Local\Athena` under your
home directory. Neither the working directory nor the temporary extraction
folder is used for these records. Keep this private data separate from releases.
Run only one Athena process at a time against this data directory.

To carry source-mode data forward, copy your old `memory.json` and
`experiences.json` (and their `.bak` files, if present) beside the executable
before first configured startup. For each store absent from the user-data
directory, Athena copies the adjacent legacy store and its backup without
deleting originals or overwriting existing destinations. Archived corruption
and quarantine files remain at their old location; move those manually if desired.
Never distribute your private stores with the application.

Local document tools operate inside the folder containing `Athena.exe` and
retain their existing permission and secret-file restrictions.
The executable is unsigned; this release has no installer or automatic updates.

Licensed under Apache License 2.0; see `LICENSE`.
