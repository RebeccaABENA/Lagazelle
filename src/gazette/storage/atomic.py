"""Écriture atomique de fichiers JSON et verrou inter-processus minimal."""

from __future__ import annotations

import contextlib
import json
import os
import tempfile
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any


def atomic_write_json(path: str | Path, data: Any) -> None:
    """Écrit `data` en JSON dans un fichier temporaire du même dossier, puis `os.replace`.

    `os.replace` est atomique sur un même système de fichiers : un lecteur voit soit
    l'ancien fichier complet, soit le nouveau, jamais un JSON tronqué.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
            fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_name, path)
    except BaseException:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(tmp_name)
        raise


def read_json(path: str | Path, default: Any = None) -> Any:
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except FileNotFoundError:
        return default


@contextlib.contextmanager
def file_lock(
    path: str | Path, timeout: float = 30.0, stale_after: float = 120.0
) -> Iterator[None]:
    """Verrou par fichier `.lock` (création exclusive), portable Linux/Windows.

    Sert quand plusieurs tâches Airflow mettent à jour le même fichier d'état.
    Un verrou plus vieux que `stale_after` secondes (processus tué) est repris.
    """
    lock_path = Path(f"{path}.lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + timeout
    while True:
        try:
            fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.close(fd)
            break
        except FileExistsError:
            with contextlib.suppress(FileNotFoundError):
                if time.time() - lock_path.stat().st_mtime > stale_after:
                    os.unlink(lock_path)
                    continue
            if time.monotonic() > deadline:
                raise TimeoutError(f"Verrou toujours pris : {lock_path}") from None
            time.sleep(0.05)
    try:
        yield
    finally:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(lock_path)
