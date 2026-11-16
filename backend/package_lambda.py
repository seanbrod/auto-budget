"""Build a Lambda zip on Linux using the target Python version and architecture."""

import platform
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def main() -> None:
    if platform.system() != "Linux" or sys.version_info[:2] != (3, 12):
        raise SystemExit("Build on Linux with Python 3.12 and the target Lambda architecture.")

    output = ROOT / "dist"
    output.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="auto-budget-lambda-") as directory:
        staging = Path(directory)
        subprocess.run(
            [sys.executable, "-m", "pip", "install", "--target", str(staging), str(ROOT)],
            check=True,
        )
        shutil.make_archive(str(output / "api"), "zip", staging)
    print(f"Created {output / 'api.zip'}")


if __name__ == "__main__":
    main()
