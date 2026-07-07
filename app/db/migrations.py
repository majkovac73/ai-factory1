"""
Lightweight startup migrations for SQLite — no Alembic.

Each migration function is idempotent: it checks whether the change is
needed before applying it. Call run_all_migrations(engine) from app/main.py
before Base.metadata.create_all().
"""
import logging

from sqlalchemy import inspect, text

logger = logging.getLogger("ai-factory")


def _migrate_fulfillment_records_add_transaction_id(engine):
    """
    Adds etsy_transaction_id to fulfillment_records and changes the unique
    constraint from (etsy_receipt_id) to (etsy_receipt_id, etsy_transaction_id).

    SQLite does not support ALTER TABLE DROP CONSTRAINT or ADD CONSTRAINT, so
    the migration recreates the table, copies existing rows (padding
    etsy_transaction_id with '' for any pre-existing rows), and drops the old table.
    """
    inspector = inspect(engine)
    if "fulfillment_records" not in inspector.get_table_names():
        return  # Table doesn't exist yet; create_all will make it correctly

    columns = [c["name"] for c in inspector.get_columns("fulfillment_records")]
    if "etsy_transaction_id" in columns:
        return  # Already migrated

    logger.info("Migration: adding etsy_transaction_id to fulfillment_records")

    with engine.connect() as conn:
        conn.execute(text("""
            CREATE TABLE fulfillment_records_new (
                id VARCHAR NOT NULL PRIMARY KEY,
                etsy_receipt_id VARCHAR NOT NULL,
                etsy_transaction_id VARCHAR NOT NULL DEFAULT '',
                task_id VARCHAR,
                pod_product_id VARCHAR,
                printify_order_id VARCHAR,
                status VARCHAR NOT NULL DEFAULT 'submitted',
                tracking_number VARCHAR,
                carrier VARCHAR,
                created_at DATETIME NOT NULL,
                updated_at DATETIME NOT NULL,
                CONSTRAINT uq_receipt_transaction
                    UNIQUE (etsy_receipt_id, etsy_transaction_id)
            )
        """))
        conn.execute(text("""
            INSERT INTO fulfillment_records_new
                (id, etsy_receipt_id, etsy_transaction_id, task_id, pod_product_id,
                 printify_order_id, status, tracking_number, carrier, created_at, updated_at)
            SELECT
                id, etsy_receipt_id, '' AS etsy_transaction_id, task_id, pod_product_id,
                printify_order_id, status, tracking_number, carrier, created_at, updated_at
            FROM fulfillment_records
        """))
        conn.execute(text("DROP TABLE fulfillment_records"))
        conn.execute(text(
            "ALTER TABLE fulfillment_records_new RENAME TO fulfillment_records"
        ))
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_fulfillment_records_etsy_receipt_id "
            "ON fulfillment_records (etsy_receipt_id)"
        ))
        conn.commit()

    logger.info("Migration: fulfillment_records updated successfully")


def run_all_migrations(engine):
    """Run every migration in order. Safe to call on every startup."""
    _migrate_fulfillment_records_add_transaction_id(engine)
