"""Drop the HNSW index: measured slower and lossy for the filtered candidate set.

Retrieval scores a pre-filtered candidate set (one destination, availability and
min_nights already applied) — about 40 of 1318 documents. Measured on that shape:

    exact filtered scan (the shipped query)   1.7 ms, all 40 candidates scored
    HNSW ORDER BY <=> LIMIT 10, no filter    54.5 ms, 5 of 10 results admissible

Adding ORDER BY/LIMIT to the shipped query does not help either: the planner keeps
the primary-key scan plus a top-N sort, because entity_id=ANY(...) is far more
selective than the vector index. The index was never used, and using it would
trade exact recall for a 32x slower plan.

ANN becomes the right tool when the candidate set stops being selective — roughly
when admissible IDs approach the whole corpus. Re-create it together with a query
that has no entity_id filter, not before.
"""

from alembic import op

revision = "0012_drop_unused_hnsw"
down_revision = "0011_users_auth"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("DROP INDEX IF EXISTS search_documents_vector")


def downgrade() -> None:
    op.execute("CREATE INDEX search_documents_vector ON search_documents USING hnsw (embedding vector_cosine_ops)")