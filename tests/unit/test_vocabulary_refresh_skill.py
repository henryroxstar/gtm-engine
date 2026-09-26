import json
import subprocess
import sys
from pathlib import Path


def test_aggregate_hits(tmp_path):
    # Create a mock source file with synthetic unmapped terms
    mock_hits_file = tmp_path / "hits.jsonl"
    hits = [
        {"title": "Unmapped Quantum Engineer", "evidence": "working on quantum agents in the lab"},
        {"title": "Unmapped Quantum Engineer", "evidence": "quantum agents are the future"},
        {"title": "VP of Random", "evidence": "we use basic algorithms"},
    ]
    with open(mock_hits_file, "w") as f:
        for hit in hits:
            f.write(json.dumps(hit) + "\n")

    # Run the aggregate_hits.py script
    script_path = Path("plugin/skills/vocabulary-refresh/scripts/aggregate_hits.py").resolve()

    result = subprocess.run(
        [sys.executable, str(script_path), str(mock_hits_file)],
        capture_output=True,
        text=True,
        check=True,
    )

    output = json.loads(result.stdout)

    # Verify it aggregated correctly
    # Titles
    assert ["Unmapped Quantum Engineer", 2] in output
    assert ["VP of Random", 1] in output

    # Evidence words (length > 4)
    assert ["quantum", 2] in output
    assert ["agents", 2] in output
