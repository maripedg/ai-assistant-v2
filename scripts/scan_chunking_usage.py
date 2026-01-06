import pathlib
import re

patterns = {
    "chunkers": re.compile(r"(backend\.ingest\.chunkers|ingest\.chunkers)"),
    "chunking_shims": re.compile(r"(backend\.ingest\.chunking|ingest\.chunking)"),
    "loaders_chunking": re.compile(r"(backend\.ingest\.loaders\.chunking|ingest\.loaders\.chunking)"),
}

roots = ["backend", "scripts"]

for key, pat in patterns.items():
    hits = []
    for root in roots:
        p = pathlib.Path(root)
        if not p.exists():
            continue
        for f in p.rglob("*.py"):
            try:
                text = f.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                continue
            for i, line in enumerate(text.splitlines(), 1):
                if pat.search(line):
                    hits.append((str(f), i, line.strip()))

    print("\n==============================")
    print(f"{key.upper()}")
    print("==============================")
    if not hits:
        print("NO HITS")
    else:
        for f, i, line in hits[:100]:
            print(f"{f}:{i}: {line}")
        if len(hits) > 100:
            print(f"... ({len(hits)-100} more)")
