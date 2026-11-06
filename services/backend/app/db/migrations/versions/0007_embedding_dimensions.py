"""Pin embedding dimensions for qwen3-embedding-8b."""
from alembic import op

revision = "0007_embedding_dimensions"
down_revision = "0006_search_documents"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE search_documents ALTER COLUMN embedding TYPE vector(1024)")


def downgrade() -> None:
    op.execute("ALTER TABLE search_documents ALTER COLUMN embedding TYPE vector")
