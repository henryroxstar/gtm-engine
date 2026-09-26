import json
import sys
from collections import Counter


def aggregate_hits(hits_file: str) -> list[tuple[str, int]]:
    """Reads a JSONL file of dropped web hits and counts unmapped titles or hit phrases."""
    counter = Counter()
    try:
        with open(hits_file) as f:
            for line in f:
                if not line.strip():
                    continue
                hit = json.loads(line)
                # Aggregate unmapped titles
                title = hit.get("title", "").strip()
                if title:
                    counter[title] += 1
                # Aggregate evidence chunks for ai vocabulary
                evidence = hit.get("evidence", "").strip()
                if evidence:
                    # simplistic extraction for frequency counting
                    words = [w for w in evidence.split() if len(w) > 4]
                    for w in words:
                        counter[w.lower()] += 1
    except FileNotFoundError:
        print(f"Error: Could not read {hits_file}", file=sys.stderr)
        sys.exit(1)

    return counter.most_common(100)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python aggregate_hits.py <path_to_hits.jsonl>")
        sys.exit(1)

    candidates = aggregate_hits(sys.argv[1])
    print(json.dumps(candidates, indent=2))
