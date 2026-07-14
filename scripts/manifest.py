"""
manifest.py
===========
Genera manifest.json amb l'índex de totes les dades disponibles.
El visor web llegeix aquest fitxer per saber quines imatges pot carregar.
"""
import json
from datetime import datetime, timezone
from pathlib import Path

DATA_DIR = Path(__file__).parent.parent / "data"
ROOT     = Path(__file__).parent.parent

def construir_manifest():
    manifest = {
        "actualitzat": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "composit": {},
        "echotop_composit": {},
        "radars_individuals": {},
    }

    # Composit radar
    for json_f in sorted((DATA_DIR / "composit").rglob("*.json")):
        with open(json_f) as f:
            meta = json.load(f)
        ts = meta.get("timestamp_utc","")
        if ts:
            rel_png = json_f.with_suffix(".png").relative_to(ROOT)
            manifest["composit"][ts] = str(rel_png).replace("\\","/")

    # ECHOTOP composit
    for json_f in sorted((DATA_DIR / "echotop" / "composit").rglob("*.json")
                         if (DATA_DIR / "echotop" / "composit").exists() else []):
        with open(json_f) as f:
            meta = json.load(f)
        ts = meta.get("timestamp_utc","")
        if ts:
            rel_png = json_f.with_suffix(".png").relative_to(ROOT)
            manifest["echotop_composit"][ts] = str(rel_png).replace("\\","/")

    # Radars individuals ECHOTOP
    echotop_dir = DATA_DIR / "echotop"
    if echotop_dir.exists():
        for radar_dir in sorted(echotop_dir.iterdir()):
            if radar_dir.name == "composit" or not radar_dir.is_dir(): continue
            codi = radar_dir.name
            manifest["radars_individuals"][codi] = {}
            for json_f in sorted(radar_dir.rglob("*.json")):
                with open(json_f) as f:
                    meta = json.load(f)
                ts = meta.get("timestamp_utc","")
                if ts:
                    rel_png = json_f.with_suffix(".png").relative_to(ROOT)
                    manifest["radars_individuals"][codi][ts] = str(rel_png).replace("\\","/")

    # Estadístiques
    n_comp  = len(manifest["composit"])
    n_top   = len(manifest["echotop_composit"])
    n_rads  = sum(len(v) for v in manifest["radars_individuals"].values())
    n_radars = len(manifest["radars_individuals"])
    print(f"Manifest: {n_comp} composits | {n_top} ECHOTOP | "
          f"{n_rads} frames individuals ({n_radars} radars)")

    out = ROOT / "manifest.json"
    with open(out, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False)
    print(f"Manifest desat: {out}")

if __name__ == "__main__":
    construir_manifest()
