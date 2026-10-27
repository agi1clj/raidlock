import datetime

FORMAT = "%Y-%m-%dT%H:%M:%SZ"
_offset = datetime.timedelta(0)


def now():
    return datetime.datetime.now(datetime.UTC).replace(microsecond=0) + _offset


def iso(dt=None):
    return (dt or now()).strftime(FORMAT)


def parse(text):
    try:
        return datetime.datetime.strptime(text, FORMAT).replace(tzinfo=datetime.UTC)
    except (TypeError, ValueError):
        return None


def later(seconds, start=None):
    return iso((start or now()) + datetime.timedelta(seconds=seconds))


def age_seconds(text, at=None):
    dt = parse(text)
    if dt is None:
        return None
    return ((at or now()) - dt).total_seconds()


def travel(seconds):
    global _offset
    _offset += datetime.timedelta(seconds=seconds)


def reset():
    global _offset
    _offset = datetime.timedelta(0)
