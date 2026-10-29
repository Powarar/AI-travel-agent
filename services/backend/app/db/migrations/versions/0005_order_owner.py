"""Orders belong to a user and keep a snapshot of the plan they were placed for."""

from alembic import op

revision = "0005_order_owner"
down_revision = "0004_agent_run"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE checkout_orders ADD COLUMN owner text")
    op.execute("ALTER TABLE checkout_orders ADD COLUMN created_at timestamptz NOT NULL DEFAULT now()")
    op.execute("ALTER TABLE checkout_orders ADD COLUMN state_version integer")
    op.execute("ALTER TABLE checkout_orders ADD COLUMN snapshot jsonb")
    op.execute("CREATE INDEX checkout_orders_owner ON checkout_orders(owner, created_at DESC)")


def downgrade() -> None:
    op.execute("DROP INDEX checkout_orders_owner")
    for column in ("snapshot", "state_version", "created_at", "owner"):
        op.execute(f"ALTER TABLE checkout_orders DROP COLUMN {column}")
