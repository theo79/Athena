"""Compile and execute the production C state machine, with synthetic button time.

Usage: python tests/check_power_button.py --cc /path/to/clang [--temp-dir DIR]
Windows uses clang/lld without a CRT; Linux/macOS use the host C compiler.
"""
import argparse
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cc", default=shutil.which("clang") or shutil.which("cc"))
    parser.add_argument("--temp-dir", type=Path)
    parser.add_argument("--zig", action="store_true", help="Use --cc as the zig executable")
    args = parser.parse_args()
    if not args.cc:
        parser.error("Provide a native C compiler with --cc (clang with lld on Windows)")
    root = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory(prefix="athena-button-", dir=args.temp_dir) as tmp:
        library = Path(tmp) / ("button.dll" if sys.platform == "win32" else "button.so")
        flags = ["-std=c11", "-Wall", "-Wextra", "-Werror", "-shared"]
        if sys.platform == "win32":
            flags += (["--target=x86_64-windows-msvc"] if args.zig else
                      ["--target=x86_64-pc-windows-msvc", "-fuse-ld=lld"])
            flags += ["-ffreestanding",
                      "-fno-builtin", "-fno-stack-protector", "-nostdlib",
                      "-Wl,/noentry", "-Wl,/nodefaultlib"]
        elif sys.platform != "win32":
            flags += ["-fPIC"]
        sources = [str(root / "main/power_button.c"), str(root / "tests/power_button_cases.c"),
                   str(root / "main/power.c"), str(root / "tests/power_standby_cases.c")]
        includes = ["-I", str(root / "main"), "-I", str(root / "tests/stubs")]
        if args.zig and sys.platform == "win32":
            # Link a CRT-free DLL with the minimal test loader hook.
            command = [args.cc, "build-lib", "-dynamic", "-O", "ReleaseSafe",
                       "-target", "x86_64-windows-msvc", "-cflags", "-std=c11",
                       "-Wall", "-Wextra", "-Werror", "-ffreestanding", "--",
                       *sources, *includes, f"-femit-bin={library}"]
        else:
            compiler = [args.cc, "cc"] if args.zig else [args.cc]
            command = [*compiler, *flags, *includes, *sources, "-o", str(library)]
        subprocess.run(command, check=True)
        # Execute in a child so Windows releases the DLL before temp cleanup.
        check = """import ctypes, sys
lib = ctypes.CDLL(sys.argv[1])
for name in ("run_power_button_tests", "run_power_standby_tests"):
    result = getattr(lib, name)()
    print(f"{name}: PASS" if result == 0 else f"{name}: FAIL at C line {result}")
    if result: sys.exit(1)
"""
        subprocess.run([sys.executable, "-c", check, str(library)], check=True)


if __name__ == "__main__":
    main()
