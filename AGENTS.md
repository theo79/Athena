# Athena engineering policy

Athena is OS-independent. Windows is a development host, not the exclusive
deployment target. These requirements apply to M2 and all later milestones.

- Support Windows 11, Ubuntu/Debian, Fedora and Arch Linux with Python 3.12+.
- Keep core application logic OS-independent. Use pathlib and portable standard
  library APIs; do not hardcode OS-specific paths, separators, shell commands or
  storage locations into core logic.
- Isolate unavoidable platform behavior in explicit, documented adapters.
- Use temporary paths and portable filenames in filesystem tests. Test filename
  parsing/validation separately from actual file creation when OS rules differ.
- Never add platform skips or weaken assertions merely to make tests pass on one
  OS. Retain meaningful coverage of the intended behavior on each platform.
- CI must validate Windows and Linux independently across supported Python
  versions; distribution jobs additionally exercise Debian, Fedora and Arch.
- Run relevant tests and the full regression suite after changes, plus
  `python -m pip check`. Respect existing CRLF files; use
  `git -c core.whitespace=cr-at-eol diff --check` without normalizing source files.
- Report which environments actually ran. Configured CI jobs are not evidence
  that remote Windows/Linux checks have passed.
- Keep changes within the authorized milestone. Do not introduce unrelated
  refactors or dependencies to address a platform-specific fixture failure.
