"""
recuperar_dies.py
=================
Recupera de l'historial de git els dies que netejar.py va esborrar.
EXECUTAR A LA BRANCA 'arxiu' (git checkout arxiu). Nomes usa git.
Despres: python scripts/manifest.py + git add + commit + push.
"""
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

def git(*args):
    r = subprocess.run(["git"] + list(args), capture_output=True,
                       text=True, encoding="utf-8", errors="replace", cwd=ROOT)
    if r.returncode != 0 and r.stderr:
        print("  AVIS:", r.stderr.strip()[:300])
    return r.stdout or ""

print("Buscant carpetes de dia esborrades a l'historial...")
out = git("log", "--diff-filter=D", "--format=#%H", "--name-only", "--", "data/")

sha = None
per_sha = {}
for line in out.splitlines():
    if line.startswith("#"):
        sha = line[1:].strip()
        continue
    line = line.strip()
    if not line:
        continue
    parts = Path(line).parts
    dd = None
    if len(parts) >= 3 and parts[0] == "data":
        for i, seg in enumerate(parts):
            if len(seg) == 8 and seg.isdigit():
                dd = str(Path(*parts[:i + 1]))
                break
    if dd and not (ROOT / dd).exists():
        per_sha.setdefault(sha, set()).add(dd)

total = 0
for sha, dirs in per_sha.items():
    pend = sorted(d for d in dirs if not (ROOT / d).exists())
    if not pend:
        continue
    print(f"Recuperant {len(pend)} carpetes des de {sha[:10]}^ ...")
    git("checkout", sha + "^", "--", *pend)
    total += len(pend)

print(f"\nFet: {total} carpetes recuperades.")
print("Ara: python scripts/manifest.py && git add data/ manifest.json && commit && push")
