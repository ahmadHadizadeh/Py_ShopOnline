import re

from django.db import migrations


TABLE_NAME = "orders_orderitem"
PRODUCT_TABLE = "catalog_product"
TEMP_TABLE = "__repair_orders_orderitem"


def _quote_identifier(name):
    return '"' + name.replace('"', '""') + '"'


def repair_orderitem_product_schema(apps, schema_editor):
    connection = schema_editor.connection

    if connection.vendor != "sqlite":
        return

    if connection.in_atomic_block:
        raise RuntimeError(
            "orders.0007 requires a non-atomic SQLite migration."
        )

    with connection.cursor() as cursor:
        foreign_keys_enabled = bool(
            cursor.execute("PRAGMA foreign_keys").fetchone()[0]
        )

        table_row = cursor.execute(
            """
            SELECT sql
            FROM sqlite_master
            WHERE type = 'table' AND name = %s
            """,
            [TABLE_NAME],
        ).fetchone()

        if not table_row or not table_row[0]:
            return

        create_sql = table_row[0]

        columns = cursor.execute(
            f"PRAGMA table_info({_quote_identifier(TABLE_NAME)})"
        ).fetchall()

        product_column = next(
            (column for column in columns if column[1] == "product_id"),
            None,
        )

        if product_column is None:
            raise RuntimeError(
                "orders_orderitem.product_id column is missing."
            )

        foreign_keys = cursor.execute(
            f"PRAGMA foreign_key_list({_quote_identifier(TABLE_NAME)})"
        ).fetchall()

        has_product_fk = any(
            fk[2] == PRODUCT_TABLE
            and fk[3] == "product_id"
            and fk[4] == "id"
            and str(fk[6]).upper() == "SET NULL"
            for fk in foreign_keys
        )

        if not product_column[3] and has_product_fk:
            return

        if cursor.execute(
            """
            SELECT 1
            FROM sqlite_master
            WHERE type = 'table' AND name = %s
            """,
            [TEMP_TABLE],
        ).fetchone():
            raise RuntimeError(
                f"Temporary table {TEMP_TABLE!r} already exists."
            )

        index_and_trigger_sql = cursor.execute(
            """
            SELECT type, name, sql
            FROM sqlite_master
            WHERE tbl_name = %s
              AND type IN ('index', 'trigger')
              AND sql IS NOT NULL
            ORDER BY type, name
            """,
            [TABLE_NAME],
        ).fetchall()

        quoted_old = _quote_identifier(TABLE_NAME)
        quoted_temp = _quote_identifier(TEMP_TABLE)

        product_column_pattern = re.compile(
            r'(?P<name>"product_id"|product_id)\s+'
            r'(?P<definition>[^,\n]+)',
            re.IGNORECASE,
        )
        match = product_column_pattern.search(create_sql)

        if not match:
            raise RuntimeError(
                "Could not locate product_id definition in "
                "orders_orderitem CREATE TABLE SQL."
            )

        product_definition = re.sub(
            r"\bNOT\s+NULL\b",
            "NULL",
            match.group("definition"),
            count=1,
            flags=re.IGNORECASE,
        )

        if "REFERENCES" not in product_definition.upper():
            product_definition += (
                ' REFERENCES "catalog_product" ("id") '
                "ON DELETE SET NULL DEFERRABLE INITIALLY DEFERRED"
            )

        repaired_create_sql = (
            create_sql[: match.start("definition")]
            + product_definition
            + create_sql[match.end("definition") :]
        )

        repaired_create_sql = re.sub(
            r'(\bCREATE\s+TABLE\s+)'
            r'(?:"orders_orderitem"|orders_orderitem)(?=\s|\()',
            rf'\1{quoted_temp}',
            repaired_create_sql,
            count=1,
            flags=re.IGNORECASE,
        )

        if repaired_create_sql == create_sql:
            raise RuntimeError(
                "Could not redirect repaired CREATE TABLE statement "
                "to the temporary table."
            )

        quoted_columns = [
            _quote_identifier(column[1])
            for column in columns
        ]

        select_expressions = []
        for column in columns:
            name = column[1]
            quoted_name = _quote_identifier(name)

            if name == "product_id":
                select_expressions.append(
                    f"""
                    CASE
                        WHEN old.{quoted_name} IS NULL
                             OR EXISTS (
                                 SELECT 1
                                 FROM "catalog_product" AS product
                                 WHERE product."id" = old.{quoted_name}
                             )
                        THEN old.{quoted_name}
                        ELSE NULL
                    END AS {quoted_name}
                    """.strip()
                )
            else:
                select_expressions.append(
                    f"old.{quoted_name}"
                )

        orphan_count = cursor.execute(
            """
            SELECT COUNT(*)
            FROM "orders_orderitem" AS old
            WHERE old."product_id" IS NOT NULL
              AND NOT EXISTS (
                  SELECT 1
                  FROM "catalog_product" AS product
                  WHERE product."id" = old."product_id"
              )
            """
        ).fetchone()[0]

        if foreign_keys_enabled:
            cursor.execute("PRAGMA foreign_keys = OFF")
            if cursor.execute("PRAGMA foreign_keys").fetchone()[0]:
                raise RuntimeError(
                    "SQLite foreign_keys could not be disabled."
                )

        try:
            cursor.execute("BEGIN")

            cursor.execute(repaired_create_sql)

            cursor.execute(
                f"""
                INSERT INTO {quoted_temp}
                    ({", ".join(quoted_columns)})
                SELECT {", ".join(select_expressions)}
                FROM {quoted_old} AS old
                """
            )

            temp_fk_errors = cursor.execute(
                f"PRAGMA foreign_key_check({quoted_temp})"
            ).fetchall()

            if temp_fk_errors:
                raise RuntimeError(
                    "Temporary repaired table failed foreign-key validation: "
                    f"{temp_fk_errors!r}"
                )

            cursor.execute(f"DROP TABLE {quoted_old}")
            cursor.execute(
                f"ALTER TABLE {quoted_temp} RENAME TO {quoted_old}"
            )

            for _object_type, _name, sql in index_and_trigger_sql:
                cursor.execute(sql)

            if cursor.execute(
                """
                SELECT 1
                FROM sqlite_master
                WHERE type = 'table' AND name = 'sqlite_sequence'
                """
            ).fetchone():
                cursor.execute(
                    """
                    INSERT OR REPLACE INTO sqlite_sequence(name, seq)
                    SELECT %s, COALESCE(MAX(id), 0)
                    FROM "orders_orderitem"
                    """,
                    [TABLE_NAME],
                )

            cursor.execute("COMMIT")
        except Exception:
            cursor.execute("ROLLBACK")
            raise
        finally:
            if foreign_keys_enabled:
                cursor.execute("PRAGMA foreign_keys = ON")

        final_columns = cursor.execute(
            f"PRAGMA table_info({quoted_old})"
        ).fetchall()

        final_product_column = next(
            column
            for column in final_columns
            if column[1] == "product_id"
        )

        final_foreign_keys = cursor.execute(
            f"PRAGMA foreign_key_list({quoted_old})"
        ).fetchall()

        final_has_product_fk = any(
            fk[2] == PRODUCT_TABLE
            and fk[3] == "product_id"
            and fk[4] == "id"
            and str(fk[6]).upper() == "SET NULL"
            for fk in final_foreign_keys
        )

        if final_product_column[3] != 0:
            raise RuntimeError(
                "orders_orderitem.product_id is still NOT NULL after repair."
            )

        if not final_has_product_fk:
            raise RuntimeError(
                "orders_orderitem.product_id foreign key is missing "
                "after repair."
            )

        if foreign_keys_enabled:
            fk_errors = cursor.execute(
                f"PRAGMA foreign_key_check({quoted_old})"
            ).fetchall()

            if fk_errors:
                raise RuntimeError(
                    "Repaired orders_orderitem has foreign-key errors: "
                    f"{fk_errors!r}"
                )

        integrity = cursor.execute(
            "PRAGMA integrity_check"
        ).fetchone()[0]

        if integrity != "ok":
            raise RuntimeError(
                "SQLite integrity_check failed after repair: "
                f"{integrity!r}"
            )

        print(
            "orders.0007 repaired orders_orderitem; "
            f"orphan product refs nulled: {orphan_count}"
        )


class Migration(migrations.Migration):
    dependencies = [
        ("orders", "0006_alter_orderitem_product"),
    ]

    atomic = False

    operations = [
        migrations.RunPython(
            repair_orderitem_product_schema,
            migrations.RunPython.noop,
        ),
    ]
