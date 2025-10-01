import pathlib
import sys

OFFENDER_PATTERNS = (
    "from app.",
    "from providers.",
    "import app.",
    "import providers.",
)

ROOT = pathlib.Path(__file__).resolve().parents[1]
TARGET_DIR = ROOT / "backend"


def scan_file(path: pathlib.Path):
    offenders = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except UnicodeDecodeError:
        return offenders
    for idx, line in enumerate(lines, start=1):
        if any(pattern in line for pattern in OFFENDER_PATTERNS):
            offenders.append((idx, line.rstrip()))
    return offenders


def main():
    offenders_found = False
    for file_path in sorted(TARGET_DIR.rglob("*.py")):
        matches = scan_file(file_path)
        for lineno, src in matches:
            offenders_found = True
            relative = file_path.relative_to(ROOT)
            print(f"OFFENDER {relative}:{lineno}: {src}")
    sys.exit(1 if offenders_found else 0)


if __name__ == "__main__":
    main()
