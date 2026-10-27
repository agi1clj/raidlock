import argparse
import json
import sys

p = argparse.ArgumentParser(allow_abbrev=False)
p.add_argument("--write", action="append", default=[])
p.add_argument("--quota", action="store_true")
a, _ = p.parse_known_args()
sys.stdin.read()
for spec in a.write:
    path, _, content = spec.partition("=")
    with open(path, "w", encoding="utf-8") as f:
        f.write(content + "\n")
print(json.dumps({"type": "system", "subtype": "init", "model": "claude-test", "apiKeySource": "none"}))
if a.quota:
    message = "Claude usage limit reached; your credit is exhausted until the reset"
    print(
        json.dumps(
            {
                "type": "result",
                "subtype": "error_during_execution",
                "is_error": True,
                "result": message,
                "usage": {"input_tokens": 100, "output_tokens": 10},
                "total_cost_usd": 0.02,
            }
        )
    )
    sys.exit(1)
done = {"status": "done", "evidence": ["ok"], "paths_changed": [], "open_questions": []}
print(
    json.dumps(
        {
            "type": "result",
            "subtype": "success",
            "result": json.dumps(done),
            "structured_output": done,
            "usage": {"input_tokens": 100, "output_tokens": 10},
            "total_cost_usd": 0.02,
        }
    )
)
