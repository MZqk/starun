import os
from pathlib import Path

from app import filesystem


def test_descriptor_anchoring_survives_parent_swap(tmp_path: Path) -> None:
    data_root = tmp_path / "data"
    outside = tmp_path / "outside"
    outside.mkdir()
    root_fd = filesystem.open_directory_fd(data_root, create=True)
    try:
        task_fd = filesystem.open_relative_directory_fd(
            root_fd,
            ("tasks", "anchored-task"),
            create=True,
        )
        try:
            moved_root = tmp_path / "moved-data"
            data_root.rename(moved_root)
            data_root.symlink_to(outside, target_is_directory=True)

            descriptor = filesystem.create_regular_file_fd(
                task_fd,
                "result.bin",
                exclusive=True,
            )
            try:
                os.write(descriptor, b"anchored")
            finally:
                os.close(descriptor)
        finally:
            os.close(task_fd)
    finally:
        os.close(root_fd)

    assert (moved_root / "tasks" / "anchored-task" / "result.bin").read_bytes() == b"anchored"
    assert list(outside.iterdir()) == []
