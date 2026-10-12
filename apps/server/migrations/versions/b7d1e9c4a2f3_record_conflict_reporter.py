"""record the developer who reported each conflict"""

import sqlalchemy as sa
from alembic import op

revision = "b7d1e9c4a2f3"
down_revision = "3e4fd1a568f1"
branch_labels = None
depends_on = None


def upgrade():
    # Nullable on purpose: conflicts created before this revision stay owner-resolved only.
    if op.get_bind().dialect.name == "sqlite":
        # SQLite cannot ALTER constraints, so its local database gets the column alone.
        op.add_column("conflicts", sa.Column("reported_by", sa.String(length=36), nullable=True))
    else:
        op.add_column(
            "conflicts",
            sa.Column(
                "reported_by", sa.String(length=36), sa.ForeignKey("users.id"), nullable=True
            ),
        )


def downgrade():
    op.drop_column("conflicts", "reported_by")
