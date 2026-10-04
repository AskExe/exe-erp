"""Owned file admission and role constants; no allocator/native connection imports."""

import os
import stat
import time
from pathlib import Path

from .native_cleanup import Failures
from .native_contract import Refused

ROLE = "Exe Company ERP Viewer"
DOCTYPES = ("Company", "Customer", "Supplier", "Item")
AUTOMATIC = ("All", "Guest", "Desk User")
PERMISSIONS = ("read", "select", "report", "write", "create", "delete", "submit", "cancel", "amend", "import", "export", "share", "email", "print")
WRITE = PERMISSIONS[3:]
CONTROL = ("User", "Role", "DocPerm", "Custom DocPerm", "System Settings", "File", "Communication")


def stable_secret(path, maximum=4096, absolute_end=None):
    end = time.monotonic() + 3 if absolute_end is None else absolute_end
    if time.monotonic() >= end:
        raise Refused("expired_secret_read")
    path = Path(path)
    if not path.is_absolute() or path.resolve() != path:
        raise Refused("invalid_secret_file")
    parent = path.parent.stat()
    if parent.st_uid != os.getuid() or stat.S_IMODE(parent.st_mode) != 0o700:
        raise Refused("invalid_secret_parent")
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    errors = Failures(end)
    result = None
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or before.st_uid != os.getuid() or before.st_nlink != 1 or stat.S_IMODE(before.st_mode) not in (0o400, 0o600) or not 0 < before.st_size <= maximum:
            raise Refused("invalid_secret_file")
        data = os.read(fd, maximum + 1)
        def identity(s):
            return (s.st_dev, s.st_ino, s.st_uid, s.st_mode, s.st_nlink, s.st_size, s.st_mtime_ns, s.st_ctime_ns)
        if len(data) != before.st_size or identity(os.fstat(fd)) != identity(before) or identity(path.stat(follow_symlinks=False)) != identity(before) or (path.parent.stat().st_dev, path.parent.stat().st_ino) != (parent.st_dev, parent.st_ino):
            raise Refused("changed_secret_file")
        result = data.decode("utf-8")
        if time.monotonic() >= end:
            raise Refused("late_secret_read")
    except BaseException as error:
        errors.first(error)
    finally:
        errors.cleanup("secret_fd_close", lambda: os.close(fd))
    errors.finish()
    return result

