import os
import secrets
import stat
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


class UnsafePathError(OSError):
    pass


def _directory_flags() -> int:
    required = ("O_DIRECTORY", "O_NOFOLLOW")
    if any(not hasattr(os, name) for name in required) or os.open not in os.supports_dir_fd:
        raise UnsafePathError("secure descriptor-relative traversal is unavailable")
    return (
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | os.O_DIRECTORY
        | os.O_NOFOLLOW
    )


def _validate_components(components: tuple[str, ...]) -> tuple[str, ...]:
    if not components:
        raise UnsafePathError("path must contain at least one component")
    for component in components:
        if (
            not component
            or component in {".", ".."}
            or "/" in component
            or "\\" in component
            or "\x00" in component
        ):
            raise UnsafePathError("path component is invalid")
    return components


def _regular_file_flags() -> int:
    if (
        not hasattr(os, "O_NOFOLLOW")
        or os.open not in os.supports_dir_fd
        or os.unlink not in os.supports_dir_fd
    ):
        raise UnsafePathError("secure descriptor-relative file access is unavailable")
    return getattr(os, "O_CLOEXEC", 0) | os.O_NOFOLLOW


def open_directory_fd(path: Path, *, create: bool = False) -> int:
    absolute = Path(os.path.abspath(path))
    if absolute.anchor != "/":
        raise UnsafePathError("secure absolute path traversal is unavailable")
    flags = _directory_flags()
    current_fd = os.open("/", flags)
    try:
        for component in absolute.parts[1:]:
            if not component or component in {".", ".."}:
                raise UnsafePathError("directory path is invalid")
            if create:
                try:
                    os.mkdir(component, mode=0o700, dir_fd=current_fd)
                except FileExistsError:
                    pass
            next_fd = os.open(component, flags, dir_fd=current_fd)
            os.close(current_fd)
            current_fd = next_fd
        return current_fd
    except BaseException:
        os.close(current_fd)
        raise


def open_relative_directory_fd(
    root_fd: int,
    components: tuple[str, ...],
    *,
    create: bool = False,
    mode: int = 0o700,
) -> int:
    validated = _validate_components(components)
    flags = _directory_flags()
    current_fd = os.dup(root_fd)
    try:
        for component in validated:
            if create:
                try:
                    os.mkdir(component, mode=mode, dir_fd=current_fd)
                except FileExistsError:
                    pass
            next_fd = os.open(component, flags, dir_fd=current_fd)
            os.close(current_fd)
            current_fd = next_fd
        return current_fd
    except BaseException:
        os.close(current_fd)
        raise


def create_regular_file_fd(
    directory_fd: int,
    name: str,
    *,
    exclusive: bool,
    mode: int = 0o600,
) -> int:
    _validate_components((name,))
    flags = os.O_WRONLY | os.O_CREAT | _regular_file_flags()
    flags |= os.O_EXCL if exclusive else os.O_TRUNC
    descriptor = os.open(name, flags, mode, dir_fd=directory_fd)
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise UnsafePathError("opened file is not regular")
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def open_regular_file_fd(root_fd: int, components: tuple[str, ...]) -> int:
    validated = _validate_components(components)
    parent_fd = (
        open_relative_directory_fd(root_fd, validated[:-1])
        if len(validated) > 1
        else os.dup(root_fd)
    )
    try:
        descriptor = os.open(
            validated[-1],
            os.O_RDONLY | _regular_file_flags(),
            dir_fd=parent_fd,
        )
    finally:
        os.close(parent_fd)
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise UnsafePathError("opened file is not regular")
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def write_all(descriptor: int, data: bytes) -> None:
    remaining = memoryview(data)
    while remaining:
        written = os.write(descriptor, remaining)
        if written <= 0:
            raise OSError("file write made no progress")
        remaining = remaining[written:]


def unlink_regular_file_at(
    directory_fd: int,
    name: str,
    *,
    missing_ok: bool = True,
) -> None:
    _validate_components((name,))
    try:
        metadata = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
        if not stat.S_ISREG(metadata.st_mode):
            raise UnsafePathError("unlink target is not a regular file")
        os.unlink(name, dir_fd=directory_fd)
    except FileNotFoundError:
        if not missing_ok:
            raise


def copy_regular_file_at(
    source_root_fd: int,
    source_components: tuple[str, ...],
    destination_fd: int,
    destination_name: str,
) -> int:
    _validate_components((destination_name,))
    if os.rename not in os.supports_dir_fd:
        raise UnsafePathError("secure descriptor-relative replacement is unavailable")
    source_fd = open_regular_file_fd(source_root_fd, source_components)
    temporary_name = f".tmp-{secrets.token_hex(16)}"
    destination_file_fd = -1
    try:
        destination_file_fd = create_regular_file_fd(
            destination_fd,
            temporary_name,
            exclusive=True,
        )
        while chunk := os.read(source_fd, 1024 * 1024):
            write_all(destination_file_fd, chunk)
        os.fsync(destination_file_fd)
        size = os.fstat(destination_file_fd).st_size
        os.close(destination_file_fd)
        destination_file_fd = -1
        os.rename(
            temporary_name,
            destination_name,
            src_dir_fd=destination_fd,
            dst_dir_fd=destination_fd,
        )
        os.fsync(destination_fd)
        return size
    except BaseException:
        if destination_file_fd >= 0:
            os.close(destination_file_fd)
        try:
            unlink_regular_file_at(destination_fd, temporary_name)
        except FileNotFoundError:
            pass
        raise
    finally:
        os.close(source_fd)


def relative_path_components(root: Path, path: Path) -> tuple[str, ...]:
    absolute_root = Path(os.path.abspath(root))
    absolute_path = Path(os.path.abspath(path))
    try:
        relative = absolute_path.relative_to(absolute_root)
    except ValueError as exc:
        raise UnsafePathError("path is outside the trusted root") from exc
    return _validate_components(relative.parts)


@contextmanager
def opened_directory(path: Path, *, create: bool = False) -> Iterator[int]:
    descriptor = open_directory_fd(path, create=create)
    try:
        yield descriptor
    finally:
        os.close(descriptor)


@contextmanager
def opened_relative_directory(
    root_fd: int,
    components: tuple[str, ...],
    *,
    create: bool = False,
) -> Iterator[int]:
    descriptor = open_relative_directory_fd(root_fd, components, create=create)
    try:
        yield descriptor
    finally:
        os.close(descriptor)
