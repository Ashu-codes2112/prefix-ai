"""A hospital's cancellation rate before Prefix AI, so Analytics can estimate what was avoided.

Revision ID: 0011
Revises: 0010
Create Date: 2026-11-17 16:40:45.734334
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "organization",
        sa.Column("baseline_cancellation_rate", sa.Numeric(precision=5, scale=2), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("organization", "baseline_cancellation_rate")
