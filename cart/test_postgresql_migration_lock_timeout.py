"""Integration test for the PostgreSQL lock timeout in cart.0004."""

import threading
import time
from unittest import skipUnless

from django.db import close_old_connections, connection, transaction
from django.db.migrations.executor import MigrationExecutor
from django.db.migrations.recorder import MigrationRecorder
from django.db.utils import OperationalError
from django.test import TransactionTestCase


TARGET_MIGRATION = ("cart", "0004_cartitem_nonnegative_unit_price_snapshot")
PREVIOUS_MIGRATION = (
    "cart",
    "0003_remove_cartitem_unique_product_per_cart_and_more",
)
CART_ITEM_TABLE = "cart_cartitem"
SNAPSHOT_CONSTRAINT = "cart_item_unit_price_snapshot_gte_0"


@skipUnless(
    connection.vendor == "postgresql",
    "This test verifies PostgreSQL-specific migration lock behavior.",
)
class PostgreSQLCartMigrationLockTimeoutTests(TransactionTestCase):
    """Prove cart.0004 times out atomically and succeeds on a clean retry."""

    reset_sequences = True

    def _constraint_exists(self):
        with connection.cursor() as cursor:
            constraints = connection.introspection.get_constraints(
                cursor,
                CART_ITEM_TABLE,
            )
        return SNAPSHOT_CONSTRAINT in constraints

    def test_lock_timeout_rolls_back_migration_and_allows_retry(self):
        # The test database is migrated to its latest state by Django's test
        # runner. Move only this app back one migration so cart.0004 can run.
        executor = MigrationExecutor(connection)
        self.assertIn(
            TARGET_MIGRATION,
            executor.loader.graph.leaf_nodes("cart"),
        )
        executor.migrate([PREVIOUS_MIGRATION])

        self.assertNotIn(
            TARGET_MIGRATION,
            MigrationRecorder(connection).applied_migrations(),
        )
        self.assertFalse(self._constraint_exists())

        with connection.cursor() as cursor:
            cursor.execute("SELECT current_setting('lock_timeout')")
            original_lock_timeout = cursor.fetchone()[0]

        lock_acquired = threading.Event()
        release_lock = threading.Event()
        migration_ready = threading.Event()
        migration_finished = threading.Event()
        blocker_finished = threading.Event()
        migration_state = {}
        thread_errors = {}

        def hold_access_share_lock():
            close_old_connections()
            try:
                with transaction.atomic():
                    with connection.cursor() as cursor:
                        cursor.execute(
                            "LOCK TABLE cart_cartitem IN ACCESS SHARE MODE"
                        )
                    lock_acquired.set()
                    if not release_lock.wait(timeout=15):
                        raise RuntimeError(
                            "Test did not release the CartItem table lock."
                        )
            except BaseException as exc:
                thread_errors["blocker"] = exc
            finally:
                connection.close()
                blocker_finished.set()

        def apply_target_migration():
            close_old_connections()
            try:
                with connection.cursor() as cursor:
                    cursor.execute("SELECT pg_backend_pid()")
                    migration_state["backend_pid"] = cursor.fetchone()[0]

                migration_ready.set()
                MigrationExecutor(connection).migrate([TARGET_MIGRATION])
                migration_state["applied"] = True
            except BaseException as exc:
                migration_state["error"] = exc
            finally:
                migration_ready.set()
                connection.close()
                migration_finished.set()

        blocker = threading.Thread(
            target=hold_access_share_lock,
            name="cart-migration-lock-holder",
            daemon=True,
        )
        migrator = threading.Thread(
            target=apply_target_migration,
            name="cart-migration-runner",
            daemon=True,
        )

        lock_wait_observed = False
        migration_started_at = None

        blocker.start()
        try:
            self.assertTrue(
                lock_acquired.wait(timeout=5),
                "The blocker did not acquire ACCESS SHARE on cart_cartitem.",
            )

            migration_started_at = time.monotonic()
            migrator.start()
            self.assertTrue(
                migration_ready.wait(timeout=5),
                "The migration worker did not initialize its PostgreSQL connection.",
            )

            deadline = migration_started_at + 7.5
            backend_pid = migration_state.get("backend_pid")
            self.assertIsNotNone(backend_pid, migration_state.get("error"))

            while not migration_finished.is_set() and time.monotonic() < deadline:
                with connection.cursor() as cursor:
                    cursor.execute(
                        """
                        SELECT wait_event_type
                        FROM pg_stat_activity
                        WHERE pid = %s
                        """,
                        [backend_pid],
                    )
                    row = cursor.fetchone()

                if row is not None and row[0] == "Lock":
                    lock_wait_observed = True

                migration_finished.wait(timeout=0.025)
        finally:
            # Always release the blocker, including when an assertion fails.
            release_lock.set()
            if migrator.ident is not None:
                migrator.join(timeout=10)
            blocker.join(timeout=10)

        self.assertFalse(migrator.is_alive(), "Migration worker did not terminate.")
        self.assertFalse(blocker.is_alive(), "Lock-holder worker did not terminate.")
        self.assertFalse(thread_errors, f"PostgreSQL lock-holder errors: {thread_errors}")
        self.assertTrue(
            migration_finished.is_set(),
            "Migration worker did not report completion.",
        )
        self.assertTrue(
            lock_wait_observed,
            "PostgreSQL never reported the migration waiting for a table lock.",
        )

        elapsed = time.monotonic() - migration_started_at
        error = migration_state.get("error")
        self.assertNotIn("applied", migration_state)
        self.assertIsInstance(error, OperationalError)
        self.assertIn("lock timeout", str(error).lower())
        self.assertGreaterEqual(elapsed, 4.0)

        # A failed atomic migration must leave both schema and migration
        # recorder unchanged. The blocker is already released at this point.
        self.assertNotIn(
            TARGET_MIGRATION,
            MigrationRecorder(connection).applied_migrations(),
        )
        self.assertFalse(self._constraint_exists())

        # Re-run on the same test connection after the blocker is gone. This
        # also proves SET LOCAL did not leak beyond the migration transaction.
        MigrationExecutor(connection).migrate([TARGET_MIGRATION])

        self.assertIn(
            TARGET_MIGRATION,
            MigrationRecorder(connection).applied_migrations(),
        )
        self.assertTrue(self._constraint_exists())

        with connection.cursor() as cursor:
            cursor.execute("SELECT current_setting('lock_timeout')")
            restored_lock_timeout = cursor.fetchone()[0]

        self.assertEqual(restored_lock_timeout, original_lock_timeout)
