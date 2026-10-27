import shutil


def find(name):
    """The absolute path of `name` on PATH, or `name` itself so the caller's usual missing-tool error applies."""
    return shutil.which(name) or name
