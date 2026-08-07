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
from descarregar import (neteja_interferencies, _decode_top, _cap_classes_rang,
                         ESCALA_COLOR, RADARS_EXCLOSOS, RADAR_SITES, OUT_W, OUT_H)

_DGRIDS = {}
def graella_dist(codi):
    """Distancia (km) de cada pixel del domini al radar 'codi' (amb cache)."""
    if codi in _DGRIDS: return _DGRIDS[codi]
    site = RADAR_SITES.get(codi)
    if site is None:
        g = np.full((OUT_H, OUT_W), 400.0, np.float32)  # desconegut: minima prioritat
    else:
        lats = 44.0 - (np.arange(OUT_H) + 0.5) / 100.0
        lons = -9.5 + (np.arange(OUT_W) + 0.5) / 100.0
        dy = (lats - site[0]) * 110.57
        dx = (lons[None, :] - site[1]) * 111.32 * np.cos(np.radians(lats))[:, None]
        g = np.sqrt(dx ** 2 + dy[:, None] ** 2).astype(np.float32)
    _DGRIDS[codi] = g
    return g

ROOT = Path(__file__).parent.parent
ECHO = ROOT / "data" / "echotop"
REGLA = "radar_mes_proxim+cap_classe_rang"   # versio actual; frames amb esta marca es salten

def reconstruir():
    n_fets = n_skip = 0
    radar_dirs = [d for d in sorted(ECHO.iterdir())
                  if d.is_dir() and d.name != "composit"]
    for comp_png in sorted(ECHO.glob("composit/*/echotop_*.png")):
        if comp_png.name.endswith("_alt.png"): continue
        ts = comp_png.stem.replace("echotop_", "")
        dia = ts[:8]

        # Resumible: salta els frames ja reconstruits amb la regla actual
        json_f0 = comp_png.with_suffix(".json")
        if json_f0.exists():
            try:
                if json.loads(json_f0.read_text()).get("composit_regla") == REGLA:
                    continue
            except Exception: pass

        comp_alt  = np.zeros((OUT_H, OUT_W), np.uint8)
        comp_rgba = np.zeros((OUT_H, OUT_W, 4), np.uint8)
        comp_dist = np.full((OUT_H, OUT_W), np.inf, np.float32)
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
            dg = graella_dist(rd.name)
            # Limit de classe per distancia + recoloracio
            cls_cap = _cap_classes_rang(cls, dg)
            canvi = (cls_cap != cls) & (cls_cap > 0)
            if canvi.any():
                import numpy as _np
                for cl in _np.unique(cls_cap[canvi]):
                    rgba[canvi & (cls_cap == cl)] = ESCALA_COLOR[int(cl)]
            cls = cls_cap
            m = cls > 0
            # Regla de proximitat: guanya el radar amb dades mes proxim
            upd = m & (dg < comp_dist)
            comp_alt[upd]  = cls[upd]
            comp_rgba[upd] = rgba[upd]
            comp_rgba[upd, 3] = 255
            comp_dist[upd] = dg[upd]

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
        dist_u8 = np.where(np.isfinite(comp_dist),
                           np.clip(np.rint(comp_dist), 1, 254), 0).astype(np.uint8)
        buf = io.BytesIO()
        PILImage.fromarray(dist_u8, "L").save(buf, "PNG", optimize=True)
        comp_png.with_name(comp_png.stem + "_dist.png").write_bytes(buf.getvalue())

        json_f = comp_png.with_suffix(".json")
        if json_f.exists():
            try:
                meta = json.loads(json_f.read_text())
                meta["px_actius"] = int((comp_alt > 0).sum())
                meta["radars"] = radars
                meta["reconstruit_sense"] = sorted(RADARS_EXCLOSOS)
                meta["composit_regla"] = REGLA
                meta["dist_units"] = "km (uint8; 0=sense dades, 254=max)"
                json_f.write_text(json.dumps(meta, indent=2))
            except Exception: pass
        n_fets += 1
        print(f"  OK {comp_png.name}: {len(radars)} radars, px={int((comp_alt>0).sum())}")

    print(f"\nReconstruits {n_fets} composits ({n_skip} saltats)")

if __name__ == "__main__":
    print(f"=== Reconstruccio composits sense {sorted(RADARS_EXCLOSOS)} ===")
    reconstruir()
