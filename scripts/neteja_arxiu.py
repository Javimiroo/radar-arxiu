"""
neteja_arxiu.py
===============
Neteja RETROACTIVA d'interferencies de tot l'arxiu existent.
Aplica el mateix filtre que descarregar.py (neteja_interferencies) als frames
ja guardats. Idempotent: es pot executar tantes vegades com calga.

- ECHOTOP (composit i per radar): llig *_alt.png (classes 1-13), filtra,
  reescriu _alt.png i posa transparents els pixels eliminats al PNG de color.
- Composit reflectivitat: llig el .npz (classes natives), filtra, regenera
  PNG i actualitza el .json.
"""
import io, json, sys
from pathlib import Path

import numpy as np
from PIL import Image as PILImage

sys.path.insert(0, str(Path(__file__).parent))
from descarregar import neteja_interferencies, array_a_png, RADAR_RGBA

ROOT = Path(__file__).parent.parent
DATA = ROOT / "data"

def neteja_echotop():
    n_fitxers = n_px = 0
    for alt_f in sorted(DATA.glob("echotop/**/*_alt.png")):
        png_f = alt_f.with_name(alt_f.name.replace("_alt.png", ".png"))
        if not png_f.exists(): continue
        alt = np.array(PILImage.open(alt_f))
        if alt.max() == 0 or alt.max() > 13: continue   # buit o dades antigues invalides
        net = neteja_interferencies(alt, min_area=2, filtre_rugositat=True)
        treu = (alt > 0) & (net == 0)
        if not treu.any(): continue
        # Reescriu _alt.png
        buf = io.BytesIO()
        PILImage.fromarray(net, "L").save(buf, "PNG", optimize=True)
        alt_f.write_bytes(buf.getvalue())
        # Esborra els pixels al PNG de color
        rgba = np.array(PILImage.open(png_f).convert("RGBA"))
        rgba[treu] = 0
        buf = io.BytesIO()
        PILImage.fromarray(rgba, "RGBA").save(buf, "PNG", optimize=True)
        png_f.write_bytes(buf.getvalue())
        # Actualitza px_actius al json si existeix
        json_f = png_f.with_suffix(".json")
        if json_f.exists():
            try:
                meta = json.loads(json_f.read_text())
                meta["px_actius"] = int((net > 0).sum())
                meta["neteja"] = "retroactiva_v2"
                json_f.write_text(json.dumps(meta, indent=2))
            except Exception: pass
        n_fitxers += 1; n_px += int(treu.sum())
        print(f"  {png_f.relative_to(DATA)}: -{int(treu.sum())} px")
    print(f"ECHOTOP: {n_fitxers} frames netejats, {n_px} px eliminats")

def neteja_composit():
    n_fitxers = n_px = 0
    for npz_f in sorted(DATA.glob("composit/**/*.npz")):
        try:
            arr = np.load(npz_f)["data"]
        except Exception: continue
        if arr.ndim != 2 or arr.shape[0] < 10: continue
        net = neteja_interferencies(arr.astype(np.uint8), min_area=1)
        treu = (arr > 0) & (net == 0)
        if not treu.any(): continue
        np.savez_compressed(npz_f, data=net)
        png_f = npz_f.with_suffix(".png")
        png_f.write_bytes(array_a_png(net, RADAR_RGBA))
        json_f = npz_f.with_suffix(".json")
        if json_f.exists():
            try:
                meta = json.loads(json_f.read_text())
                meta["px_actius"] = int((net > 0).sum())
                meta["max_val"] = int(net.max())
                meta["neteja"] = "retroactiva_v2"
                json_f.write_text(json.dumps(meta, indent=2))
            except Exception: pass
        n_fitxers += 1; n_px += int(treu.sum())
        print(f"  {png_f.relative_to(DATA)}: -{int(treu.sum())} px")
    print(f"COMPOSIT: {n_fitxers} frames netejats, {n_px} px eliminats")

if __name__ == "__main__":
    print("=== Neteja retroactiva d'interferencies ===")
    neteja_echotop()
    neteja_composit()
    print("Fet.")
