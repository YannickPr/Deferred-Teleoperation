# Exclusive local Robot ownership

`NodeStore.exclusive_robot_owner()` fences Robot services that use the same
local SQLite database.  `DummyRobotService.handle()` and
`DummyRobotService.recover()` acquire this non-blocking owner lock before
claiming inbox work or resetting interrupted processing and release it only
after the press/observe operation and its terminal durable commit.  M3a Robot
services inherit the same boundary.

The lock is a sidecar beside the canonical database path:

```text
<resolved-database-path>.robot-owner.lock
```

The database path and sidecar path are resolved once when `NodeStore` is
constructed, so relative paths and symlink aliases address the same sidecar.
The sidecar is created on first use, remains on disk after release, and stays
empty.  Unix uses the non-blocking exclusive `fcntl.flock` operation; Windows
uses non-blocking `msvcrt.locking` for one byte at offset zero.  The Windows
region may extend past EOF, so no marker byte or owner metadata is written.
See the Python [`fcntl`](https://docs.python.org/3/library/fcntl.html) and
[`msvcrt`](https://docs.python.org/3/library/msvcrt.html) documentation for
the platform primitives.

If another service owns the sidecar, acquisition raises `BusyError`
immediately.  Filesystem, permissions, and unsupported-lock errors are
propagated unchanged.  The caller should retry the complete `handle()` or
`recover()` operation according to its delivery loop; there is no hidden wait
or retry in the owner lock.  A busy `handle()` has not claimed its inbox row,
and a busy `recover()` has not reset `PROCESSING`, so neither operation emits a
new `HELD` result or performs adapter I/O.

The operating system releases the lock when its owner exits, including a
crash or forced termination.  Durable recovery then follows the existing
uncertain-dispatch rule: a crash before the adapter press is observed as
`NOT_APPLIED` and resolves to `HELD` without a press; a crash after the press
is observed as `APPLIED` and resolves to `SUCCEEDED` with the one persisted
press.  Normal return, an exception, and asyncio cancellation release the
sidecar through the context manager's `finally` path.  Reopening the database
reuses the retained sidecar.

This is a local process fence for cooperating services on one canonical path.
It does not provide a lease, fencing token, migration, DDL, or cross-host
coordination.  Hard-linked database spellings, distinct database files on the
same device, NFS or other filesystems with weaker lock semantics, and multiple
hosts are outside this guarantee.  SQLite and the sidecar do not make a real
actuator exactly-once; the external adapter's durable attributable observation
remains the recovery authority.

Focused proof:

```text
PYTHONPATH=python/src python -m pytest -q tests/test_robot_ownership.py --tb=short
```
