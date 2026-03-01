"""
Resume service — single source of truth for resume YAML.

Responsibilities:
- Read / write the current resume YAML (filesystem, per user)
- Persist every meaningful change as a version in the DB
- Restore from a previous version

Security notes:
- yaml_content contains PII (name, email, phone). It is the user's own data
  they explicitly chose to store. Never expose one user's data to another.
- All functions require user_id; it is used both as the filesystem path
  component and as the DB foreign key, preventing cross-user access.
- yaml.safe_load is used everywhere — never yaml.load — to prevent
  arbitrary code execution via crafted YAML.
"""

import os
import yaml

from models import (
    get_user_dir,
    save_resume_version,
    list_resume_versions,
    get_resume_version,
    get_latest_resume_version,
    update_version_tags,
    DATA_DIR,
)


def _resume_path(user_id: int) -> str:
    return os.path.join(get_user_dir(user_id), 'resume.yaml')


# ---------------------------------------------------------------------------
# Current resume (filesystem)
# ---------------------------------------------------------------------------

def get_current_resume(user_id: int) -> str | None:
    """Return the raw YAML string for the user's current resume, or None."""
    path = _resume_path(user_id)
    if not os.path.exists(path):
        return None
    with open(path, 'r', encoding='utf-8') as f:
        return f.read()


def save_current_resume(
    user_id: int,
    yaml_content: str,
    source: str = 'manual_edit',
    label: str | None = None,
    tags: list | None = None,
) -> int:
    """Write the canonical resume file and snapshot it in the DB.

    Args:
        user_id:      Owning user.
        yaml_content: Raw YAML string. Validated before writing.
        source:       One of 'upload' | 'manual_edit' | 'jd_applied' | 'ai_edit'.
        label:        Optional human-readable label for the version.
        tags:         Optional list of keyword strings (e.g. ["python", "backend"]) used
                      by the JD-matching feature to select the best version for a given JD.

    Returns:
        The new resume_versions.id (version id).

    Raises:
        ValueError: if yaml_content is not valid YAML.
    """
    _validate_yaml(yaml_content)

    user_dir = get_user_dir(user_id)
    os.makedirs(user_dir, exist_ok=True)

    path = _resume_path(user_id)
    with open(path, 'w', encoding='utf-8') as f:
        f.write(yaml_content)

    return save_resume_version(user_id, yaml_content, source=source, label=label, tags=tags)


def tag_version(version_id: int, user_id: int, tags: list) -> None:
    """Update tags on an existing resume version (replaces previous tags).

    Args:
        tags: list of keyword strings, e.g. ["python", "ml", "backend"].
    """
    update_version_tags(version_id, user_id, tags)


# ---------------------------------------------------------------------------
# Versioning
# ---------------------------------------------------------------------------

def list_versions(user_id: int) -> list[dict]:
    """Return version metadata for all resume snapshots, newest first.

    Does NOT include yaml_content — call get_version() for the full payload.
    """
    return list_resume_versions(user_id)


def get_version(version_id: int, user_id: int) -> dict | None:
    """Return a full version dict including yaml_content.

    user_id is enforced as a security guard — users can only fetch their own versions.
    """
    return get_resume_version(version_id, user_id)


def restore_version(version_id: int, user_id: int) -> str:
    """Restore a previous version as the current resume.

    Saves a new version row with source='manual_edit' so the restore itself
    is recorded in history. Returns the restored YAML string.

    Raises:
        ValueError: if version_id does not belong to user_id.
    """
    version = get_resume_version(version_id, user_id)
    if not version:
        raise ValueError(f"Version {version_id} not found for user {user_id}")

    yaml_content = version['yaml_content']
    save_current_resume(user_id, yaml_content, source='manual_edit',
                        label=f'Restored from version {version_id}')
    return yaml_content


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _validate_yaml(yaml_content: str) -> None:
    """Raise ValueError if yaml_content is not valid YAML (uses safe_load)."""
    try:
        yaml.safe_load(yaml_content)
    except yaml.YAMLError as e:
        raise ValueError(f"Invalid YAML: {e}") from e


def parse_yaml(yaml_content: str) -> dict:
    """Safely parse a YAML string to a dict. Returns {} on empty input."""
    if not yaml_content or not yaml_content.strip():
        return {}
    result = yaml.safe_load(yaml_content)
    return result if isinstance(result, dict) else {}


def dump_yaml(data: dict) -> str:
    """Dump a dict to a YAML string with safe defaults."""
    return yaml.dump(data, allow_unicode=True, default_flow_style=False, sort_keys=False)
