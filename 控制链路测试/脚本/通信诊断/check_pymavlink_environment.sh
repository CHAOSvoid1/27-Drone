#!/bin/sh
# Read-only dependency check for the LicheeRV Nano Buildroot image.
# This script does not install packages or change system files.

set -u

echo "===== platform ====="
uname -a
uname -m

echo "===== python ====="
python3 -c 'import sys, sysconfig; print(sys.version); print("platform:", sysconfig.get_platform()); print("purelib:", sysconfig.get_paths().get("purelib"))'

echo "===== pip ====="
python3 -m pip --version

echo "===== installed dependencies ====="
python3 - <<'PY'
modules = ("serial", "lxml", "future", "fastcrc", "pymavlink")
for name in modules:
    try:
        module = __import__(name)
        print(f"{name}: OK version={getattr(module, '__version__', 'unknown')} file={getattr(module, '__file__', 'unknown')}")
    except Exception as exc:
        print(f"{name}: MISSING ({type(exc).__name__}: {exc})")
PY

echo "===== storage and memory ====="
df -h /
free -h

echo "===== compatible wheel tags (first 40) ====="
python3 -m pip debug --verbose 2>/dev/null | sed -n '/Compatible tags/,$p' | head -n 41

echo "===== result ====="
echo "Read-only check complete. Do not run pip install until this output is reviewed."
