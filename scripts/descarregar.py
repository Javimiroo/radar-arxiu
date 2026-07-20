"""
descarregar.py
==============
Descarrega el composit de radar AEMET (tota Espanya) i l'ECHOTOP per radar individual.
Desa les dades a data/YYYYMMDD/ com a PNG (visor) i NPZ (analisi).

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

try:
    from scipy import ndimage
    _SCIPY = True
except ImportError:
    _SCIPY = False
    print("AVIS: scipy no disponible, sense filtre d'interferencies")

# Configuracio
ROOT      = Path(__file__).parent.parent
DATA_DIR  = ROOT / "data"
ESP_LON   = (-9.5,  4.5)
ESP_LAT   = (35.5, 44.0)
OUT_W, OUT_H = 1400, 850

HEADERS = {
    "User-Agent": "GRAFRadarBot/1.0",
    "Cache-Control": "no-cache, no-store, must-revalidate",
    "Pragma": "no-cache",
}

RADAR_RGBA = {
    # Colors exactes del colormap AEMET (ds.colormap(1) del GeoTIFF composit)
    1:  (239, 242, 249,  80),  #  5-15 dBZ — quasi blanc (molt feble)
    2:  (  0,   0, 252, 155),  # 15-20 dBZ — blau
    3:  (  0, 148, 252, 170),  # 20-25 dBZ — blau clar
    4:  (  0, 252, 252, 182),  # 25-30 dBZ — cian
    5:  ( 67, 131,  35, 193),  # 30-35 dBZ — verd fosc
    6:  (  0, 192,   0, 203),  # 35-40 dBZ — verd
    7:  (  0, 255,   0, 213),  # 40-45 dBZ — verd brillant
    8:  (255, 255,   0, 220),  # 45-50 dBZ — groc
    9:  (255, 187,   0, 228),  # 50-55 dBZ — ambre
    10: (255, 127,   0, 236),  # 55-60 dBZ — taronja
    11: (255,   0,   0, 244),  # 60-65 dBZ — roig
    12: (200,   0,  90, 255),  # >65 dBZ   — magenta fosc
}

# Helpers generals
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
    img = PILImage.fromarray(rgba, "RGBA").resize(size, PILImage.BILINEAR)
    buf = io.BytesIO(); img.save(buf, "PNG", optimize=True)
    return buf.getvalue()

def _rugositat(z, lab, i, sl):
    """Salt mitja de classe entre pixels veins dins d'un component.
    Ecos reals: camp suau (<1). Interferencies RF: salts brutals (>1.5)."""
    comp = (lab[sl] == i)
    v = z[sl].astype(np.int16)
    salts = []
    m_h = comp[:, :-1] & comp[:, 1:]
    if m_h.any(): salts.append(np.abs(v[:, :-1] - v[:, 1:])[m_h])
    m_v = comp[:-1, :] & comp[1:, :]
    if m_v.any(): salts.append(np.abs(v[:-1, :] - v[1:, :])[m_v])
    if not salts: return 0.0
    return float(np.concatenate(salts).mean())

def neteja_interferencies(classes, min_area=2, filtre_rugositat=False):
    """Elimina interferencies RF i clutter puntual:
    - components de <= min_area pixels (pixels aillats)
    - linies fines llargues (spokes RF radials)
    - components esqueletics (spokes diagonals, densitat molt baixa)
    - [rugositat] components menuts amb classes barrejades caoticament:
      un echotop real es un camp suau; la interferencia salta de 2 a 16 km
      entre pixels veins. Nomes s'aplica a components <= 60 px, aixi les
      tempestes i pirocumuls reals no es toquen mai."""
    if not _SCIPY: return classes
    lab, nlab = ndimage.label(classes > 0, structure=np.ones((3, 3)))
    if nlab == 0: return classes
    objs = ndimage.find_objects(lab)
    sizes = ndimage.sum(classes > 0, lab, range(1, nlab + 1))
    treu = np.zeros(nlab + 1, bool)
    for i, sl in enumerate(objs, start=1):
        area = sizes[i - 1]
        h = sl[0].stop - sl[0].start
        w = sl[1].stop - sl[1].start
        if area <= min_area:
            treu[i] = True                       # pixels aillats
        elif max(h, w) >= 6 and min(h, w) <= 2:
            treu[i] = True                       # linia fina (spoke RF)
        elif max(h, w) >= 8 and area / (h * w) < 0.15:
            treu[i] = True                       # spoke diagonal
        elif filtre_rugositat and area <= 60:
            if _rugositat(classes, lab, i, sl) >= 1.5:
                treu[i] = True                   # classes barrejades = RF
    classes = classes.copy()
    classes[treu[lab]] = 0
    return classes

def ts_de_nom_compo(nom):
    import re
    m = re.search(r'(\d{12,14})', nom)
    if m:
        ts = m.group(1)
        return ts + "00" if len(ts) == 12 else ts
    return None

def ts_de_nom_top(nom):
    n = nom.replace("./down_", "")
    codi, ts = n[:3], n[3:15]
    return codi, ts

def carregar_arxivats(dia_dir):
    idx = dia_dir / ".arxivats.txt"
    if not idx.exists(): return set()
    return set(idx.read_text().splitlines())

def marcar_arxivat(dia_dir, key):
    idx = dia_dir / ".arxivats.txt"
    with open(idx, "a") as f: f.write(key + "\n")

def desa_frame(dia_dir, nom, arr, meta, png_bytes, tipus="radar"):
    dia_dir.mkdir(parents=True, exist_ok=True)
    (dia_dir / f"{nom}.png").write_bytes(png_bytes)
    np.savez_compressed(dia_dir / f"{nom}.npz", data=arr)
    with open(dia_dir / f"{nom}.json", "w") as f:
        json.dump(meta, f, indent=2)
    marcar_arxivat(dia_dir, nom)
    print(f"  OK {nom}  px_actius={meta.get('px_actius',0)}")

# Composit radar nacional
def arxivar_composit():
    print("\n=== Composit radar Espanya ===")
    try:
        raw = get("https://www.aemet.es/en/api-eltiempo/radar/download/compo")
    except Exception as e:
        print(f"  ERROR: {e}"); return

    tf = tarfile.open(fileobj=io.BytesIO(raw))
    membres = sorted(tf.getmembers(), key=lambda m: m.name)

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
        arr = neteja_interferencies(arr, min_area=1)
        png = array_a_png(arr, RADAR_RGBA)
        meta = {
            "timestamp_utc": f"{ts[:4]}-{ts[4:6]}-{ts[6:8]}T{ts[8:10]}:{ts[10:12]}:00Z",
            "tipus": "composit_espanya", "bounds": bounds,
            "shape": list(arr.shape), "max_val": int(arr.max()),
            "px_actius": int((arr > 0).sum()),
        }
        desa_frame(dia_dir, nom, arr, meta, png, "radar")

# Helpers ECHOTOP
# Escala oficial AEMET (tag ESCALA del GeoTIFF TOP): color RGB → alçada en km.
# L'index de classe (1-13) es guarda al *_alt.png; el visor el tradueix a rang de km.
ESCALA_TOP = [
    # (R, G, B), classe
    ((  0,   0, 252),  1),  # 1-2 km
    ((  0, 130, 255),  2),  # 2-3 km
    ((  0, 190, 255),  3),  # 3-4 km
    ((  0, 255, 255),  4),  # 4-5 km
    (( 67, 131,  35),  5),  # 5-6 km
    ((150, 200,   0),  6),  # 6-7 km
    ((  0, 250,   0),  7),  # 7-8 km
    ((255, 255,   0),  8),  # 8-10 km
    ((255, 170,   0),  9),  # 10-12 km
    ((255, 107,   0), 10),  # 12-14 km
    ((252,   0,   0), 11),  # 14-16 km
    ((200,   0,  80), 12),  # 16-20 km
    ((130,  10, 110), 13),  # >20 km
]

def _mask_echotop(patch):
    # Dades reals: alfa >= 240 (fons gris A=179, zona fora d'abast A=0)
    return patch[:,:,3] >= 240

def _decode_top(patch, mask):
    """Converteix color RGB -> classe d'alçada (1-13) per coincidencia mes propera."""
    classes = np.zeros(patch.shape[:2], dtype=np.uint8)
    if not mask.any(): return classes
    px = patch[mask][:, :3].astype(np.int32)          # (N,3)
    pal = np.array([c for c, _ in ESCALA_TOP], np.int32)  # (13,3)
    ids = np.array([i for _, i in ESCALA_TOP], np.uint8)
    d2 = ((px[:, None, :] - pal[None, :, :]) ** 2).sum(axis=2)  # (N,13)
    classes[mask] = ids[d2.argmin(axis=1)]
    return classes

def _col_loca_radar(rgba, b, w, h, esp_lon, esp_lat, out_w, out_h):
    """
    FIX: clampem bounds en lloc de saltar-nos el radar (antic: c1>w -> continue).
    Retorna (patch_rgba, ox0, oy0, ox1, oy1) o None si no hi ha solapament.
    """
    c0_f = (esp_lon[0]-b.left)/(b.right-b.left)*w
    c1_f = (esp_lon[1]-b.left)/(b.right-b.left)*w
    r0_f = (b.top-esp_lat[1])/(b.top-b.bottom)*h
    r1_f = (b.top-esp_lat[0])/(b.top-b.bottom)*h

    c0 = max(0, int(c0_f)); c1 = min(w, int(c1_f))
    r0 = max(0, int(r0_f)); r1 = min(h, int(r1_f))
    if c1 <= c0 or r1 <= r0: return None

    lon0 = b.left + c0/w*(b.right-b.left)
    lon1 = b.left + c1/w*(b.right-b.left)
    lat1 = b.top  - r0/h*(b.top-b.bottom)
    lat0 = b.top  - r1/h*(b.top-b.bottom)

    ox0 = max(0, int((lon0-esp_lon[0])/(esp_lon[1]-esp_lon[0])*out_w))
    ox1 = min(out_w, int((lon1-esp_lon[0])/(esp_lon[1]-esp_lon[0])*out_w))
    oy0 = max(0, int((esp_lat[1]-lat1)/(esp_lat[1]-esp_lat[0])*out_h))
    oy1 = min(out_h, int((esp_lat[1]-lat0)/(esp_lat[1]-esp_lat[0])*out_h))
    if ox1 <= ox0 or oy1 <= oy0: return None

    cat = np.stack([rgba[i][r0:r1, c0:c1] for i in range(4)], axis=-1).astype(np.uint8)
    patch = np.array(PILImage.fromarray(cat, "RGBA").resize((ox1-ox0, oy1-oy0), PILImage.NEAREST))
    return patch, ox0, oy0, ox1, oy1

# ECHOTOP per radar individual
def arxivar_echotop():
    print("\n=== ECHOTOP per radar individual ===")
    try:
        raw = get("https://www.aemet.es/en/api-eltiempo/radar/download/TOP")
    except Exception as e:
        print(f"  ERROR: {e}"); return

    tf = tarfile.open(fileobj=io.BytesIO(raw))
    top_membres = [m for m in tf.getmembers() if ".TOP." in m.name]

    per_ts = defaultdict(list)
    for m in top_membres:
        codi, ts = ts_de_nom_top(m.name)
        per_ts[ts].append((codi, m))

    if not per_ts: print("  Sense membres TOP"); return

    for ts, membres_ts in sorted(per_ts.items()):
        ts_full = "20" + ts
        dia_dir_comp = DATA_DIR / "echotop" / "composit" / ts_full[:8]
        nom_comp = f"echotop_{ts_full}"

        comp_alt  = np.zeros((OUT_H, OUT_W), dtype=np.uint8)
        comp_rgba = np.zeros((OUT_H, OUT_W, 4), dtype=np.uint8)

        for codi, membre in membres_ts:
            dades = tf.extractfile(membre).read()
            with MemoryFile(dades) as mf:
                with mf.open() as ds:
                    rgba = ds.read([1,2,3,4])
                    b = ds.bounds; dw = ds.width; dh = ds.height

            res = _col_loca_radar(rgba, b, dw, dh, ESP_LON, ESP_LAT, OUT_W, OUT_H)
            if res is None: continue
            patch, ox0, oy0, ox1, oy1 = res

            data_mask = _mask_echotop(patch)
            alt = _decode_top(patch, data_mask)
            alt = neteja_interferencies(alt, min_area=2, filtre_rugositat=True)
            data_mask = alt > 0

            reg_alt  = comp_alt [oy0:oy1, ox0:ox1]
            reg_rgba = comp_rgba[oy0:oy1, ox0:ox1]
            upd = data_mask & (alt > reg_alt)
            reg_alt [upd] = alt[upd]
            reg_rgba[upd] = patch[upd]
            reg_rgba[upd, 3] = 255

            dia_dir_rad = DATA_DIR / "echotop" / codi / ts_full[:8]
            nom_rad = f"echotop_{codi}_{ts_full}"
            if nom_rad not in carregar_arxivats(dia_dir_rad):
                canvas_ind = np.zeros((OUT_H, OUT_W, 4), dtype=np.uint8)
                canvas_ind_alt = np.zeros((OUT_H, OUT_W), dtype=np.uint8)
                sub = canvas_ind[oy0:oy1, ox0:ox1]
                sub_alt = canvas_ind_alt[oy0:oy1, ox0:ox1]
                sub[data_mask] = patch[data_mask]
                sub[data_mask, 3] = 255
                sub_alt[data_mask] = alt[data_mask]
                n_act = int(data_mask.sum())
                meta_ind = {
                    "timestamp_utc": f"20{ts[:2]}-{ts[2:4]}-{ts[4:6]}T{ts[6:8]}:{ts[8:10]}:00Z",
                    "tipus": f"echotop_{codi}", "radar": codi,
                    "bounds": {"lon_min":ESP_LON[0],"lat_min":ESP_LAT[0],
                               "lon_max":ESP_LON[1],"lat_max":ESP_LAT[1]},
                    "shape": [OUT_H, OUT_W], "px_actius": n_act,
                    "alt_units": "classe_escala_top_1_13",
                }
                buf = io.BytesIO()
                PILImage.fromarray(canvas_ind, "RGBA").save(buf, "PNG", optimize=True)
                desa_frame(dia_dir_rad, nom_rad,
                           np.zeros((1,1), dtype=np.int16), meta_ind, buf.getvalue(), "echotop")
                # Desa mapa d'alçades
                buf_alt = io.BytesIO()
                PILImage.fromarray(canvas_ind_alt, "L").save(buf_alt, "PNG", optimize=True)
                dia_dir_rad.mkdir(parents=True, exist_ok=True)
                (dia_dir_rad / f"{nom_rad}_alt.png").write_bytes(buf_alt.getvalue())

        if nom_comp not in carregar_arxivats(dia_dir_comp):
            n_act = int((comp_alt > 0).sum())
            meta_comp = {
                "timestamp_utc": f"20{ts[:2]}-{ts[2:4]}-{ts[4:6]}T{ts[6:8]}:{ts[8:10]}:00Z",
                "tipus": "echotop_composit",
                "radars": [c for c,_ in membres_ts],
                "bounds": {"lon_min":ESP_LON[0],"lat_min":ESP_LAT[0],
                           "lon_max":ESP_LON[1],"lat_max":ESP_LAT[1]},
                "shape": [OUT_H, OUT_W], "px_actius": n_act,
                "alt_units": "classe_escala_top_1_13",
            }
            buf = io.BytesIO()
            PILImage.fromarray(comp_rgba, "RGBA").save(buf, "PNG", optimize=True)
            desa_frame(dia_dir_comp, nom_comp,
                       np.zeros((1,1), dtype=np.int16), meta_comp, buf.getvalue(), "echotop")
            # Desa mapa d'alçades com a PNG greyscale (1 unitat = 50m)
            buf_alt = io.BytesIO()
            PILImage.fromarray(comp_alt, "L").save(buf_alt, "PNG", optimize=True)
            dia_dir_comp.mkdir(parents=True, exist_ok=True)
            (dia_dir_comp / f"{nom_comp}_alt.png").write_bytes(buf_alt.getvalue())

if __name__ == "__main__":
    print(f"Inici: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} UTC")
    arxivar_composit()
    arxivar_echotop()
    print("\nFet.")
