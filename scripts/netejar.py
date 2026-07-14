"""
netejar.py
==========
Elimina dades de més de 30 dies per mantenir el repositori manejable.
"""
from datetime import datetime, timezone, timedelta
from pathlib import Path
import shutil

DATA_DIR  = Path(__file__).parent.parent / "data"
MAX_DIES  = 30

def netejar():
    llindar = datetime.now(timezone.utc) - timedelta(days=MAX_DIES)
    llindar_str = llindar.strftime("%Y%m%d")
    eliminats = 0

    for dia_dir in DATA_DIR.rglob("*/[0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]"):
        if not dia_dir.is_dir(): continue
        dia = dia_dir.name
        if len(dia) == 8 and dia.isdigit() and dia < llindar_str:
            print(f"  Eliminant: {dia_dir}")
            shutil.rmtree(dia_dir)
            eliminats += 1

    print(f"Carpetes eliminades: {eliminats} (> {MAX_DIES} dies)")

if __name__ == "__main__":
    netejar()
