"""Store observation provenance on each catalog row."""

from alembic import op

revision = "0002_observation_provenance"
down_revision = "0001_catalog"
branch_labels = None
depends_on = None

TABLES = (
    "destinations",
    "airports",
    "hotels",
    "stay_rates",
    "transport_legs",
    "transfers",
    "climate",
)


def upgrade() -> None:
    for table in TABLES:
        op.execute(f"ALTER TABLE {table} ADD COLUMN fetched_at timestamptz")
        op.execute(f"ALTER TABLE {table} ADD COLUMN synthetic boolean")
        op.execute(
            f"UPDATE {table} AS row SET fetched_at = source.fetched_at, "
            f"synthetic = (row.data_status = 'synthetic') "
            f"FROM sources AS source WHERE row.source_id = source.source_id"
        )
        op.execute(f"ALTER TABLE {table} ALTER COLUMN fetched_at SET NOT NULL")
        op.execute(f"ALTER TABLE {table} ALTER COLUMN synthetic SET NOT NULL")


def downgrade() -> None:
    for table in reversed(TABLES):
        op.execute(f"ALTER TABLE {table} DROP COLUMN synthetic")
        op.execute(f"ALTER TABLE {table} DROP COLUMN fetched_at")
