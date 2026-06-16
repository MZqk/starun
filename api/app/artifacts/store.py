import hashlib
import json
import os
import secrets
import stat
from pathlib import Path

from app.artifacts.contracts import (
    MAX_ARTIFACT_BYTES,
    ArtifactManifestEntry,
    JsonValue,
    MediaType,
    media_type_for_name,
    validate_artifact_name,
)
from app.filesystem import UnsafePathError, open_directory_fd


class ArtifactPathError(ValueError):
    pass


class UnsupportedArtifactError(ValueError):
    pass


class ArtifactSizeError(ValueError):
    pass


class ArtifactStore:
    def __init__(self, root: Path, *, create: bool = True) -> None:
        self._root_fd: int | None = None
        self.root = root.absolute()
        try:
            self._root_fd = open_directory_fd(root, create=create)
        except (OSError, UnsafePathError) as exc:
            raise ArtifactPathError(
                "artifact root or parent is unsafe or symlinked"
            ) from exc

    @classmethod
    def from_directory_fd(cls, root: Path, directory_fd: int) -> "ArtifactStore":
        instance = cls.__new__(cls)
        instance._root_fd = None
        instance.root = root.absolute()
        descriptor = os.dup(directory_fd)
        try:
            if not stat.S_ISDIR(os.fstat(descriptor).st_mode):
                raise ArtifactPathError("artifact root must be a directory")
            instance._root_fd = descriptor
            return instance
        except BaseException:
            os.close(descriptor)
            raise

    @property
    def root_fd(self) -> int:
        return self._require_root_fd()

    def write_bytes(
        self,
        name: str,
        data: bytes,
        *,
        demo: bool = False,
    ) -> ArtifactManifestEntry:
        media_type = self._validate_supported_name(name)
        if len(data) > MAX_ARTIFACT_BYTES:
            raise ArtifactSizeError("artifact exceeds maximum byte size")
        root_fd = self._require_root_fd()
        temporary_name = f".tmp-{secrets.token_hex(16)}"
        flags = (
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0)
        )
        descriptor = os.open(temporary_name, flags, 0o600, dir_fd=root_fd)
        try:
            view = memoryview(data)
            while view:
                written = os.write(descriptor, view)
                if written <= 0:
                    raise OSError("artifact write made no progress")
                view = view[written:]
            os.fsync(descriptor)
            os.close(descriptor)
            descriptor = -1
            os.replace(
                temporary_name,
                name,
                src_dir_fd=root_fd,
                dst_dir_fd=root_fd,
            )
            os.fsync(root_fd)
        except BaseException:
            if descriptor >= 0:
                os.close(descriptor)
            try:
                os.unlink(temporary_name, dir_fd=root_fd)
            except FileNotFoundError:
                pass
            raise
        return self.describe(name, expected_media_type=media_type, demo=demo)

    def write_json(
        self,
        name: str,
        value: dict[str, JsonValue],
        *,
        demo: bool = False,
    ) -> ArtifactManifestEntry:
        data = (
            json.dumps(
                value,
                ensure_ascii=True,
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n"
        ).encode("utf-8")
        return self.write_bytes(name, data, demo=demo)

    def read_bytes(self, name: str) -> bytes:
        self._validate_supported_name(name)
        root_fd = self._require_root_fd()
        flags = (
            os.O_RDONLY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NONBLOCK", 0)
            | getattr(os, "O_NOFOLLOW", 0)
        )
        try:
            descriptor = os.open(name, flags, dir_fd=root_fd)
        except FileNotFoundError:
            raise
        except OSError as exc:
            raise ArtifactPathError("artifact could not be opened safely") from exc
        try:
            file_stat = os.fstat(descriptor)
            if not stat.S_ISREG(file_stat.st_mode):
                raise ArtifactPathError("artifact must be a regular file")
            if file_stat.st_size > MAX_ARTIFACT_BYTES:
                raise ArtifactSizeError("artifact exceeds maximum byte size")
            chunks: list[bytes] = []
            total = 0
            while chunk := os.read(descriptor, 64 * 1024):
                total += len(chunk)
                if total > MAX_ARTIFACT_BYTES:
                    raise ArtifactSizeError("artifact exceeds maximum byte size")
                chunks.append(chunk)
            return b"".join(chunks)
        finally:
            os.close(descriptor)

    def describe(
        self,
        name: str,
        *,
        expected_media_type: MediaType | None = None,
        demo: bool = False,
    ) -> ArtifactManifestEntry:
        media_type = self._validate_supported_name(name)
        if expected_media_type is not None and media_type != expected_media_type:
            raise UnsupportedArtifactError("artifact media type changed")
        data = self.read_bytes(name)
        return ArtifactManifestEntry(
            name=name,
            media_type=media_type,
            size=len(data),
            sha256=hashlib.sha256(data).hexdigest(),
            demo=demo,
        )

    def exists(self, name: str) -> bool:
        self._validate_supported_name(name)
        try:
            file_stat = os.stat(
                name,
                dir_fd=self._require_root_fd(),
                follow_symlinks=False,
            )
        except FileNotFoundError:
            return False
        return stat.S_ISREG(file_stat.st_mode)

    def matches_root(self, path: Path) -> bool:
        flags = (
            os.O_RDONLY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_NOFOLLOW", 0)
        )
        try:
            descriptor = os.open(path, flags)
        except OSError:
            return False
        try:
            expected = os.fstat(self._require_root_fd())
            actual = os.fstat(descriptor)
            return (expected.st_dev, expected.st_ino) == (actual.st_dev, actual.st_ino)
        finally:
            os.close(descriptor)

    def delete(self, name: str, *, missing_ok: bool = True) -> None:
        self._validate_supported_name(name)
        try:
            os.unlink(name, dir_fd=self._require_root_fd())
            os.fsync(self._require_root_fd())
        except FileNotFoundError:
            if not missing_ok:
                raise

    def delete_many(self, names: list[str]) -> None:
        for name in names:
            self.delete(name, missing_ok=True)

    def close(self) -> None:
        if self._root_fd is not None:
            os.close(self._root_fd)
            self._root_fd = None

    def __enter__(self) -> "ArtifactStore":
        self._require_root_fd()
        return self

    def __exit__(
        self,
        _exc_type: object,
        _exc_value: object,
        _traceback: object,
    ) -> None:
        self.close()

    def __del__(self) -> None:
        try:
            self.close()
        except OSError:
            pass

    def _validate_supported_name(self, name: str) -> MediaType:
        try:
            validate_artifact_name(name)
        except ValueError as exc:
            raise ArtifactPathError(str(exc)) from exc
        try:
            return media_type_for_name(name)
        except ValueError as exc:
            raise UnsupportedArtifactError(str(exc)) from exc

    def _require_root_fd(self) -> int:
        if self._root_fd is None:
            raise RuntimeError("artifact store is closed")
        return self._root_fd
