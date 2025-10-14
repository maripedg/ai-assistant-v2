#!/usr/bin/env python3
"""
Read-only configuration audit tool for RAG alignment.

Scans YAML files recursively under a config root (default: backend/config/),
collects relevant retrieval and embedding parameters, detects inconsistencies,
and prints a clear table and final summary. Optionally writes results to CSV.

Constraints:
- Read-only: no file or DB modifications
- Offline: no network or model calls
- No installs: use only stdlib + yaml + argparse
"""

import argparse
import os
import sys
from typing import Any, Dict, Iterable, List, Optional, Tuple

try:
    import yaml  # PyYAML expected to be available in project
except Exception as e:
    print("PyYAML is required to run this audit script.")
    raise


# Keys to search
TARGET_KEYS = {
    "retrieval",
    "embeddings",
    "profiles",
    "distance",
    "metric",
    "score_mode",
    "normalized",
    "threshold",
    "model",
}

# Section anchors we recognize for context
SECTION_ANCHORS = ("retrieval", "embeddings", "profiles")


def is_yaml_file(path: str) -> bool:
    name = path.lower()
    return name.endswith(".yml") or name.endswith(".yaml")


def safe_load_yaml(fp: str, verbose: bool = False) -> Optional[Any]:
    try:
        with open(fp, "r", encoding="utf-8") as f:
            content = f.read()
        if not content.strip():
            return None
        return yaml.safe_load(content)
    except Exception as e:
        if verbose:
            print(f"[warn] Failed to parse YAML: {fp} :: {e}")
        return None


def walk(obj: Any, path: List[str]) -> Iterable[Tuple[List[str], str, Any]]:
    """Yield (full_path, key, value) for each mapping key encountered recursively.

    For lists, traverse indices as part of the path to maintain context.
    """
    if isinstance(obj, dict):
        for k, v in obj.items():
            cur_path = path + [str(k)]
            yield (cur_path, str(k), v)
            # Recurse into children
            for item in walk(v, cur_path):
                yield item
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            cur_path = path + [str(i)]
            for item in walk(v, cur_path):
                yield item
    else:
        # leaf scalar
        return


def first_anchor_in(path: List[str]) -> Optional[str]:
    for p in path:
        if p in SECTION_ANCHORS:
            return p
    return None


def format_value(v: Any, max_len: int = 120) -> str:
    if isinstance(v, (dict, list)):
        # Provide concise structural hint
        if isinstance(v, dict):
            s = "<mapping {} keys>".format(len(v))
        else:
            s = "<sequence {} items>".format(len(v))
    else:
        s = str(v)
    if len(s) > max_len:
        return s[: max_len - 3] + "..."
    return s


def to_boolish(v: Any) -> Optional[bool]:
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return bool(v)
    if isinstance(v, str):
        t = v.strip().lower()
        if t in {"true", "yes", "y", "1"}:
            return True
        if t in {"false", "no", "n", "0"}:
            return False
    return None


def canon_similarity_name(v: Any) -> Optional[str]:
    if v is None:
        return None
    s = str(v).strip().lower()
    # Map common synonyms to canonical names
    mapping = {
        "cos": "cosine",
        "cosine": "cosine",
        "cosine_similarity": "cosine",
        "l2": "euclidean",
        "euclidean": "euclidean",
        "euclidean_l2": "euclidean",
        "manhattan": "manhattan",
        "l1": "manhattan",
        "dot": "dot",
        "ip": "dot",
        "inner": "dot",
        "inner_product": "dot",
    }
    return mapping.get(s, s)


def truncate(s: str, width: int) -> str:
    if len(s) <= width:
        return s
    if width <= 3:
        return s[:width]
    return s[: width - 3] + "..."


def pad(s: str, width: int) -> str:
    return truncate(s, width).ljust(width)


def discover_yaml_files(root: str) -> List[str]:
    files: List[str] = []
    for base, _, fnames in os.walk(root):
        for fn in fnames:
            fp = os.path.join(base, fn)
            if is_yaml_file(fp):
                files.append(fp)
    files.sort()
    return files


def collect_hits(files: List[str], verbose: bool = False) -> Tuple[List[Dict[str, str]], Dict[str, set]]:
    """
    Returns:
      - hits: list of {file, section, parameter, value}
      - aggregates: sets for summary keys
    """
    hits: List[Dict[str, str]] = []
    retrieval_distance: set = set()
    retrieval_score_mode: set = set()
    embeddings_metric: set = set()
    embeddings_normalized: set = set()

    for fp in files:
        data = safe_load_yaml(fp, verbose=verbose)
        if data is None:
            if verbose:
                print(f"[info] Skipping empty/unparsed YAML: {fp}")
            continue

        for full_path, key, value in walk(data, []):
            if key not in TARGET_KEYS:
                continue

            section = first_anchor_in(full_path) or "-"
            # Record hit value
            hits.append(
                {
                    "file": fp,
                    "section": section,
                    "parameter": key,
                    "value": format_value(value),
                }
            )

            # Collect for summary if they occur under expected sections
            if section == "retrieval":
                if key == "distance":
                    c = canon_similarity_name(value)
                    if c:
                        retrieval_distance.add(c)
                elif key == "score_mode":
                    if isinstance(value, (str, int, float, bool)):
                        retrieval_score_mode.add(str(value).strip().lower())
            elif section == "embeddings":
                if key == "metric":
                    c = canon_similarity_name(value)
                    if c:
                        embeddings_metric.add(c)
                elif key == "normalized":
                    b = to_boolish(value)
                    if b is not None:
                        embeddings_normalized.add("true" if b else "false")

    aggregates = {
        "retrieval.distance": retrieval_distance,
        "retrieval.score_mode": retrieval_score_mode,
        "embeddings.metric": embeddings_metric,
        "embeddings.normalized": embeddings_normalized,
    }
    return hits, aggregates


def compute_alignment(aggregates: Dict[str, set]) -> Tuple[bool, List[str]]:
    tips: List[str] = []

    r_dist = aggregates.get("retrieval.distance", set())
    e_metric = aggregates.get("embeddings.metric", set())
    r_score = aggregates.get("retrieval.score_mode", set())
    e_norm = aggregates.get("embeddings.normalized", set())

    aligned = True

    # Check distance vs metric alignment (only if both singular)
    if len(r_dist) == 0 or len(e_metric) == 0:
        aligned = False
        tips.append("Distance/metric unknown; ensure both are set consistently.")
    elif len(r_dist) > 1 or len(e_metric) > 1:
        aligned = False
        tips.append(
            f"Mixed values: retrieval.distance={sorted(r_dist)} vs embeddings.metric={sorted(e_metric)}"
        )
    else:
        r = next(iter(r_dist))
        e = next(iter(e_metric))
        if r != e:
            aligned = False
            tips.append(f"Use consistent similarity: retrieval.distance='{r}' vs embeddings.metric='{e}'")

    # Check normalized requirement when score_mode=normalized
    needs_norm = False
    if "normalized" in r_score:
        needs_norm = True

    if needs_norm:
        if len(e_norm) == 0:
            aligned = False
            tips.append("score_mode=normalized but embeddings.normalized is missing")
        elif "true" not in e_norm:
            aligned = False
            tips.append("Enable embeddings.normalized=true when score_mode=normalized")

    return aligned, tips


def print_table(hits: List[Dict[str, str]]) -> None:
    # Determine column widths
    headers = ("FILE", "SECTION", "PARAMETER", "VALUE")
    rows = [
        (
            str(h["file"]),
            str(h["section"]),
            str(h["parameter"]),
            str(h["value"]),
        )
        for h in hits
    ]

    file_w = max([len(headers[0])] + [len(r[0]) for r in rows])
    sec_w = max([len(headers[1])] + [len(r[1]) for r in rows])
    par_w = max([len(headers[2])] + [len(r[2]) for r in rows])
    val_w = max([len(headers[3])] + [len(r[3]) for r in rows])

    # Clamp widths for readability
    file_w = min(file_w, 60)
    sec_w = min(sec_w, 16)
    par_w = min(par_w, 18)
    val_w = min(val_w, 80)

    line = f"{pad(headers[0], file_w)}  {pad(headers[1], sec_w)}  {pad(headers[2], par_w)}  {pad(headers[3], val_w)}"
    sep = "-" * len(line)
    print(line)
    print(sep)
    for r in rows:
        print(
            f"{pad(r[0], file_w)}  {pad(r[1], sec_w)}  {pad(r[2], par_w)}  {pad(r[3], val_w)}"
        )


def write_csv(path: str, hits: List[Dict[str, str]]) -> None:
    # Minimal CSV writer without external dependencies
    def esc(cell: str) -> str:
        needs_quote = any(ch in cell for ch in [",", "\n", "\r", '"'])
        cell_escaped = cell.replace('"', '""')
        return f'"{cell_escaped}"' if needs_quote or cell_escaped.strip() != cell_escaped else cell_escaped

    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write("file,section,parameter,value\n")
        for h in hits:
            row = [h.get("file", ""), h.get("section", ""), h.get("parameter", ""), h.get("value", "")]
            f.write(",".join(esc(str(c)) for c in row) + "\n")


def summarize_print(aggregates: Dict[str, set], aligned: bool, tips: List[str]) -> None:
    def fmt_set(s: set) -> str:
        if not s:
            return "unknown"
        if len(s) == 1:
            return next(iter(s))
        return "mixed: " + ", ".join(sorted(s))

    print("\n==== CONFIG SUMMARY ====")
    print(f"retrieval.distance      : {fmt_set(aggregates.get('retrieval.distance', set()))}")
    print(f"retrieval.score_mode    : {fmt_set(aggregates.get('retrieval.score_mode', set()))}")
    print(f"embeddings.metric       : {fmt_set(aggregates.get('embeddings.metric', set()))}")
    print(f"embeddings.normalized   : {fmt_set(aggregates.get('embeddings.normalized', set()))}")
    print(f"ALIGNED                 : {'true' if aligned else 'false'}")
    if tips:
        print("Tips:")
        for t in tips:
            print(f"- {t}")


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Audit RAG config for retrieval/embedding alignment (read-only)"
    )
    parser.add_argument(
        "--config-root",
        default="backend/config",
        help="Root directory to scan recursively for YAML files",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable verbose output (parse issues, file discovery)",
    )
    parser.add_argument(
        "--output",
        help="Optional CSV output file path to write hits",
    )

    args = parser.parse_args(argv)

    root = args.config_root
    if not os.path.isdir(root):
        print(f"Config root not found: {root}")
        return 2

    if args.verbose:
        print(f"[info] Scanning for YAML under: {root}")

    files = discover_yaml_files(root)
    if args.verbose:
        print(f"[info] Found {len(files)} YAML file(s)")
        for fp in files:
            print(f"       - {fp}")

    hits, aggregates = collect_hits(files, verbose=args.verbose)

    if hits:
        print_table(hits)
    else:
        print("No matching keys found in YAML files.")

    aligned, tips = compute_alignment(aggregates)
    summarize_print(aggregates, aligned, tips)

    if args.output:
        try:
            write_csv(args.output, hits)
            if args.verbose:
                print(f"[info] Wrote CSV: {args.output}")
        except Exception as e:
            print(f"[warn] Failed to write CSV '{args.output}': {e}")

    return 0


if __name__ == "__main__":
    sys.exit(main())

