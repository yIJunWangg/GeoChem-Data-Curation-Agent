"""Add user quota, shared model credential, and usage governance tables.

Revision ID: 20260720_0002
Revises: 20260718_0001
"""

from alembic import op


revision = "20260720_0002"
down_revision = "20260718_0001"
branch_labels = None
depends_on = None


TABLES = (
    """CREATE TABLE IF NOT EXISTS user_profiles (
        user_id TEXT PRIMARY KEY, username TEXT NOT NULL DEFAULT '',
        display_name TEXT DEFAULT '', email TEXT DEFAULT '', status TEXT DEFAULT 'active',
        created_at TEXT NOT NULL, updated_at TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS model_credentials (
        credential_id TEXT PRIMARY KEY, name TEXT NOT NULL, provider TEXT NOT NULL,
        api_format TEXT DEFAULT 'openai', base_url TEXT DEFAULT '', model_id TEXT NOT NULL,
        secret_ref TEXT DEFAULT '', encrypted_secret TEXT DEFAULT '', secret_nonce TEXT DEFAULT '',
        key_fingerprint TEXT DEFAULT '', enabled INTEGER DEFAULT 1, created_by TEXT DEFAULT '',
        created_at TEXT NOT NULL, updated_at TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS user_model_allocations (
        allocation_id TEXT PRIMARY KEY, user_id TEXT NOT NULL,
        credential_id TEXT NOT NULL REFERENCES model_credentials(credential_id),
        enabled INTEGER DEFAULT 1, monthly_token_limit BIGINT DEFAULT 0,
        monthly_cost_limit DOUBLE PRECISION DEFAULT 0.0, created_by TEXT DEFAULT '',
        created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
        UNIQUE(user_id, credential_id)
    )""",
    """CREATE TABLE IF NOT EXISTS user_storage_quotas (
        user_id TEXT PRIMARY KEY, quota_bytes BIGINT NOT NULL DEFAULT 10737418240,
        used_bytes BIGINT NOT NULL DEFAULT 0, updated_by TEXT DEFAULT '', updated_at TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS storage_objects (
        object_id TEXT PRIMARY KEY, user_id TEXT NOT NULL, project_id TEXT DEFAULT '',
        article_id TEXT DEFAULT '', object_type TEXT NOT NULL, relative_path TEXT NOT NULL UNIQUE,
        size_bytes BIGINT NOT NULL DEFAULT 0, checksum TEXT DEFAULT '', created_at TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS user_ai_usage_monthly (
        usage_id TEXT PRIMARY KEY, user_id TEXT NOT NULL, credential_id TEXT DEFAULT '',
        usage_month TEXT NOT NULL, input_tokens BIGINT DEFAULT 0, output_tokens BIGINT DEFAULT 0,
        total_tokens BIGINT DEFAULT 0, estimated_cost DOUBLE PRECISION DEFAULT 0.0,
        updated_at TEXT NOT NULL, UNIQUE(user_id, credential_id, usage_month)
    )""",
)


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        raise RuntimeError("The server migration target must be PostgreSQL")
    for statement in TABLES:
        op.execute(statement)
    op.execute("ALTER TABLE articles ADD COLUMN IF NOT EXISTS owner_user_id TEXT DEFAULT ''")
    op.execute("CREATE INDEX IF NOT EXISTS idx_model_credentials_provider ON model_credentials(provider, enabled)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_user_model_allocations_user ON user_model_allocations(user_id, enabled)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_storage_objects_user ON storage_objects(user_id, created_at)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_user_ai_usage_month ON user_ai_usage_monthly(usage_month, user_id)")


def downgrade() -> None:
    for table in (
        "user_ai_usage_monthly", "storage_objects", "user_storage_quotas",
        "user_model_allocations", "model_credentials", "user_profiles",
    ):
        op.execute(f'DROP TABLE IF EXISTS "{table}" CASCADE')
