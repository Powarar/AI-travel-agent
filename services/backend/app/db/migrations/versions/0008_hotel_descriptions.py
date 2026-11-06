"""Persist source descriptions for semantic search and index vector distances."""
from alembic import op

revision = "0008_hotel_descriptions"
down_revision = "0007_embedding_dimensions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE hotels ADD COLUMN description text NOT NULL DEFAULT ''")
    op.execute("CREATE INDEX search_documents_vector ON search_documents USING hnsw (embedding vector_cosine_ops)")


def downgrade() -> None:
    op.execute("DROP INDEX search_documents_vector")
    op.execute("ALTER TABLE hotels DROP COLUMN description")
