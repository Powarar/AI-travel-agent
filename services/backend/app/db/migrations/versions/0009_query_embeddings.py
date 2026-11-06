"""Cache query vectors by model and content hash; no raw query is retained here."""
from alembic import op

revision = "0009_query_embeddings"
down_revision = "0008_hotel_descriptions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""CREATE TABLE query_embeddings (
        model text NOT NULL, content_hash text NOT NULL, embedding vector(1024) NOT NULL,
        created_at timestamptz NOT NULL DEFAULT now(), PRIMARY KEY(model, content_hash)
    )""")


def downgrade() -> None:
    op.execute("DROP TABLE query_embeddings")
