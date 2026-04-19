#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
generate_insert.py
~~~~~~~~~~~~~~~~~~

Generiert einen einzigen INSERT‑Befehl, der alle Zeilen einer SQLite‑Tabelle
einschließt.  Das Skript kann als Kommandozeilen‑Tool verwendet werden:

    python generate_insert.py <datenbank.db> <tabelle> [-o <ausgabe.txt>]

Wenn keine Ausgabedatei angegeben ist, wird der Befehl auf stdout geschrieben.
"""

import argparse
import sqlite3
import sys
from pathlib import Path


def escape_sqlite_value(value):
    """
    Wandelt ein Python‑Objekt in einen String um, der in einer SQLite‑Anweisung
    als Literal verwendet werden kann.
    """
    if value is None:
        return "NULL"
    if isinstance(value, str):
        # Escape single quotes by doubling them (SQLite‑Standard)
        escaped = value.replace("'", "''")
        return f"'{escaped}'"
    if isinstance(value, bytes):
        # Blob‑Werte als hex‑Literal ausgeben
        return f"X'{value.hex()}'"
    # Bool, int, float und alles andere (z. B. komplexe Typen) werden als
    # Text konvertiert – für die meisten Anwendungen reicht das aus.
    return str(value)


def generate_insert(db_path: Path, table: str) -> str:
    """
    Baut den INSERT‑Befehl zurück.  Er wirft bei Fehlern eine RuntimeError.
    """
    conn = sqlite3.connect(str(db_path))
    cur = conn.cursor()

    # 1. Spaltennamen ermitteln
    cur.execute(f'PRAGMA table_info("{table}")')
    columns_info = cur.fetchall()
    if not columns_info:
        raise RuntimeError(f"Keine Informationen zu Tabelle '{table}' gefunden.")
    columns = [col[1] for col in columns_info]  # col[1] ist der Name

    # 2. Alle Zeilen holen
    cur.execute(f'SELECT * FROM "{table}"')
    rows = cur.fetchall()

    if not rows:
        raise RuntimeError(f"Tabelle '{table}' enthält keine Zeilen.")

    # 3. INSERT‑Statement bauen
    col_part = ", ".join(f'"{c}"' for c in columns)
    value_parts = []
    for row in rows:
        escaped_vals = [escape_sqlite_value(v) for v in row]
        value_parts.append(f"({', '.join(escaped_vals)})")

    insert_stmt = f'INSERT INTO "{table}" ({col_part}) VALUES\n' + ",\n".join(value_parts) + ';'
    return insert_stmt


def main():
    parser = argparse.ArgumentParser(
        description="Erzeuge einen einzigen INSERT INTO‑Befehl für alle Zeilen einer SQLite‑Tabelle."
    )
    parser.add_argument("database", help="Pfad zur SQLite‑Datenbankdatei")
    parser.add_argument("table", help="Name der Tabelle")
    parser.add_argument(
        "-o", "--output",
        help="Datei, in die der INSERT‑Befehl geschrieben werden soll "
             "(Standard: stdout)",
    )

    args = parser.parse_args()
    db_path = Path(args.database)

    try:
        insert_sql = generate_insert(db_path, args.table)
    except RuntimeError as e:
        sys.stderr.write(f"Fehler: {e}\n")
        sys.exit(1)

    if args.output:
        out_path = Path(args.output)
        out_path.write_text(insert_sql, encoding="utf-8")
        print(f"INSERT‑Befehl wurde in '{out_path}' geschrieben.")
    else:
        print(insert_sql)


if __name__ == "__main__":
    main()