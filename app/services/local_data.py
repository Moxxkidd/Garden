"""Local SQLite backup sets and explicit, reference-safe generated-file cleanup."""

import hashlib
import json
import os
import shutil
import sqlite3
from contextlib import closing, contextmanager
from datetime import datetime, timedelta, timezone
from functools import wraps
from pathlib import Path

from sqlalchemy.engine import make_url

from app.cli.paths import formal_runtime_paths
from app.core.errors import ConflictError, InputValidationError
from app.core.settings import get_settings
from app.core.version import project_version
from app.services.collection_executor import executor_operation
from app.services.project_tokens import ProjectTokens

# These are concrete local references in the current schema, not a generic file registry.
REFERENCES = (
    ("auth_sessions", "id", "storage_ref", "session_payloads"),
    ("scan_runs", "id", "discovery_input_ref", "session_payloads/requests"),
    ("scan_runs", "id", "report_path", "reports"),
    ("evidence", "id", "storage_ref", "evidence"),
    ("scan_requests", "id", "protected_storage_ref", "session_payloads"),
    ("identity_checkpoints", "id", "storage_ref", "session_payloads"),
    ("credential_profiles", "id", "login_config_path", "configs"),
    ("collection_queues", "id", "private_ref", "collection"),
    ("collection_work", "id", "private_ref", "collection"),
    ("delivery_exports", "id", "artifact", "exports"),
)


def checksum(path):
    with path.open("rb") as handle:
        return _hash_stream(handle)


def _hash_stream(handle):
    value = hashlib.sha256()
    for block in iter(lambda: handle.read(65536), b""):
        value.update(block)
    return value.hexdigest()


def private_directory(path):
    if not path.parent.exists():
        private_directory(path.parent)
    path.mkdir(exist_ok=True, mode=0o700)
    path.chmod(0o700)


def private_copy(source, target):
    private_directory(target.parent)
    with source.open("rb") as incoming, target.open("xb") as outgoing:
        os.fchmod(outgoing.fileno(), 0o600)
        shutil.copyfileobj(incoming, outgoing, 65536)


def empty_destination(path):
    path = Path(path).expanduser().absolute()
    if path.is_symlink() or (path.exists() and (not path.is_dir() or any(path.iterdir()))):
        raise ConflictError("destination_must_be_empty")
    private_directory(path)
    return path


def local_file_operation(function):
    @wraps(function)
    def invoke(*args, **kwargs):
        try:
            return function(*args, **kwargs)
        except (OSError, sqlite3.DatabaseError, json.JSONDecodeError) as error:
            raise InputValidationError(
                f"本地数据操作失败（{type(error).__name__}）；请检查目录权限和备份完整性。"
            ) from error

    return invoke


class LocalDataService:
    def __init__(self, *, database_url=None, storage_directory=None, clock=None):
        self.database_url = database_url or get_settings().database_url
        database = make_url(self.database_url)
        if (
            database.get_backend_name() != "sqlite"
            or not database.database
            or database.database == ":memory:"
        ):
            raise InputValidationError("本地数据管理需要文件型 SQLite。")
        self.database = Path(database.database).absolute()
        paths = formal_runtime_paths()
        self.storage = (
            Path(storage_directory)
            if storage_directory
            else (paths.storage_dir if paths else Path.cwd() / "data")
        )
        self.storage = self.storage.absolute()
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.tokens = ProjectTokens()

    def _connect(self):
        return sqlite3.connect(self.database.as_uri() + "?mode=rw", uri=True, timeout=5)

    def _assert_idle(self, db):
        from app.models.scan_run import TERMINAL_SCAN_RUN_STATUSES

        marks = ",".join("?" for _ in TERMINAL_SCAN_RUN_STATUSES)
        active = db.execute(
            f"select count(*) from scan_runs where status not in ({marks})",
            tuple(TERMINAL_SCAN_RUN_STATUSES),
        ).fetchone()[0]
        active += db.execute(
            "select count(*) from delivery_exports where status='generating'"
        ).fetchone()[0]
        active += db.execute(
            "select count(*) from inventory_runs where status in ('pending','queued','running')"
        ).fetchone()[0]
        active += db.execute(
            "select count(*) from scan_jobs where status in ('pending','queued','running')"
        ).fetchone()[0]
        active += db.execute(
            "select count(*) from login_attempts "
            "where state in ('created','waiting_for_user','validating_restore')"
        ).fetchone()[0]
        if active:
            raise ConflictError("data_management_active_writers：请先等待采集、登录和导出结束。")

    @contextmanager
    def _exclusive(self):
        with executor_operation(self.database_url), closing(self._connect()) as db:
            # Hold the writer reservation as well: metadata/import/session mutations cannot
            # change a reference between enumerating it and copying/deleting its file.
            db.execute("BEGIN IMMEDIATE")
            try:
                self._assert_idle(db)
                yield db
            finally:
                db.rollback()

    def references(self, db):
        result = []
        for table, pk, column, folder in REFERENCES:
            for key, value in db.execute(
                f"SELECT {pk}, {column} FROM {table} WHERE {column} IS NOT NULL AND {column} != ?",
                ("",),
            ):
                relative = folder in {"collection", "exports"}
                local_value = str(key) + "/" + value if table == "delivery_exports" else value
                source = self.storage / folder / local_value if relative else Path(value).absolute()
                if relative and not source.resolve().is_relative_to(
                    (self.storage / folder).resolve()
                ):
                    raise InputValidationError("storage_reference_outside_managed_directory")
                file_name = (
                    hashlib.sha256(str(source).encode()).hexdigest()[:16] + "-" + source.name
                )
                destination = (
                    f"storage/{folder}/{local_value}"
                    if relative
                    else f"storage/{folder}/{file_name}"
                )
                # Session reader admits only direct children of session_payloads.
                if not relative and table in {"auth_sessions", "identity_checkpoints"}:
                    destination = f"storage/session_payloads/{file_name}"
                result.append(
                    dict(
                        table=table,
                        column=column,
                        pk=pk,
                        key=key,
                        value=value,
                        source=str(source),
                        destination=destination,
                        relative=relative,
                    )
                )
        # Evidence JSON has one additional concrete screenshot reference.
        for row in list(result):
            if row["table"] == "evidence" and Path(row["source"]).is_file():
                payload = json.loads(Path(row["source"]).read_text())
                screenshot = payload.get("screenshot", {}).get("path")
                if screenshot:
                    path = Path(screenshot).absolute()
                    result.append(
                        dict(
                            table=None,
                            source=str(path),
                            destination=f"storage/evidence/screenshots/{hashlib.sha256(str(path).encode()).hexdigest()[:16]}-{path.name}",
                        )
                    )
        return result

    def usage(self):
        def size(root):
            return (
                sum(p.stat().st_size for p in root.rglob("*") if p.is_file() and not p.is_symlink())
                if root.exists()
                else 0
            )

        return dict(
            database_bytes=sum(
                p.stat().st_size
                for p in [
                    self.database,
                    Path(str(self.database) + "-wal"),
                    Path(str(self.database) + "-shm"),
                ]
                if p.exists()
            ),
            evidence_bytes=size(self.storage / "evidence"),
            export_bytes=size(self.storage / "exports"),
            storage_bytes=size(self.storage),
            private_note=(
                "备份是私有数据，可能包含 Cookie、Token 和会话材料；不能作为可分享交接包。"
            ),
        )

    @local_file_operation
    def backup(self, destination):
        with self._exclusive():
            target = empty_destination(destination)
            try:
                db_path = target / "garden.db"
                with (
                    closing(
                        sqlite3.connect(self.database.as_uri() + "?mode=ro", uri=True)
                    ) as source,
                    closing(sqlite3.connect(db_path)) as copied,
                ):
                    db_path.chmod(0o600)
                    source.backup(copied)
                    references = self.references(copied)
                    revision = copied.execute("select version_num from alembic_version").fetchone()[
                        0
                    ]
                files, seen = [], set()
                for ref in references:
                    src, name = Path(ref["source"]), ref["destination"]
                    if name in seen:
                        continue
                    seen.add(name)
                    if not src.is_file() or src.is_symlink():
                        raise InputValidationError(
                            "backup_reference_missing：已有本地引用缺失，未生成可恢复备份。"
                        )
                    private_copy(src, target / name)
                    files.append(
                        dict(
                            path=name,
                            size=(target / name).stat().st_size,
                            sha256=checksum(target / name),
                        )
                    )
                files.insert(
                    0, dict(path="garden.db", size=db_path.stat().st_size, sha256=checksum(db_path))
                )
                manifest = dict(
                    schema="garden-private-backup-v1",
                    garden_version=project_version(),
                    database_revision=revision,
                    created_at=self.clock().isoformat(),
                    private=True,
                    files=files,
                    references=references,
                    exclusions=["环境变量/系统凭据库中的外部秘密不复制；恢复后需自行配置。"],
                )
                manifest_path = target / "manifest.json"
                with manifest_path.open("x", encoding="utf-8") as handle:
                    os.fchmod(handle.fileno(), 0o600)
                    json.dump(manifest, handle, ensure_ascii=False, indent=2)
                return dict(
                    directory=str(target),
                    file_count=len(files),
                    private=True,
                    bytes=sum(f["size"] for f in files),
                )
            except BaseException:
                shutil.rmtree(target)
                raise

    @local_file_operation
    def restore(self, backup_directory, destination):
        source = Path(backup_directory).expanduser().absolute()
        manifest = json.loads((source / "manifest.json").read_text())
        if manifest["schema"] != "garden-private-backup-v1":
            raise InputValidationError("unsupported_backup_schema")
        # Accept only this format's contained regular files; never extract arbitrary paths.
        for item in manifest["files"]:
            path = source / item["path"]
            if (
                path.is_symlink()
                or not path.resolve().is_relative_to(source.resolve())
                or checksum(path) != item["sha256"]
                or path.stat().st_size != item["size"]
            ):
                raise InputValidationError("backup_file_integrity_failed")
        names = {item["path"] for item in manifest["files"]}
        if "garden.db" not in names or any(
            ref["destination"] not in names for ref in manifest["references"]
        ):
            raise InputValidationError("backup_reference_invalid")
        target = empty_destination(destination)
        try:
            for item in manifest["files"]:
                output = target / item["path"]
                if not output.resolve().is_relative_to(target.resolve()):
                    raise InputValidationError("backup_path_invalid")
                private_copy(source / item["path"], output)
            replacements = {
                r["source"]: str(target / r["destination"]) for r in manifest["references"]
            }
            with closing(sqlite3.connect(target / "garden.db")) as db:
                if (
                    db.execute("PRAGMA integrity_check").fetchone()[0] != "ok"
                    or db.execute("PRAGMA foreign_key_check").fetchall()
                ):
                    raise InputValidationError("backup_database_integrity_failed")
                for ref in manifest["references"]:
                    if ref["table"] and not ref["relative"]:
                        table, pk, column = ref["table"], ref["pk"], ref["column"]
                        if (table, pk, column) not in {(a, b, c) for a, b, c, _ in REFERENCES}:
                            raise InputValidationError("backup_reference_invalid")
                        db.execute(
                            f"UPDATE {table} SET {column}=? WHERE {pk}=?",
                            (replacements[ref["source"]], ref["key"]),
                        )
                # Restoration changes paths and runtime context; never reactivate automatic sends.
                db.execute("UPDATE project_schedules SET enabled=0, last_result='restored_paused'")
                db.commit()
                if db.execute("PRAGMA foreign_key_check").fetchall():
                    raise InputValidationError("restored_database_integrity_failed")
            for ref in manifest["references"]:
                if ref["table"] == "evidence":
                    path = target / ref["destination"]
                    data = json.loads(path.read_text())
                    shot = data.get("screenshot")
                    if shot and shot.get("path") in replacements:
                        shot["path"] = replacements[shot["path"]]
                        path.write_text(json.dumps(data, ensure_ascii=False))
            config = target / "config.env"
            with config.open("x") as handle:
                os.fchmod(handle.fileno(), 0o600)
                handle.write(f"GARDEN_DATABASE_URL='sqlite+pysqlite:///{target / 'garden.db'}'\n")
            import shlex

            return dict(
                directory=str(target),
                database_url=f"sqlite+pysqlite:///{target / 'garden.db'}",
                active_directory_changed=False,
                schedules_paused=True,
                switch_instruction=(
                    f"检查此隔离目录后，明确使用 GARDEN_HOME={shlex.quote(str(target))} "
                    "garden web start；当前服务与数据目录未切换。"
                ),
            )
        except BaseException:
            shutil.rmtree(target)
            raise

    def _cleanup_items(self, db, retention_days, archived_project_ids):
        cutoff = (self.clock() - timedelta(days=retention_days)).timestamp()
        for pid in archived_project_ids:
            if db.execute("select status from projects where id=?", (pid,)).fetchone() != (
                "archived",
            ):
                raise ConflictError("cleanup_project_must_be_archived")
        refs = self.references(db)
        protected = {str(Path(r["source"]).resolve()) for r in refs}
        evidence_refs = {
            str(Path(r["source"]).resolve()) for r in refs if r["table"] != "delivery_exports"
        }
        if (self.storage / "exports").is_symlink():
            raise InputValidationError("export_directory_invalid")
        items = []
        for job_id, project_id, status, created in db.execute(
            "select id,project_id,status,created_at from delivery_exports"
        ):
            age = datetime.fromisoformat(created).replace(tzinfo=timezone.utc).timestamp()
            if status == "generating" or (age > cutoff and project_id not in archived_project_ids):
                continue
            directory = self.storage / "exports" / job_id
            if directory.is_symlink() or not directory.resolve().is_relative_to(
                (self.storage / "exports").resolve()
            ):
                raise InputValidationError("export_directory_invalid")
            for path in directory.rglob("*") if directory.exists() else []:
                if (
                    path.is_file()
                    and not path.is_symlink()
                    and str(path.resolve()) not in evidence_refs
                ):
                    # Expired generated deliveries may be removed; source evidence stays.
                    items.append(
                        dict(
                            path=str(path),
                            size=path.stat().st_size,
                            sha256=checksum(path),
                            kind="export",
                            job_id=job_id,
                        )
                    )
        for folder, pattern in [("session_payloads", "*.json"), ("collection", "*.json")]:
            root = self.storage / folder
            if root.is_symlink():
                raise InputValidationError("temporary_directory_invalid")
            for path in root.rglob(pattern) if root.exists() else []:
                if (
                    path.is_file()
                    and not path.is_symlink()
                    and str(path.resolve()) not in protected
                    and path.stat().st_mtime <= cutoff
                ):
                    items.append(
                        dict(
                            path=str(path),
                            size=path.stat().st_size,
                            sha256=checksum(path),
                            kind="unreferenced_private",
                        )
                    )
        return sorted(items, key=lambda x: x["path"])

    def cleanup_preview(self, *, retention_days=30, archived_project_ids=()):
        if not 0 <= retention_days <= 3650:
            raise InputValidationError("清理保留天数须为 0—3650。")
        with closing(self._connect()) as db:
            items = self._cleanup_items(db, retention_days, archived_project_ids)
        token = self.tokens.put((retention_days, list(archived_project_ids), items))
        return dict(
            token=token,
            count=len(items),
            bytes=sum(i["size"] for i in items),
            items=items,
            note="只删除过期/选定归档项目的生成产物及无引用私有临时材料；保留资产、观察、备注和证据。",
        )

    def cleanup_confirm(self, token):
        with self.tokens.lock, self._exclusive() as db:
            days, pids, expected = self.tokens.get(token)
            items = self._cleanup_items(db, days, pids)
            if items != expected:
                raise ConflictError("cleanup_preview_changed：文件或引用已变化，请重新预览。")
            for item in items:
                Path(item["path"]).unlink()
            for job_id in {i["job_id"] for i in items if i["kind"] == "export"}:
                db.execute(
                    "update delivery_exports set artifact=NULL,status='failed',"
                    "error_code='artifact_cleaned' where id=?",
                    (job_id,),
                )
            db.commit()
            self.tokens.values.pop(token)
            return dict(count=len(items), bytes=sum(i["size"] for i in items))
