"""Replace the legacy platform roles with the four-role business model.

Revision ID: 0005_four_role_access_model
Revises: 0004_approvals_notifications
Create Date: 2026-08-08
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0005_four_role_access_model"
down_revision: str | None = "0004_approvals_notifications"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_ROLE_DETAILS = {
    "SERVICE_ADMINISTRATOR": (
        "Service Administrator",
        "Administers the platform, users, Planning cycles, schedules, and governed Oracle EPM operations.",
    ),
    "POWER_USER": (
        "Power User",
        "Runs approved Planning processes and operations, reviews data, and monitors execution without administering users or design.",
    ),
    "USER": (
        "User",
        "Completes assigned Planning work, reviews authorized data, runs approved processes, and generates reports.",
    ),
    "VIEWER": (
        "Viewer",
        "Consumes authorized Planning reports and read-only agent guidance without operational controls.",
    ),
}

_ROLE_PERMISSIONS = {
    "SERVICE_ADMINISTRATOR": (
        "agent.use",
        "data.review",
        "history.view",
        "operation.execute",
        "process.design",
        "process.run",
        "report.generate",
        "schedule.manage",
        "user.manage",
        "variable.update",
    ),
    "POWER_USER": (
        "agent.use",
        "data.review",
        "history.view",
        "operation.execute",
        "process.run",
        "report.generate",
    ),
    "USER": (
        "agent.use",
        "data.review",
        "process.run",
        "report.generate",
    ),
    "VIEWER": ("agent.use", "report.generate"),
}


def upgrade() -> None:
    connection = op.get_bind()

    for code, (name, description) in _ROLE_DETAILS.items():
        connection.execute(
            sa.text(
                """
                INSERT INTO platform_roles (code, name, description, is_system)
                VALUES (:code, :name, :description, true)
                ON CONFLICT (code) DO UPDATE
                SET name = EXCLUDED.name,
                    description = EXCLUDED.description,
                    is_system = true
                """
            ),
            {"code": code, "name": name, "description": description},
        )

    role_mapping = {
        "PLATFORM_ADMINISTRATOR": "SERVICE_ADMINISTRATOR",
        # Preserve least privilege: a former consultant is not silently given
        # user-administration authority during the catalog consolidation.
        "EPM_CONSULTANT": "POWER_USER",
        "PROCESS_OPERATOR": "POWER_USER",
        "PLANNER": "USER",
        "AUDITOR": "VIEWER",
    }
    for old_code, new_code in role_mapping.items():
        connection.execute(
            sa.text(
                """
                UPDATE planning_tasks
                SET assigned_role_id = target.role_id
                FROM platform_roles old_role, platform_roles target
                WHERE planning_tasks.assigned_role_id = old_role.role_id
                  AND old_role.code = :old_code
                  AND target.code = :new_code
                """
            ),
            {"old_code": old_code, "new_code": new_code},
        )
        connection.execute(
            sa.text(
                """
                INSERT INTO platform_user_roles (
                    user_id, role_id, assigned_at, assigned_by_user_id
                )
                SELECT assignment.user_id,
                       target.role_id,
                       assignment.assigned_at,
                       assignment.assigned_by_user_id
                FROM platform_user_roles assignment
                JOIN platform_roles old_role
                  ON old_role.role_id = assignment.role_id
                JOIN platform_roles target
                  ON target.code = :new_code
                WHERE old_role.code = :old_code
                ON CONFLICT (user_id, role_id) DO NOTHING
                """
            ),
            {"old_code": old_code, "new_code": new_code},
        )

    connection.execute(
        sa.text(
            """
            DELETE FROM platform_user_roles assignment
            USING platform_roles role
            WHERE assignment.role_id = role.role_id
              AND role.code IN (
                  'PLATFORM_ADMINISTRATOR', 'EPM_CONSULTANT',
                  'PROCESS_OPERATOR', 'PLANNER', 'AUDITOR'
              )
            """
        )
    )
    connection.execute(
        sa.text(
            """
            DELETE FROM platform_roles
            WHERE code IN (
                'PLATFORM_ADMINISTRATOR', 'EPM_CONSULTANT',
                'PROCESS_OPERATOR', 'PLANNER', 'AUDITOR'
            )
            """
        )
    )

    # A single primary role avoids conflicting personas and makes access reviews
    # deterministic. Existing multi-role users retain their highest privilege.
    connection.execute(
        sa.text(
            """
            DELETE FROM platform_user_roles lower_assignment
            USING platform_roles lower_role
            WHERE lower_assignment.role_id = lower_role.role_id
              AND EXISTS (
                  SELECT 1
                  FROM platform_user_roles higher_assignment
                  JOIN platform_roles higher_role
                    ON higher_role.role_id = higher_assignment.role_id
                  WHERE higher_assignment.user_id = lower_assignment.user_id
                    AND CASE higher_role.code
                          WHEN 'SERVICE_ADMINISTRATOR' THEN 1
                          WHEN 'POWER_USER' THEN 2
                          WHEN 'USER' THEN 3
                          WHEN 'VIEWER' THEN 4
                          ELSE 99
                        END
                        < CASE lower_role.code
                            WHEN 'SERVICE_ADMINISTRATOR' THEN 1
                            WHEN 'POWER_USER' THEN 2
                            WHEN 'USER' THEN 3
                            WHEN 'VIEWER' THEN 4
                            ELSE 99
                          END
              )
            """
        )
    )

    connection.execute(
        sa.text(
            """
            DELETE FROM platform_role_permissions
            WHERE role_id IN (
                SELECT role_id FROM platform_roles
                WHERE code IN (
                    'SERVICE_ADMINISTRATOR', 'POWER_USER', 'USER', 'VIEWER'
                )
            )
            """
        )
    )
    for code, permissions in _ROLE_PERMISSIONS.items():
        for permission in permissions:
            connection.execute(
                sa.text(
                    """
                    INSERT INTO platform_role_permissions (role_id, permission_code)
                    SELECT role_id, :permission
                    FROM platform_roles
                    WHERE code = :code
                    ON CONFLICT (role_id, permission_code) DO NOTHING
                    """
                ),
                {"code": code, "permission": permission},
            )


def downgrade() -> None:
    """Restore legacy codes; merged historical assignments are not reversible."""
    connection = op.get_bind()
    renames = {
        "SERVICE_ADMINISTRATOR": ("PLATFORM_ADMINISTRATOR", "Platform Administrator"),
        "POWER_USER": ("PROCESS_OPERATOR", "Process Operator"),
        "USER": ("PLANNER", "Planner"),
    }
    for current_code, (old_code, old_name) in renames.items():
        connection.execute(
            sa.text(
                """
                UPDATE platform_roles
                SET code = :old_code, name = :old_name
                WHERE code = :current_code
                """
            ),
            {
                "current_code": current_code,
                "old_code": old_code,
                "old_name": old_name,
            },
        )

    for code, name, description in (
        ("EPM_CONSULTANT", "EPM Consultant", "Designs governed processes and performs Oracle EPM operations."),
        ("AUDITOR", "Auditor", "Read-only access to execution history and evidence."),
    ):
        connection.execute(
            sa.text(
                """
                INSERT INTO platform_roles (code, name, description, is_system)
                VALUES (:code, :name, :description, true)
                ON CONFLICT (code) DO NOTHING
                """
            ),
            {"code": code, "name": name, "description": description},
        )
