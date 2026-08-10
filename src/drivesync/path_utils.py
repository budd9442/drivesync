import fnmatch
import os
import pathlib
from typing import List, Optional, Tuple


def normalize_local_path(path: str | pathlib.Path) -> str:
    """Canonicalize Windows path to absolute resolved string with normalized case."""
    p = pathlib.Path(path).resolve()
    # Return string representation of resolved path
    return str(p)


def get_relative_path(full_path: str, root_path: str) -> Optional[str]:
    """Get normalized relative path from root path."""
    try:
        norm_full = pathlib.Path(full_path).resolve()
        norm_root = pathlib.Path(root_path).resolve()
        rel = norm_full.relative_to(norm_root)
        # Convert backslashes to forward slashes for cross-platform/remote consistency
        return rel.as_posix()
    except ValueError:
        return None


def to_remote_path(remote_root: str, rel_path: str) -> str:
    """Combine remote root folder path with relative file path using forward slashes."""
    clean_root = remote_root.strip("/")
    clean_rel = rel_path.strip("/")
    if clean_root:
        return f"{clean_root}/{clean_rel}"
    return clean_rel


def is_ignored(full_path: str | pathlib.Path, ignore_patterns: List[str]) -> bool:
    """Check if file matches any ignore pattern (e.g. temp files, ~$*.docx)."""
    p = pathlib.Path(full_path)
    name = p.name
    posix_path = p.as_posix()

    for pattern in ignore_patterns:
        if fnmatch.fnmatch(name, pattern) or fnmatch.fnmatch(posix_path, pattern):
            return True
        # Also check relative pattern match
        if "/" in pattern and fnmatch.fnmatch(name, pattern.split("/")[-1]):
            return True

    return False
