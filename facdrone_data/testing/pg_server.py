# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marius Ciepluch
"""Service provider: a private PostgreSQL server for one pytest process, from pip-shipped binaries.

The DB-backed tests used to skip unless an operator exported ``FACDRONE_TEST_PG_DSN``; nobody
did, so they never ran. Now each test process (each xdist worker) starts its own server from the
``pgembed`` wheel: PostgreSQL 17, production's major version, with binaries that need only libc
and zlib, so the same wheel runs on the Ubuntu host and in the Debian CI image. Each server gets
a fresh data directory under the system temp dir, a Unix socket only (no TCP port, so workers
cannot collide), fsync off and trust auth. It is not a container and needs no system install.
An exported operator DSN still wins.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

import pgembed

DATABASE = "facdrone_test"


BIN_DIR = Path(pgembed.__file__).parent / "pginstall" / "bin"


@dataclass(frozen=True)
class EmbeddedPostgres:
    root: Path
    bin_dir: Path

    @property
    def data(self) -> Path:
        return self.root / "data"

    @property
    def dsn(self) -> str:
        return f"postgresql+psycopg://postgres@/{DATABASE}?host={self.root}"

    def _run(self, tool: str, *args: str) -> None:
        subprocess.run(  # noqa: S603 — the pinned wheel's own binaries, fixed argv
            [str(self.bin_dir / tool), *args], check=True, capture_output=True, text=True
        )

    def stop(self) -> None:
        """Stop the server if it runs, then remove its directory; never masks a start error."""
        try:
            if (self.data / "postmaster.pid").exists():
                self._run("pg_ctl", "-D", str(self.data), "-m", "immediate", "-w", "stop")
        finally:
            shutil.rmtree(self.root, ignore_errors=True)


def start() -> EmbeddedPostgres:
    """initdb, start on a socket in the data root, and create the test database."""
    root = Path(tempfile.mkdtemp(prefix="facdrone-pg-"))
    server = EmbeddedPostgres(root, BIN_DIR)
    try:
        server._run(
            "initdb", "-D", str(server.data), "-U", "postgres", "--auth=trust",
            "--encoding=UTF8", "--locale=C", "--no-sync",
        )  # fmt: skip
        options = f"-k {root} -c listen_addresses='' -c fsync=off -c full_page_writes=off"
        server._run(
            "pg_ctl", "-D", str(server.data), "-o", options,
            "-l", str(root / "server.log"), "-w", "start",
        )  # fmt: skip
        server._run("createdb", "-h", str(root), "-U", "postgres", DATABASE)
    except BaseException:
        server.stop()
        raise
    return server
