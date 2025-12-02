# core/management/commands/runserver_plus.py
import os
import sys
import subprocess
import signal
from pathlib import Path
from django.core.management import call_command
from django.core.management.base import BaseCommand
from django.conf import settings
import threading
import time


class Command(BaseCommand):
    help = "Startet Django runserver + Celery Worker + Beat + Flower (alles in einem Befehl)"

    def add_arguments(self, parser):
        parser.add_argument(
            '--addrport', default='127.0.0.1:8000',
            help='Adresse und Port für den Django-Server (z. B. 0.0.0.0:8000)',
        )
        parser.add_argument(
            '--loglevel', default='info',
            choices=['debug', 'info', 'warning', 'error', 'critical'],
            help='Log-Level für Celery und Flower',
        )
        parser.add_argument(
            '--noflower', action='store_true',
            help='Flower nicht starten (nur Worker + Beat)',
        )

    def handle(self, *args, **options):
        addrport = options['addrport']
        loglevel = options['loglevel'].upper()
        start_flower = not options['noflower']

        log_dir = Path(settings.BASE_DIR) / "logs"
        log_dir.mkdir(exist_ok=True)

        celery_log_file = log_dir / "celery.log"
        flower_log_file = log_dir / "flower.log"

        python = sys.executable

        # 1. Celery Worker + Beat (kombiniert)
        celery_cmd = [
            python, "-m", "celery",
            "-A", "core",                     # ← passe an, falls dein Celery-App-Name anders ist (z. B. config.celery_app)
            "worker",
            "--beat",                         # Beat inklusive
            "--loglevel", loglevel,
            "--without-gossip",
            "--without-mingle",
            "--logfile", str(celery_log_file),
            # "--concurrency", "4",           # bei Bedarf wieder aktivieren
        ]

        # 2. Flower (optional)
        flower_cmd = [
            python, "-m", "celery",
            "-A", "core",
            "flower",
            "--port=5555",
            "--loglevel=" + loglevel,
            "--logfile=" + str(flower_log_file),
        ]

        procs = []
        log_threads = []

        def cleanup(signum=None, frame=None):
            self.stdout.write(self.style.WARNING("\nBeende alle Hintergrundprozesse..."))

            # 1. Erst freundlich bitten (SIGTERM)
            for p in procs:
                if p.poll() is None:
                    p.terminate()

            # 2. Warten – max. 8 Sekunden
            gone, still_alive = [], []
            for p in procs:
                try:
                    p.wait(timeout=8)
                    gone.append(p)
                except subprocess.TimeoutExpired:
                    still_alive.append(p)

            # 3. Wer noch lebt → hart killen (SIGKILL)
            for p in still_alive:
                self.stdout.write(self.style.ERROR(f"Prozess {p.pid} reagiert nicht → SIGKILL"))
                p.kill()

            # Log-Threads stoppen
            for t in log_threads:
                t.do_run = False

            self.stdout.write(self.style.SUCCESS("Alle Prozesse sauber beendet."))

            if signum is not None:
                sys.exit(0)

        # Signal-Handler registrieren
        signal.signal(signal.SIGINT, cleanup)
        signal.signal(signal.SIGTERM, cleanup)

        try:
            # === Celery Worker + Beat starten ===
            self.stdout.write(self.style.SUCCESS("Starte Celery Worker + Beat..."))
            celery_proc = subprocess.Popen(
                celery_cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )
            procs.append(celery_proc)

            # === Flower starten (falls gewünscht) ===
            if start_flower:
                self.stdout.write(self.style.SUCCESS("Starte Flower → http://127.0.0.1:5555"))
                flower_proc = subprocess.Popen(
                    flower_cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    bufsize=1,
                    env={**os.environ, "FLOWER_NO_THREADING": "1"},  # ← eliminiert den threading-Fehler!
                )
                procs.append(flower_proc)
            else:
                self.stdout.write(self.style.NOTICE("Flower deaktiviert (--noflower)"))

            # === Live-Log-Ausgabe (non-blocking, sauber beendbar) ===
            def follow_logs(proc, prefix):
                t = threading.current_thread()
                t.do_run = True
                for line in proc.stdout:  # type: ignore
                    if not t.do_run:
                        break
                    if line.strip():
                        self.stdout.write(prefix + line.rstrip())
                proc.stdout.close()  # type: ignore

            for proc in procs:
                prefix = "[Celery] " if proc == celery_proc else "[Flower] "
                thread = threading.Thread(target=follow_logs, args=(proc, prefix), daemon=False)
                thread.do_run = True
                thread.start()
                log_threads.append(thread)

            # === Django Server starten ===
            self.stdout.write(self.style.SUCCESS(f"Starte Django auf {addrport}"))
            self.stdout.write(self.style.SUCCESS("Drücke Ctrl+C zum sauberen Beenden\n"))

            call_command("runserver", addrport, use_reloader=False, skip_checks=True)

        except KeyboardInterrupt:
            pass
        finally:
            cleanup()