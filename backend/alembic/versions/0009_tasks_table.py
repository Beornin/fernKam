"""tasks_table

Revision ID: 0009
Revises: 0008
Create Date: 2026-06-13

Adds a persistent tasks table for tracking background operations.
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision: str = '0009'
down_revision: Union[str, None] = '0008'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'tasks',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('task_type', sa.String(64), nullable=False),
        sa.Column('status', sa.String(32), nullable=False, server_default='running'),
        sa.Column('message', sa.Text, nullable=False, server_default=''),
        sa.Column('started_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('progress', JSONB, nullable=True),
    )
    op.create_index('ix_tasks_status', 'tasks', ['status'])
    op.create_index('ix_tasks_started_at', 'tasks', ['started_at'])


def downgrade() -> None:
    op.drop_index('ix_tasks_started_at', table_name='tasks')
    op.drop_index('ix_tasks_status', table_name='tasks')
    op.drop_table('tasks')
