"""Versioned search documents and embeddings, updated by background ingestion."""
from alembic import op

revision = "0006_search_documents"
down_revision = "0005_order_owner"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE search_documents (
            entity_id text NOT NULL REFERENCES hotels(hotel_id) ON DELETE CASCADE,
            model text NOT NULL,
            content_hash text NOT NULL,
            document text NOT NULL,
            terms jsonb NOT NULL,
            doc_length integer NOT NULL CHECK (doc_length >= 0),
            embedding vector(1024),
            updated_at timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (entity_id, model)
        )
    """)
    op.execute("CREATE INDEX search_documents_terms ON search_documents USING gin(terms)")


def downgrade() -> None:
    op.execute("DROP TABLE search_documents")
