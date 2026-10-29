"""Первая схема PostgreSQL: аккаунты, поездки, каталог городов и мест

Revision ID: 0001
Revises:
Create Date: 2026-10-29
"""
from alembic import op
import sqlalchemy as sa

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

TS = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.BigInteger, sa.Identity(), primary_key=True),
        sa.Column("email", sa.Text, nullable=False, unique=True),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("password", sa.Text, nullable=False),
        sa.Column("created", TS, nullable=False),
    )
    for name in ("sessions", "resets"):
        op.create_table(
            name,
            sa.Column("token", sa.Text, primary_key=True),
            sa.Column("user_id", sa.BigInteger, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
            sa.Column("created", TS, nullable=False),
            sa.Column("expires", TS, nullable=False),
            *([sa.Column("used", TS)] if name == "resets" else []),
        )
        op.create_index(f"ix_{name}_user_id", name, ["user_id"])
    op.create_index("ix_sessions_expires", "sessions", ["expires"])
    op.create_table(
        "trips",
        sa.Column("id", sa.Text, primary_key=True),
        sa.Column("user_id", sa.BigInteger, sa.ForeignKey("users.id", ondelete="CASCADE")),
        sa.Column("data", sa.JSON, nullable=False),
        sa.Column("created", TS, nullable=False),
        sa.Column("updated", TS, nullable=False),
    )
    op.create_index("trips_user_updated", "trips", ["user_id", "updated"])
    op.create_table(
        "cities",
        sa.Column("file", sa.Text, primary_key=True),
        sa.Column("data", sa.JSON, nullable=False),
    )
    op.create_table(
        "places",
        sa.Column("city_file", sa.Text, sa.ForeignKey("cities.file", ondelete="CASCADE"), primary_key=True),
        sa.Column("id", sa.Text, primary_key=True),
        sa.Column("pos", sa.BigInteger, nullable=False),
        sa.Column("data", sa.JSON, nullable=False),
    )


def downgrade() -> None:
    for name in ("places", "cities", "trips", "resets", "sessions", "users"):
        op.drop_table(name)
