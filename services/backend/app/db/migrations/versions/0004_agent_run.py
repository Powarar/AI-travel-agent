"""Serialized agent run next to the HTTP run: an interrupted turn resumes after restart."""

from alembic import op

revision = "0004_agent_run"
down_revision = "0003_trip_runs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE runs ADD COLUMN agent_run text")


def downgrade() -> None:
    op.execute("ALTER TABLE runs DROP COLUMN agent_run")
