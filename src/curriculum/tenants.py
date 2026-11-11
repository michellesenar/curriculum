"""
Hosted testers: who they are, which private link opens which database.

Layout under the hosted data directory:
    tenants.db          registry: testers and the hashed tokens that open them
    db/<tenant_id>.db   one curriculum database per tester

A token is the secret in a tester's private connector URL. Only its SHA-256
hash is stored, so a copy of the registry (or a backup of it) holds no working
links. A tester's database is named by an internal id, not by the token, so a
link can be revoked and replaced without touching their data.
"""

import hashlib
import os
import secrets
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from curriculum import db
from curriculum.paths import DATA_DIR


def hosted_dir() -> Path:
    """Hosted data root. Override with CURRICULUM_HOSTED_DIR."""
    override = os.environ.get("CURRICULUM_HOSTED_DIR")
    return Path(override).expanduser() if override else DATA_DIR / "hosted"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


@contextmanager
def _registry(root: Path):
    """Open the registry; commit on success, roll back on error, always close."""
    root.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(root / "tenants.db")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS tenants (
            id TEXT PRIMARY KEY,
            label TEXT NOT NULL,
            created_at TEXT NOT NULL,
            removed_at TEXT
        );
        CREATE TABLE IF NOT EXISTS tokens (
            token_hash TEXT PRIMARY KEY,
            tenant_id TEXT NOT NULL,
            created_at TEXT NOT NULL,
            revoked_at TEXT,
            FOREIGN KEY (tenant_id) REFERENCES tenants(id)
        );
    """)
    try:
        with conn:
            yield conn
    finally:
        conn.close()


def _db_path(tenant_id: str, root: Path) -> Path:
    return root / "db" / f"{tenant_id}.db"


def _new_token(conn: sqlite3.Connection, tenant_id: str) -> str:
    token = secrets.token_urlsafe(32)  # 256 bits
    conn.execute(
        "INSERT INTO tokens (token_hash, tenant_id, created_at) VALUES (?, ?, ?)",
        (hash_token(token), tenant_id, _now()),
    )
    return token


def create_tenant(label: str, root: Path | None = None) -> tuple[str, str]:
    """
    Add a tester. Returns (tenant_id, token). The token is shown once; it
    can't be recovered later, only replaced with rotate_token.
    """
    root = root or hosted_dir()
    tenant_id = secrets.token_hex(8)
    with _registry(root) as conn:
        conn.execute(
            "INSERT INTO tenants (id, label, created_at) VALUES (?, ?, ?)",
            (tenant_id, label, _now()),
        )
        token = _new_token(conn, tenant_id)
    db.init_hosted_db(_db_path(tenant_id, root))
    return tenant_id, token


def resolve(token: str, root: Path | None = None) -> Path | None:
    """The database path for a valid token, or None for an unknown or revoked one."""
    root = root or hosted_dir()
    if not (root / "tenants.db").exists():
        return None
    with _registry(root) as conn:
        row = conn.execute(
            """
            SELECT t.tenant_id FROM tokens t
            JOIN tenants n ON n.id = t.tenant_id
            WHERE t.token_hash = ? AND t.revoked_at IS NULL AND n.removed_at IS NULL
            """,
            (hash_token(token),),
        ).fetchone()
    return _db_path(row["tenant_id"], root) if row else None


def revoke_token(token: str, root: Path | None = None) -> bool:
    """Turn off one link. Returns False if it was unknown or already revoked."""
    root = root or hosted_dir()
    with _registry(root) as conn:
        cur = conn.execute(
            "UPDATE tokens SET revoked_at = ? WHERE token_hash = ? AND revoked_at IS NULL",
            (_now(), hash_token(token)),
        )
    return cur.rowcount > 0


def rotate_token(tenant_id: str, root: Path | None = None) -> str:
    """Revoke all of a tester's links and issue one new one. Their data is untouched."""
    root = root or hosted_dir()
    with _registry(root) as conn:
        tenant = conn.execute(
            "SELECT 1 FROM tenants WHERE id = ? AND removed_at IS NULL", (tenant_id,)
        ).fetchone()
        if tenant is None:
            raise KeyError(f"No active tester with id {tenant_id}")
        conn.execute(
            "UPDATE tokens SET revoked_at = ? WHERE tenant_id = ? AND revoked_at IS NULL",
            (_now(), tenant_id),
        )
        return _new_token(conn, tenant_id)


def remove_tenant(tenant_id: str, root: Path | None = None) -> bool:
    """
    Remove a tester: revoke every link and delete their database file.
    Backups age out on their own schedule. Returns False if no such active tester.
    """
    root = root or hosted_dir()
    with _registry(root) as conn:
        cur = conn.execute(
            "UPDATE tenants SET removed_at = ? WHERE id = ? AND removed_at IS NULL",
            (_now(), tenant_id),
        )
        if cur.rowcount == 0:
            return False
        conn.execute(
            "UPDATE tokens SET revoked_at = ? WHERE tenant_id = ? AND revoked_at IS NULL",
            (_now(), tenant_id),
        )
    # SQLite may leave -wal/-shm/-journal files beside the database
    path = _db_path(tenant_id, root)
    for suffix in ("", "-wal", "-shm", "-journal"):
        Path(str(path) + suffix).unlink(missing_ok=True)
    return True


def list_tenants(root: Path | None = None) -> list[dict]:
    """Active testers, for the owner. Never includes tokens."""
    root = root or hosted_dir()
    if not (root / "tenants.db").exists():
        return []
    with _registry(root) as conn:
        rows = conn.execute(
            "SELECT id, label, created_at FROM tenants WHERE removed_at IS NULL ORDER BY created_at"
        ).fetchall()
    return [dict(r) for r in rows]


def tenant_db_path(tenant_id: str, root: Path | None = None) -> Path | None:
    """The database path for an active tester id, or None if there's no such active tester."""
    root = root or hosted_dir()
    if not (root / "tenants.db").exists():
        return None
    with _registry(root) as conn:
        row = conn.execute(
            "SELECT 1 FROM tenants WHERE id = ? AND removed_at IS NULL", (tenant_id,)
        ).fetchone()
    return _db_path(tenant_id, root) if row else None
