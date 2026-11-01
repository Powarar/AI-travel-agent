"""Persist trip state, interrupted runs, answers, and checkout keys."""

from alembic import op

revision = "0003_trip_runs"
down_revision = "0002_observation_provenance"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE trips (
            trip_id uuid PRIMARY KEY,
            state jsonb NOT NULL,
            version integer NOT NULL
        )
    """)
    op.execute("""
        CREATE TABLE trip_versions (
            trip_id uuid NOT NULL REFERENCES trips(trip_id) ON DELETE CASCADE,
            version integer NOT NULL,
            state jsonb NOT NULL,
            PRIMARY KEY (trip_id, version)
        )
    """)
    op.execute("""
        CREATE TABLE runs (
            run_id uuid PRIMARY KEY,
            trip_id uuid NOT NULL REFERENCES trips(trip_id) ON DELETE CASCADE,
            status text NOT NULL,
            awaiting text[] NOT NULL DEFAULT '{}',
            events jsonb NOT NULL DEFAULT '[]',
            message text NOT NULL DEFAULT ''
        )
    """)
    op.execute("""
        CREATE TABLE run_answers (
            run_id uuid NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
            field text NOT NULL,
            value text NOT NULL,
            PRIMARY KEY (run_id, field)
        )
    """)
    op.execute("""
        CREATE TABLE checkout_orders (
            trip_id uuid NOT NULL REFERENCES trips(trip_id) ON DELETE CASCADE,
            idempotency_key text NOT NULL,
            order_id text NOT NULL,
            PRIMARY KEY (trip_id, idempotency_key)
        )
    """)


def downgrade() -> None:
    for table in ("checkout_orders", "run_answers", "runs", "trip_versions", "trips"):
        op.execute(f"DROP TABLE {table}")
