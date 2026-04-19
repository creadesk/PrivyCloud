#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
export_tables.py
~~~~~~~~~~~~~~~~~~~~

Dieses Skript ruft das bereits vorhandene `generate_insert.py` für eine Reihe von Tabellen auf
und schreibt die resultierenden INSERT‑Anweisungen in einer einzigen Datei.

Benutzung:
    python export_all_tables.py

Voraussetzungen:
    - `generate_insert.py` muss im gleichen Verzeichnis liegen (oder der Pfad zu diesem Skript muss angepasst werden).
    - SQLite‑Datenbank liegt unter `../../../db/db.sqlite3` relativ zu diesem Skript.
"""

import subprocess
import sys
from pathlib import Path

# --------------------------------------------------------------------------- #
# Konfiguration
# --------------------------------------------------------------------------- #

DB_PATH = Path(__file__).parent.parent.parent.parent / "db" / "db.sqlite3"  # ../../../db/db.sqlite3
OUTPUT_FILE = Path("/tmp/dbexport.sql")
TABLES_IN_ORDER = [
    "paas_appdefinition",
    "paas_appenvvarperapp",
    "paas_appvolumeperapp",
    "paas_configpatch",
    "paas_remotehost",
    "paas_appimagetag",
]

# Der Pfad zu generate_insert.py (im selben Verzeichnis)
GEN_INSERT_SCRIPT = Path(__file__).parent / "generate_insert.py"


def run_generate_insert(db_path: Path, table: str) -> str:
    """
    Führt `generate_insert.py` für eine einzelne Tabelle aus und liefert den
    erzeugten INSERT‑Befehl zurück.
    """
    # Aufruf über subprocess, damit die gesamte Logik von generate_insert.py
    # (inkl. Fehler‑Handling, Escaping usw.) genutzt wird.
    cmd = [
        sys.executable,  # Python‑Interpreter
        str(GEN_INSERT_SCRIPT),
        str(db_path),
        table,
    ]

    try:
        result = subprocess.run(
            cmd,
            check=True,
            capture_output=True,
            text=True,
        )
        return result.stdout.strip()  # Rückgabe ohne Zeilenumbrüche
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(
            f"Fehler beim Generieren des INSERTs für Tabelle `{table}`:\n"
            f"  Befehl: {exc.cmd}\n"
            f"  Rückgabecode: {exc.returncode}\n"
            f"  stdout: {exc.stdout}\n"
            f"  stderr: {exc.stderr}"
        ) from exc


def main():
    if not GEN_INSERT_SCRIPT.is_file():
        sys.stderr.write(
            f"Fehler: generate_insert.py nicht gefunden unter {GEN_INSERT_SCRIPT}\n"
        )
        sys.exit(1)

    if not DB_PATH.is_file():
        sys.stderr.write(f"Fehler: Datenbank nicht gefunden unter {DB_PATH}\n")
        sys.exit(1)

    insert_statements = []

    print(f"Starte Export für Datenbank: {DB_PATH}")
    for tbl in TABLES_IN_ORDER:
        print(f"  -> Tabelle: {tbl}")
        try:
            stmt = run_generate_insert(DB_PATH, tbl)
            insert_statements.append(stmt)
        except RuntimeError as e:
            sys.stderr.write(f"{e}\n")
            sys.exit(1)

    # Alles in eine Datei schreiben (jeweils mit einem Zeilenumbruch)
    output_text = "\n\n".join(insert_statements) + "\n"
    OUTPUT_FILE.write_text(output_text, encoding="utf-8")
    print(f"Alle INSERT‑Befehle wurden in {OUTPUT_FILE} gespeichert.")


if __name__ == "__main__":
    main()