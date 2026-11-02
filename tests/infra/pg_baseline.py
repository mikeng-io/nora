"""Restore disposable module databases without rebuilding their schema per case."""

from dataclasses import dataclass

import psycopg
from psycopg import sql


def schema_state(conn: psycopg.Connection) -> list:
    """Logical definitions only: TRUNCATE changes physical files, not this state."""
    return conn.execute("""
        SELECT kind, name, definition FROM (
          SELECT 'column' AS kind, c.relname || '.' || a.attname AS name,
                 jsonb_build_array(a.attnum, format_type(a.atttypid, a.atttypmod),
                   a.attnotnull, a.attidentity, a.attgenerated,
                   pg_get_expr(d.adbin, d.adrelid))::text AS definition
          FROM pg_attribute a JOIN pg_class c ON c.oid = a.attrelid
          LEFT JOIN pg_attrdef d ON d.adrelid = a.attrelid AND d.adnum = a.attnum
          WHERE c.relnamespace = 'public'::regnamespace
            AND c.relkind IN ('r', 'p') AND a.attnum > 0 AND NOT a.attisdropped
          UNION ALL
          SELECT 'relation', relname,
                 jsonb_build_array(relkind, relpersistence, reloptions, relacl)::text
          FROM pg_class WHERE relnamespace = 'public'::regnamespace
            AND relkind IN ('r', 'p', 'v', 'm', 'S')
          UNION ALL
          SELECT 'constraint', c.relname || '.' || t.conname, pg_get_constraintdef(t.oid)
          FROM pg_constraint t JOIN pg_class c ON c.oid = t.conrelid
          WHERE c.relnamespace = 'public'::regnamespace
          UNION ALL
          SELECT 'index', indexname, indexdef FROM pg_indexes WHERE schemaname = 'public'
          UNION ALL
          SELECT 'trigger', c.relname || '.' || t.tgname,
                 jsonb_build_array(t.tgenabled, pg_get_triggerdef(t.oid))::text
          FROM pg_trigger t JOIN pg_class c ON c.oid = t.tgrelid
          WHERE c.relnamespace = 'public'::regnamespace AND NOT t.tgisinternal
          UNION ALL
          SELECT 'function', p.oid::regprocedure::text,
                 jsonb_build_array(p.prosrc, p.proconfig, p.provolatile, p.prosecdef,
                   pg_get_function_result(p.oid), p.proacl)::text
          FROM pg_proc p WHERE p.pronamespace = 'public'::regnamespace
          UNION ALL
          SELECT 'schema', nspname, '' FROM pg_namespace
          WHERE nspname !~ '^pg_' AND nspname <> 'information_schema'
          UNION ALL
          SELECT 'extension', extname, extversion FROM pg_extension
          UNION ALL
          SELECT 'view', c.relname, pg_get_viewdef(c.oid)
          FROM pg_class c WHERE c.relnamespace = 'public'::regnamespace
            AND c.relkind IN ('v', 'm')
          UNION ALL
          SELECT 'sequence', c.relname,
                 jsonb_build_array(s.seqstart, s.seqincrement, s.seqmin,
                   s.seqmax, s.seqcache, s.seqcycle)::text
          FROM pg_sequence s JOIN pg_class c ON c.oid = s.seqrelid
          WHERE c.relnamespace = 'public'::regnamespace
        ) definitions ORDER BY kind, name, definition
    """).fetchall()


@dataclass(frozen=True)
class PgBaseline:
    tables: tuple[str, ...]
    seeds: dict[str, list[tuple]]
    columns: dict[str, tuple[str, ...]]
    sequences: dict[str, tuple[int, bool]]
    references: tuple[tuple[str, str], ...]
    schema: list

    @classmethod
    def capture(cls, conn: psycopg.Connection) -> "PgBaseline":
        tables = tuple(name for (name,) in conn.execute(
            "SELECT tablename FROM pg_tables WHERE schemaname = 'public' ORDER BY tablename"
        ))
        seeds = {}
        for name in tables:
            rows = conn.execute(sql.SQL("SELECT * FROM {} ORDER BY to_jsonb({})::text").format(
                sql.Identifier(name), sql.Identifier(name)
            )).fetchall()
            if rows:
                seeds[name] = rows
        columns: dict[str, tuple[str, ...]] = {}
        for name in seeds:
            columns[name] = tuple(column for (column,) in conn.execute(
                "SELECT attname FROM pg_attribute WHERE attrelid = %s::regclass "
                "AND attnum > 0 AND NOT attisdropped AND attgenerated = '' ORDER BY attnum",
                (name,),
            ))
        sequence_names = conn.execute(
            "SELECT relname FROM pg_class WHERE relnamespace = 'public'::regnamespace "
            "AND relkind = 'S' ORDER BY relname"
        ).fetchall()
        sequences = {
            name: conn.execute(sql.SQL("SELECT last_value, is_called FROM {}").format(
                sql.Identifier(name)
            )).fetchone()
            for (name,) in sequence_names
        }
        references = tuple(conn.execute(
            "SELECT child.relname, parent.relname FROM pg_constraint f "
            "JOIN pg_class child ON child.oid = f.conrelid "
            "JOIN pg_class parent ON parent.oid = f.confrelid "
            "WHERE f.contype = 'f' AND child.relnamespace = 'public'::regnamespace "
            "AND parent.relnamespace = 'public'::regnamespace"
        ).fetchall())
        return cls(tables, seeds, columns, sequences, references, schema_state(conn))

    def reset(self, conn: psycopg.Connection) -> int:
        """Reset only changed data; an incompatible schema is an explicit fresh-DB case."""
        if schema_state(conn) != self.schema:
            raise RuntimeError("Reusable test DB schema changed; use the fresh_database marker")
        others = conn.execute(
            "SELECT pid, backend_type, state, wait_event_type, wait_event FROM pg_stat_activity "
            "WHERE datname = current_database() AND backend_type = 'client backend' "
            "AND pid <> pg_backend_pid()"
        ).fetchall()
        if others:
            raise RuntimeError(
                f"Reusable test DB has test-owned connections; close them first: {others}"
            )
        dirty = {
            name for name, exists in conn.execute(sql.SQL(" UNION ALL ").join(
                sql.SQL("SELECT {}, EXISTS (SELECT 1 FROM {})").format(
                    sql.Literal(name), sql.Identifier(name)
                ) for name in self.tables if name not in self.seeds
            )) if exists
        }
        for name, baseline in self.seeds.items():
            current = conn.execute(sql.SQL("SELECT * FROM {} ORDER BY to_jsonb({})::text").format(
                sql.Identifier(name), sql.Identifier(name)
            )).fetchall()
            if current != baseline:
                if name == "alembic_version":
                    raise RuntimeError("Reusable test DB migration head changed")
                dirty.add(name)
        while True:
            expanded = dirty | {child for child, parent in self.references if parent in dirty}
            if expanded == dirty:
                break
            dirty = expanded
        if dirty:
            conn.execute("SET LOCAL nora.row_history = 'off'")
            conn.execute(sql.SQL("TRUNCATE {} RESTART IDENTITY").format(
                sql.SQL(", ").join(map(sql.Identifier, sorted(dirty)))
            ))
            for name in sorted(dirty & self.seeds.keys()):
                columns = self.columns[name]
                statement = sql.SQL(
                    "INSERT INTO {} ({}) OVERRIDING SYSTEM VALUE VALUES ({})"
                ).format(
                    sql.Identifier(name), sql.SQL(", ").join(map(sql.Identifier, columns)),
                    sql.SQL(", ").join(sql.Placeholder() for _ in columns),
                )
                with conn.cursor() as cursor:
                    cursor.executemany(statement, self.seeds[name])
        for name, state in self.sequences.items():
            current = conn.execute(sql.SQL("SELECT last_value, is_called FROM {}").format(
                sql.Identifier(name)
            )).fetchone()
            if current != state:
                conn.execute("SELECT setval(%s::regclass, %s, %s)", (name, *state))
        return len(dirty)
