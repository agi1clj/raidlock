import hashlib
import hmac
import json
import os
import secrets
import stat

from .errors import Precondition
from .portable import BINARY, NONBLOCK, open_nofollow


def command_hash(command):
    return hashlib.sha256(command.encode()).hexdigest()


def _key(board, create=False):
    from .identity import token_path

    path = os.path.join(os.path.dirname(token_path(board.home, board.uid, board.lead)), "verify.key")
    if os.path.commonpath((os.path.realpath(path), os.path.realpath(board.workspace))) == os.path.realpath(
        board.workspace
    ):
        raise Precondition("verification key must live outside the workspace; move RAIDLOCK_HOME")
    if create:
        os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
    try:
        if create:
            try:
                fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | BINARY, 0o600)
            except FileExistsError:
                pass
            else:
                with os.fdopen(fd, "wb") as stream:
                    stream.write(secrets.token_bytes(32))
        if os.path.islink(path):
            raise OSError("symlink")
        fd = open_nofollow(path, os.O_RDONLY | NONBLOCK)
        with os.fdopen(fd, "rb") as stream:
            info = os.fstat(stream.fileno())
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_nlink != 1
                or (os.name == "posix" and stat.S_IMODE(info.st_mode) != 0o600)
            ):
                raise OSError("not a private regular file")
            key = stream.read(33)
        if len(key) != 32:
            raise OSError("invalid key")
        return key
    except OSError as exc:
        raise Precondition("missing or unsafe board verification key") from exc


def _payload(receipt):
    return json.dumps(
        {k: v for k, v in receipt.items() if k != "signature"}, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode()


def sign(board, receipt):
    return {**receipt, "signature": hmac.new(_key(board, create=True), _payload(receipt), hashlib.sha256).hexdigest()}


def valid(board, card, receipt):
    try:
        if receipt.get("card") != card["id"] or receipt.get("board") != board.uid:
            return False
        if receipt.get("command_hash") != command_hash(card.get("verify_cmd", "")):
            return False
        expected = hmac.new(_key(board), _payload(receipt), hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, receipt.get("signature", ""))
    except (Precondition, TypeError, ValueError, AttributeError):
        return False
