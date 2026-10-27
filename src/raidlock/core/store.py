import json
import os
import tempfile

from .errors import Precondition

SCHEMA = 2


def atomic_write_text(path, text):
    folder = os.path.dirname(os.path.abspath(path))
    os.makedirs(folder, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix="." + os.path.basename(path) + ".", suffix=".tmp", dir=folder)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise
    _fsync_dir(folder)


def _fsync_dir(folder):
    if os.name != "posix":
        return
    try:
        fd = os.open(folder, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def write_json(path, data):
    atomic_write_text(path, json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False) + "\n")


def read_json(path, default=None):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return default
    except ValueError as e:
        raise Precondition(f"corrupt JSON in {path}: {e}", path=path) from e


def check_schema(data, path):
    found = data.get("schema", 1) if isinstance(data, dict) else 1
    if not isinstance(found, int) or found > SCHEMA:
        raise Precondition(
            f"{path} has schema {found}; this raidlock understands up to {SCHEMA}; upgrade raidlock",
            path=path,
            schema=found,
        )
    return found


def read_doc(path, key, default=None):
    data = read_json(path)
    if data is None:
        return {} if default is None else default
    check_schema(data, path)
    return data.get(key, {} if default is None else default)


def write_doc(path, key, value, **extra):
    write_json(path, {"schema": SCHEMA, key: value, **extra})


def append_line(path, line, sync=True):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "a", encoding="utf-8", newline="\n") as f:
        f.write(line.rstrip("\n") + "\n")
        f.flush()
        if sync:
            os.fsync(f.fileno())


def read_lines(path):
    try:
        with open(path, encoding="utf-8") as f:
            return [line.rstrip("\n") for line in f if line.strip()]
    except FileNotFoundError:
        return []


def read_jsonl(path):
    out = []
    for line in read_lines(path):
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


def write_jsonl(path, rows):
    atomic_write_text(path, "".join(json.dumps(r, sort_keys=True, ensure_ascii=False) + "\n" for r in rows))
