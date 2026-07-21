"""
reconstruir_composit.py
=======================
Reconstrueix TOTS els composits ECHOTOP arxivats a partir dels frames
INDIVIDUALS per radar, excloent els radars de RADARS_EXCLOSOS (p.ex. LID).
Idempotent: es pot executar tantes vegades com calga.

Les classes es descodifiquen del color RGB de cada PNG individual (escala
oficial AEMET), aixi que funciona tambe per als frames antics sense _alt.png.
"""
import io, json, sys
from pathlib import Path

import numpy as np
from PIL import Image as PILImage

sys.path.insert(0, str(Path(__file__).parent))
from descarregar import (neteja_interferencies, _decode_top,
                         RADARS_EXCLOSOS, OUT_W, OUT_H)

ROOT = Path(__file__).parent.parent
ECHO = ROOT / "data" / "echotop"

def reconstruir():
    n_fets = n_skip = 0
    radar_dirs = [d for d in sorted(ECHO.iterdir())
                  if d.is_dir() and d.name != "composit"]
    for comp_png in sorted(ECHO.glob("composit/*/echotop_*.png")):
        if comp_png.name.endswith("_alt.png"): continue
        ts = comp_png.stem.replace("echotop_", "")
        dia = ts[:8]

        comp_alt  = np.zeros((OUT_H, OUT_W), np.uint8)
        comp_rgba = np.zeros((OUT_H, OUT_W, 4), np.uint8)
        radars, trobat = [], False

        for rd in radar_dirs:
            if rd.name in RADARS_EXCLOSOS: continue
            f = rd / dia / f"echotop_{rd.name}_{ts}.png"
            if not f.exists(): continue
            trobat = True
            rgba = np.array(PILImage.open(f).convert("RGBA"))
            if rgba.shape[:2] != (OUT_H, OUT_W): continue
            mask = rgba[:, :, 3] >= 240
            radars.append(rd.name)
            if not mask.any(): continue
            cls = _decode_top(rgba, mask)
            cls = neteja_interferencies(cls, min_area=2, filtre_rugositat=True)
            m = cls > 0
            upd = m & (cls > comp_alt)
            comp_alt[upd]  = cls[upd]
            comp_rgba[upd] = rgba[upd]
            comp_rgba[upd, 3] = 255

        if not trobat:
            n_skip += 1
            print(f"  SKIP {comp_png.name}: cap frame individual")
            continue

        buf = io.BytesIO()
        PILImage.fromarray(comp_rgba, "RGBA").save(buf, "PNG", optimize=True)
        comp_png.write_bytes(buf.getvalue())
        buf = io.BytesIO()
        PILImage.fromarray(comp_alt, "L").save(buf, "PNG", optimize=True)
        comp_png.with_name(comp_png.stem + "_alt.png").write_bytes(buf.getvalue())

        json_f = comp_png.with_suffix(".json")
        if json_f.exists():
            try:
                meta = json.loads(json_f.read_text())
                meta["px_actius"] = int((comp_alt > 0).sum())
                meta["radars"] = radars
                meta["reconstruit_sense"] = sorted(RADARS_EXCLOSOS)
                json_f.write_text(json.dumps(meta, indent=2))
            except Exception: pass
        n_fets += 1
        print(f"  OK {comp_png.name}: {len(radars)} radars, px={int((comp_alt>0).sum())}")

    print(f"\nReconstruits {n_fets} composits ({n_skip} saltats)")

if __name__ == "__main__":
    print(f"=== Reconstruccio composits sense {sorted(RADARS_EXCLOSOS)} ===")
    reconstruir()
