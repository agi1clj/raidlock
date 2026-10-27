import argparse
import json
import os
import sys
import time

p = argparse.ArgumentParser(allow_abbrev=False)
p.add_argument("--write", action="append", default=[])
p.add_argument("--replay")
p.add_argument("--exit", type=int, default=0)
p.add_argument("--sleep", type=float, default=0)
p.add_argument("--say", action="append", default=[])
p.add_argument("--dump-env")
p.add_argument("--dump-argv")
p.add_argument("--dump-prompt")
p.add_argument("-o")
a, _ = p.parse_known_args()
prompt = sys.stdin.read()
if a.dump_prompt:
    with open(a.dump_prompt, "w", encoding="utf-8") as f:
        f.write(prompt)
if a.dump_env:
    with open(a.dump_env, "w", encoding="utf-8") as f:
        json.dump(dict(os.environ), f)
if a.dump_argv:
    with open(a.dump_argv, "w", encoding="utf-8") as f:
        json.dump(sys.argv, f)
for spec in a.write:
    path, _, content = spec.partition("=")
    if os.path.dirname(path):
        os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content + "\n")
if a.sleep:
    time.sleep(a.sleep)
last = ""
if a.replay:
    with open(a.replay, encoding="utf-8") as f:
        for line in f:
            sys.stdout.write(line)
            obj = json.loads(line)
            item = obj.get("item") or {}
            if item.get("type") == "agent_message":
                last = item["text"]
for text in a.say:
    print(text)
sys.stdout.flush()
if a.o:
    with open(a.o, "w", encoding="utf-8") as f:
        f.write(last)
sys.exit(a.exit)
