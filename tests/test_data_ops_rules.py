"""The data & ops referential (DA-022, OP-000, OP-040), proven against real files.

Three deterministic rules that land in ``info`` mode under governance rule GV-020
(a new automated check lands as info, then warning, then error only once the debt
is cleared). Each rule gets a passing fixture and a failing one, and every finding
is asserted to carry ``severity == "info"`` — the rules must never block.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from guideline_checker.checker import Violation, run_checks

REFERENTIAL = Path(__file__).resolve().parents[1] / "guidelines"


@pytest.fixture
def project(tmp_path: Path) -> Path:
    """A project carrying the shipped referential (read from the scanned root)."""
    shutil.copytree(REFERENTIAL, tmp_path / "guidelines")
    return tmp_path


def _violations_for(root: Path, filename: str) -> list[Violation]:
    return [v for result in run_checks(root, all_sources=True) for v in result.violations if v.file.name == filename]


# --------------------------------------------------------------------------- #
# DA-022 — a destructive DB change must ship with a migration plan
# --------------------------------------------------------------------------- #


def test_da022_flags_a_sql_drop_table(project: Path) -> None:
    sql = project / "schema.sql"
    sql.write_text("DROP TABLE accounts;\n", encoding="utf-8")

    found = _violations_for(project, "schema.sql")

    assert len(found) == 1
    assert found[0].severity == "info"


def test_da022_flags_an_alembic_op_drop_column(project: Path) -> None:
    migrations = project / "migrations"
    migrations.mkdir()
    (migrations / "0001_drop.py").write_text(
        'def upgrade():\n    op.drop_column("users", "legacy")\n',
        encoding="utf-8",
    )

    found = _violations_for(project, "0001_drop.py")

    assert len(found) == 1
    assert found[0].severity == "info"


def test_da022_ignores_an_additive_migration(project: Path) -> None:
    migrations = project / "migrations"
    migrations.mkdir()
    (migrations / "0002_add.py").write_text(
        'def upgrade():\n    op.add_column("users", sa.Column("email", sa.String()))\n',
        encoding="utf-8",
    )

    assert _violations_for(project, "0002_add.py") == []


# --------------------------------------------------------------------------- #
# OP-000 — a deployable service declares startup, liveness and readiness probes
# --------------------------------------------------------------------------- #


def test_op000_passes_a_compose_with_healthcheck(project: Path) -> None:
    (project / "docker-compose.yml").write_text(
        "services:\n"
        "  api:\n"
        "    image: app\n"
        "    healthcheck:\n"
        '      test: ["CMD", "curl", "-f", "http://localhost:8000/health"]\n',
        encoding="utf-8",
    )

    assert _violations_for(project, "docker-compose.yml") == []


def test_op000_flags_a_compose_without_a_probe(project: Path) -> None:
    (project / "docker-compose.yml").write_text(
        "services:\n  api:\n    image: app\n",
        encoding="utf-8",
    )

    found = _violations_for(project, "docker-compose.yml")

    assert len(found) == 1
    assert found[0].severity == "info"


# --------------------------------------------------------------------------- #
# OP-040 — the service publishes a /version endpoint
# --------------------------------------------------------------------------- #


def test_op040_passes_an_entrypoint_with_a_version_route(project: Path) -> None:
    (project / "main.py").write_text(
        '@app.get("/version")\nasync def version():\n    return {"version": "1.0"}\n',
        encoding="utf-8",
    )

    assert _violations_for(project, "main.py") == []


def test_op040_flags_an_entrypoint_without_a_version_route(project: Path) -> None:
    (project / "main.py").write_text(
        '@app.get("/health")\nasync def health():\n    return {"ok": True}\n',
        encoding="utf-8",
    )

    found = _violations_for(project, "main.py")

    assert len(found) == 1
    assert found[0].severity == "info"


# --------------------------------------------------------------------------- #
# OP-023 — a dashboard definition declares a stable uid and schema version
# --------------------------------------------------------------------------- #


def test_op023_passes_a_dashboard_with_uid_and_schema(project: Path) -> None:
    d = project / "dashboards"
    d.mkdir()
    (d / "core.json").write_text(
        '{\n  "uid": "core-overview",\n  "schemaVersion": 39,\n  "title": "Core"\n}\n',
        encoding="utf-8",
    )
    assert _violations_for(project, "core.json") == []


def test_op023_flags_a_dashboard_missing_uid(project: Path) -> None:
    d = project / "dashboards"
    d.mkdir()
    (d / "core.json").write_text(
        '{\n  "schemaVersion": 39,\n  "title": "Core"\n}\n',
        encoding="utf-8",
    )
    found = _violations_for(project, "core.json")
    assert len(found) == 1
    assert found[0].severity == "info"


def test_op023_ignores_unrelated_json(project: Path) -> None:
    (project / "package.json").write_text('{\n  "name": "app"\n}\n', encoding="utf-8")
    assert _violations_for(project, "package.json") == []
