"""
descarregar.py
==============
Descarrega el composit de radar AEMET (tota Espanya) i l'ECHOTOP per radar individual.
Desa les dades a data/YYYYMMDD/ com a PNG (visor) i NPZ (anàlisi).

Fonts (sense API key):
  - Composit nacional: https://www.aemet.es/en/api-eltiempo/radar/download/compo
  - ECHOTOP per radar: https://www.aemet.es/en/api-eltiempo/radar/download/TOP
"""

import io, json, os, sys, tarfile, time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import requests
from PIL import Image as PILImage

try:
    import rasterio
    from rasterio.io import MemoryFile
except ImportError:
    print("rasterio no disponible"); sys.exit(1)

# ── Configuració ──────────────────────────────────────────────────────────────
ROOT      = Path(__file__).parent.parent
DATA_DIR  = ROOT / "data"
ESP_LON   = (-9.5,  4.5)
ESP_LAT   = (35.5, 44.0)
OUT_W, OUT_H = 1400, 850   # resolució de sortida PNG

HEADERS = {
    "User-Agent": "GRAFRadarBot/1.0",
    "Cache-Control": "no-cache, no-store, must-revalidate",
    "Pragma": "no-cache",
}

# Colormap radar: valors 1–8 → RGBA
RADAR_RGBA = {
    1: (156, 252, 255, 170), 2: (3,  211, 255, 180),
    3: (90,  255,  90, 190), 4: (0,  200,   0, 200),
    5: (255, 255,   0, 215), 6: (255, 150,   0, 225),
    7: (255,   0,   0, 235), 8: (200,   0,   0, 245),
}

# Colormap ECHOTOP: RGB → rang numèric (per compositar)
ECHOTOP_SCALE = [
    ((130, 10,110), '> 20 km'), ((200,  0, 80), '16–20 km'),
    ((252,  0,  0), '14–16 km'),((255,107,  0), '12–14 km'),
    ((255,170,  0), '10–12 km'),((255,255,  0), '8–10 km'),
    ((0,  250,  0), '7–8 km'),  ((150,200,  0), '6–7 km'),
    ((67, 131, 35), '5–6 km'),  ((0,  255,255), '4–5 km'),
    ((0,  190,255), '3–4 km'),  ((0,  130,255), '2–3 km'),
    ((0,    0,252), '1–2 km'),
]
color_rang = {rgb: i+1 for i, (rgb, _) in enumerate(ECHOTOP_SCALE)}

# ── Helpers ───────────────────────────────────────────────────────────────────
def get(url):
    r = requests.get(f"{url}?_nocache={int(time.time())}", headers=HEADERS, timeout=30)
    r.raise_for_status()
    return r.content

def retall(banda, ds, lon=ESP_LON, lat=ESP_LAT):
    b = ds.bounds; w, h = ds.width, ds.height
    c0 = max(0, int((lon[0]-b.left)/(b.right-b.left)*w))
    c1 = min(w, int((lon[1]-b.left)/(b.right-b.left)*w))
    r0 = max(0, int((b.top-lat[1])/(b.top-b.bottom)*h))
    r1 = min(h, int((b.top-lat[0])/(b.top-b.bottom)*h))
    bounds = {"lon_min":b.left+c0*(b.right-b.left)/w,
              "lat_min":b.top -r1*(b.top-b.bottom)/h,
              "lon_max":b.left+c1*(b.right-b.left)/w,
              "lat_max":b.top -r0*(b.top-b.bottom)/h}
    return banda[r0:r1, c0:c1].copy(), bounds

def array_a_png(arr, colormap, size=(OUT_W, OUT_H)):
    rgba = np.zeros((*arr.shape, 4), dtype=np.uint8)
    for val, color in colormap.items():
        rgba[arr == val] = color
    img = PILImage.fromarray(rgba, "RGBA").resize(size, PILImage.NEAREST)
    buf = io.BytesIO(); img.save(buf, "PNG", optimize=True)
    return buf.getvalue()

def echotop_a_rgba(comp_rgba, size=(OUT_W, OUT_H)):
    img = PILImage.fromarray(comp_rgba, "RGBA").resize(size, PILImage.NEAREST)
    buf = io.BytesIO(); img.save(buf, "PNG", optimize=True)
    return buf.getvalue()

def ts_de_nom_compo(nom):
    # Usa regex per trobar qualsevol seqüència de 12-14 dígits al nom
    import re
    m = re.search(r'(\d{12,14})', nom)
    if m:
        ts = m.group(1)
        return ts + "00" if len(ts) == 12 else ts
    return None

def ts_de_nom_top(nom):
    n = nom.replace("./down_", "")
    codi, ts = n[:3], n[3:15]
    return codi, ts  # ts format: YYMMDDHHMMSS

def carregar_arxivats(dia_dir):
    idx = dia_dir / ".arxivats.txt"
    if not idx.exists(): return set()
    return set(idx.read_text().splitlines())

def marcar_arxivat(dia_dir, key):
    idx = dia_dir / ".arxivats.txt"
    with open(idx, "a") as f: f.write(key + "\n")

def desa_frame(dia_dir, nom, arr, meta, png_bytes, tipus="radar"):
    dia_dir.mkdir(parents=True, exist_ok=True)
    # PNG per al visor web
    (dia_dir / f"{nom}.png").write_bytes(png_bytes)
    # NPZ per a anàlisi (comprimit)
    np.savez_compressed(dia_dir / f"{nom}.npz", data=arr)
    # Metadades
    with open(dia_dir / f"{nom}.json", "w") as f:
        json.dump(meta, f, indent=2)
    marcar_arxivat(dia_dir, nom)
    print(f"  ✓ {nom}  px_actius={meta.get('px_actius',0)}")

# ── Composit radar nacional ───────────────────────────────────────────────────
def arxivar_composit():
    print("\n=== Composit radar Espanya ===")
    try:
        raw = get("https://www.aemet.es/en/api-eltiempo/radar/download/compo")
    except Exception as e:
        print(f"  ERROR: {e}"); return

    tf = tarfile.open(fileobj=io.BytesIO(raw))
    membres = sorted(tf.getmembers(), key=lambda m: m.name)

    print(f"  Membres TAR: {[m.name for m in membres[:3]]}")  # debug noms

    for membre in membres:
        ts = ts_de_nom_compo(membre.name)
        if not ts: continue

        dia_dir = DATA_DIR / "composit" / ts[:8]
        nom = f"radar_{ts}"
        if nom in carregar_arxivats(dia_dir): continue

        dades = tf.extractfile(membre).read()
        with MemoryFile(dades) as mf:
            with mf.open() as ds:
                arr, bounds = retall(ds.read(1).astype(np.uint8), ds)

        png = array_a_png(arr, RADAR_RGBA)
        meta = {
            "timestamp_utc": f"{ts[:4]}-{ts[4:6]}-{ts[6:8]}T{ts[8:10]}:{ts[10:12]}:00Z",
            "tipus": "composit_espanya",
            "bounds": bounds,
            "shape": list(arr.shape),
            "max_val": int(arr.max()),
            "px_actius": int((arr > 0).sum()),
        }
        desa_frame(dia_dir, nom, arr, meta, png, "radar")

# ── ECHOTOP per radar individual ─────────────────────────────────────────────
def arxivar_echotop():
    print("\n=== ECHOTOP per radar individual ===")
    try:
        raw = get("https://www.aemet.es/en/api-eltiempo/radar/download/TOP")
    except Exception as e:
        print(f"  ERROR: {e}"); return

    tf = tarfile.open(fileobj=io.BytesIO(raw))
    top_membres = [m for m in tf.getmembers() if ".TOP." in m.name]

    # Agrupa per timestamp
    per_ts = defaultdict(list)
    for m in top_membres:
        codi, ts = ts_de_nom_top(m.name)
        per_ts[ts].append((codi, m))

    if not per_ts: print("  Sense membres TOP"); return

    # Processa cada timestamp
    for ts, membres_ts in sorted(per_ts.items()):
        # Converteix ts de YYMMDDHHMMSS a YYYYMMDDHHMMSS
        ts_full = "20" + ts
        dia_dir_comp = DATA_DIR / "echotop" / "composit" / ts_full[:8]
        nom_comp = f"echotop_{ts_full}"

        # Composit de tots els radars
        comp_rang = np.zeros((OUT_H, OUT_W), dtype=np.int16)
        comp_rgba = np.zeros((OUT_H, OUT_W, 4), dtype=np.uint8)

        for codi, membre in membres_ts:
            dades = tf.extractfile(membre).read()
            with MemoryFile(dades) as mf:
                with mf.open() as ds:
                    b = ds.bounds; w = ds.width; h = ds.height
                    rgba = ds.read([1,2,3,4])

            c0 = int((ESP_LON[0]-b.left)/(b.right-b.left)*w)
            c1 = int((ESP_LON[1]-b.left)/(b.right-b.left)*w)
            r0 = int((b.top-ESP_LAT[1])/(b.top-b.bottom)*h)
            r1 = int((b.top-ESP_LAT[0])/(b.top-b.bottom)*h)
            if c0<0 or r0<0 or c1>w or r1>h or c1<=c0 or r1<=r0: continue

            cat = np.stack([rgba[0][r0:r1,c0:c1], rgba[1][r0:r1,c0:c1],
                            rgba[2][r0:r1,c0:c1], rgba[3][r0:r1,c0:c1]],
                           axis=-1).astype(np.uint8)
            pr = np.array(PILImage.fromarray(cat,"RGBA").resize((OUT_W,OUT_H),PILImage.NEAREST))

            rm = np.zeros((OUT_H,OUT_W), dtype=np.int16)
            for (rc,gc,bc), rang in color_rang.items():
                rm[(pr[:,:,0]==rc)&(pr[:,:,1]==gc)&(pr[:,:,2]==bc)] = rang
            upd = rm > comp_rang
            comp_rang[upd] = rm[upd]; comp_rgba[upd] = pr[upd]

            # Desa radar individual
            dia_dir_rad = DATA_DIR / "echotop" / codi / ts_full[:8]
            nom_rad = f"echotop_{codi}_{ts_full}"
            if nom_rad not in carregar_arxivats(dia_dir_rad):
                arr_ind = rm
                bounds_esp = {"lon_min":ESP_LON[0],"lat_min":ESP_LAT[0],
                              "lon_max":ESP_LON[1],"lat_max":ESP_LAT[1]}
                meta_ind = {
                    "timestamp_utc": f"20{ts[:2]}-{ts[2:4]}-{ts[4:6]}T{ts[6:8]}:{ts[8:10]}:00Z",
                    "tipus": f"echotop_{codi}",
                    "radar": codi,
                    "bounds": bounds_esp,
                    "shape": list(arr_ind.shape),
                    "px_actius": int((arr_ind > 0).sum()),
                }
                png_ind = echotop_a_rgba(pr)
                desa_frame(dia_dir_rad, nom_rad, arr_ind, meta_ind, png_ind, "echotop")

        # Desa composit
        comp_rgba[comp_rang==0, 3] = 0
        if nom_comp not in carregar_arxivats(dia_dir_comp):
            meta_comp = {
                "timestamp_utc": f"20{ts[:2]}-{ts[2:4]}-{ts[4:6]}T{ts[6:8]}:{ts[8:10]}:00Z",
                "tipus": "echotop_composit",
                "radars": [c for c,_ in membres_ts],
                "bounds": {"lon_min":ESP_LON[0],"lat_min":ESP_LAT[0],
                           "lon_max":ESP_LON[1],"lat_max":ESP_LAT[1]},
                "shape": [OUT_H, OUT_W],
                "px_actius": int((comp_rang > 0).sum()),
            }
            png_comp = echotop_a_rgba(comp_rgba)
            desa_frame(dia_dir_comp, nom_comp, comp_rang, meta_comp, png_comp, "echotop")

if __name__ == "__main__":
    print(f"Inici: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} UTC")
    arxivar_composit()
    arxivar_echotop()
    print("\nFet.")
