"""Persist labelled evaluation results separately from production telemetry."""
from alembic import op

revision = "0010_evaluation_runs"
down_revision = "0009_query_embeddings"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""CREATE TABLE evaluation_runs (
        eval_id uuid PRIMARY KEY,
        created_at timestamptz NOT NULL DEFAULT now(),
        dataset_id text NOT NULL,
        dataset_hash text NOT NULL,
        model text NOT NULL,
        prompt_version text NOT NULL,
        mode text NOT NULL CHECK (mode IN ('live', 'offline')),
        report jsonb NOT NULL
    )""")
    op.execute("CREATE INDEX evaluation_runs_latest ON evaluation_runs(dataset_id, model, created_at DESC)")


def downgrade() -> None:
    op.execute("DROP TABLE evaluation_runs")
