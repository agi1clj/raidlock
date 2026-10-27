import os


def home():
    """Global raidlock home: $RAIDLOCK_HOME or ~/.raidlock."""
    return os.path.abspath(os.environ.get("RAIDLOCK_HOME") or os.path.join(os.path.expanduser("~"), ".raidlock"))


_SYNC = (
    ("/library/mobile documents/", "iCloud"),
    ("/icloud drive/", "iCloud"),
    ("/dropbox/", "Dropbox"),
    ("/onedrive", "OneDrive"),
    ("/google drive/", "Google Drive"),
    ("/googledrive/", "Google Drive"),
    ("/my drive/", "Google Drive"),
    ("/library/cloudstorage/", "a cloud storage provider"),
)


def synced_folder(folder):
    """Name of the sync provider when `folder` sits in a cloud-synced location, else ''."""
    real = (os.path.realpath(folder).replace("\\", "/") + "/").lower()
    return next((name for marker, name in _SYNC if marker in real), "")


def register(index, uid, board_dir):
    """Add or refresh a board in the global registry (raidlock.db); returns its workspace name."""
    stem = os.path.basename(os.path.dirname(os.path.realpath(board_dir))) or "board"
    return index.register(uid, board_dir, stem)


def workspaces(index):
    """Registered workspaces: [{uid, name, board, last_seen, version}]."""
    return index.workspaces()


def remove(index, names):
    return index.unregister(names)
