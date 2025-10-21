from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.oracle import CLOB


# revision identifiers, used by Alembic.
revision = '0001_initial_users_feedback'
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'users',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('email', sa.String(length=320), nullable=False),
        sa.Column('name', sa.String(length=255), nullable=True),
        sa.Column('role', sa.String(length=50), nullable=False, server_default='user'),
        sa.Column('status', sa.String(length=32), nullable=False, server_default='invited'),
        sa.Column('auth_provider', sa.String(length=20), nullable=False, server_default='local'),
        sa.Column('password_hash', sa.String(length=255), nullable=True),
        sa.Column('password_algo', sa.String(length=20), nullable=False, server_default='bcrypt'),
        sa.Column('password_updated_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('auth_sub', sa.String(length=255), nullable=True),
        sa.Column('auth_issuer', sa.String(length=255), nullable=True),
        sa.Column('last_login_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_unique_constraint('uq_users_email', 'users', ['email'])

    # Feedback table
    json_type = sa.Text
    op.create_table(
        'feedback',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id'), nullable=True),
        sa.Column('session_id', sa.String(length=255), nullable=True),
        sa.Column('rating', sa.Integer(), nullable=True),
        sa.Column('category', sa.String(length=100), nullable=True),
        sa.Column('comment', CLOB, nullable=True),
        sa.Column('metadata', json_type, nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index('ix_feedback_user_created', 'feedback', ['user_id', 'created_at'])
    op.create_index('ix_feedback_created', 'feedback', ['created_at'])


def downgrade() -> None:
    op.drop_index('ix_feedback_created', table_name='feedback')
    op.drop_index('ix_feedback_user_created', table_name='feedback')
    op.drop_table('feedback')
    op.drop_constraint('uq_users_email', 'users', type_='unique')
    op.drop_table('users')
    # Note: Runtime models use IDENTITY; no sequence cleanup required.
"""
NOTE: Runtime models now use Oracle IDENTITY columns for primary keys
instead of sequences. If your database already has IDENTITY on users.id
and feedback.id, this migration's sequence-related statements may be
superseded by the current DB state.
"""
