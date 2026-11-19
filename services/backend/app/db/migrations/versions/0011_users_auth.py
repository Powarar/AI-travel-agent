"""Registered users, rotating refresh tokens, and trip ownership."""

from alembic import op

revision = "0011_users_auth"
down_revision = "0010_evaluation_runs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE users (
            id uuid PRIMARY KEY,
            email text NOT NULL UNIQUE,
            password_hash text NOT NULL,
            telegram_id bigint UNIQUE,
            created_at timestamptz NOT NULL DEFAULT now()
        )
    """)
    op.execute("""
        CREATE TABLE refresh_tokens (
            jti uuid PRIMARY KEY,
            user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            expires_at timestamptz NOT NULL,
            revoked_at timestamptz,
            created_at timestamptz NOT NULL DEFAULT now()
        )
    """)
    op.execute("CREATE INDEX refresh_tokens_user_id ON refresh_tokens(user_id)")
    op.execute("ALTER TABLE trips ADD COLUMN owner text")
    op.execute("ALTER TABLE trips ADD COLUMN created_at timestamptz NOT NULL DEFAULT now()")
    # Pre-auth trips have no real owner; a sentinel keeps the column NOT NULL while
    # making those rows unreachable (no caller can present the sentinel owner).
    op.execute("UPDATE trips SET owner = 'legacy:unassigned' WHERE owner IS NULL")
    op.execute("ALTER TABLE trips ALTER COLUMN owner SET NOT NULL")
    op.execute("CREATE INDEX trips_owner_created ON trips(owner, created_at DESC)")


def downgrade() -> None:
    op.execute("DROP INDEX trips_owner_created")
    op.execute("ALTER TABLE trips DROP COLUMN owner")
    op.execute("ALTER TABLE trips DROP COLUMN created_at")
    op.execute("DROP INDEX refresh_tokens_user_id")
    op.execute("DROP TABLE refresh_tokens")
    op.execute("DROP TABLE users")