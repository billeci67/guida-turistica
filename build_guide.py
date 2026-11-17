#!/usr/bin/env python3
"""Genera una guida turistica HTML a partire da un file JSON di dati.

Uso:
  python build_guide.py dati.json guida.html     # controlla e genera
  python build_guide.py --distances dati.json    # tabella distanze stimate tra le tappe
  python build_guide.py --check dati.json        # solo controlli, nessun file
  python build_guide.py --links dati.json        # link Google Maps dell'intero percorso (a piedi, mezzi, auto)

Il modello (assets/template.html) legge tutto da un blocco JSON: qui si
controlla che i dati siano coerenti e li si inserisce nel modello.
"""
import argparse, json, math, re, sys
from urllib.parse import quote
from pathlib import Path

HERE = Path(__file__).resolve().parent
TEMPLATE = HERE.parent / "assets" / "template.html"

def haversine_km(a, b):
    r = 6371.0
    p1, p2 = math.radians(a["lat"]), math.radians(b["lat"])
    dp, dl = p2 - p1, math.radians(b["lon"] - a["lon"])
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(h))

def to_min(t):
    m = re.fullmatch(r"(\d{1,2}):(\d{2})", t or "")
    return int(m.group(1)) * 60 + int(m.group(2)) if m else None

def first_km(text):
    m = re.search(r"(\d+(?:[.,]\d+)?)\s*km", text or "")
    return float(m.group(1).replace(",", ".")) if m else None

MODES = {"walking": "A piedi", "transit": "Mezzi pubblici", "driving": "Auto"}

def route_points(d):
    """Punti dell'intero percorso: tutte le tappe (o google.route_ids) e il rientro alla partenza."""
    by_id = {s.get("id"): s for s in d.get("stops") or []}
    ids = (d.get("google") or {}).get("route_ids")
    route = [by_id[i] for i in ids if i in by_id] if ids else list(d.get("stops") or [])
    if d.get("return_leg") is not False and len(route) > 1:
        route.append(route[0])
    return [s for i, s in enumerate(route) if i == 0 or s is not route[i - 1]]

def ll(s):
    return f"{s['lat']:.4f},{s['lon']:.4f}"

def dir_url(pts, mode):
    via = "|".join(ll(s) for s in pts[1:-1])
    u = f"https://www.google.com/maps/dir/?api=1&origin={ll(pts[0])}&destination={ll(pts[-1])}"
    if via and mode != "transit":
        u += f"&waypoints={quote(via, safe='')}"
    return u + f"&travelmode={mode}"

def segments(d, mode):
    """Spezzoni per modalita': a piedi/auto max 11 punti (9 tappe intermedie), mezzi pubblici una coppia per volta."""
    route = route_points(d)
    if mode == "transit":
        return [route[i:i + 2] for i in range(len(route) - 1)]
    return [route[k:k + 11] for k in range(0, len(route) - 1, 10)]

def print_links(d):
    route = route_points(d)
    print(f"Percorso completo: {len(route)} punti"
          + (" (rientro alla partenza incluso)" if d.get("return_leg") is not False and len(route) > 1 else ""))
    modes = (d.get("google") or {}).get("modes") or list(MODES)
    for m in modes:
        segs = segments(d, m)
        print(f"\n## {MODES.get(m, m)} ({len(segs)} {('spostamento' if len(segs)==1 else 'spostamenti') if m == 'transit' else ('parte' if len(segs)==1 else 'parti')})")
        for i, pts in enumerate(segs, 1):
            print(f"{i}. {pts[0]['name'].split(' (')[0]} -> {pts[-1]['name'].split(' (')[0]}\n   {dir_url(pts, m)}")

def check(d):
    errors, warnings = [], []
    for k in ("title", "h1", "intro", "stops"):
        if not d.get(k):
            errors.append(f"Campo obbligatorio mancante: {k}")
    stops = d.get("stops") or []
    ids = set()
    prev_t = None
    for i, s in enumerate(stops, 1):
        n = f"Tappa {i} ({s.get('name', '?')})"
        for k in ("id", "name", "time", "dist", "lat", "lon", "hours", "price", "text", "shoot", "next"):
            if s.get(k) in (None, "", []):
                errors.append(f"{n}: manca '{k}'")
        if not (s.get("cid") or s.get("url")):
            errors.append(f"{n}: serve 'cid' oppure 'url' per il link Google Maps")
        if s.get("id") in ids:
            errors.append(f"{n}: id duplicato '{s.get('id')}'")
        ids.add(s.get("id"))
        if not isinstance(s.get("lat"), (int, float)) or not isinstance(s.get("lon"), (int, float)):
            errors.append(f"{n}: lat/lon devono essere numeri")
        t = to_min(s.get("time"))
        if t is None:
            errors.append(f"{n}: orario non valido '{s.get('time')}' (usa HH:MM)")
        elif prev_t is not None and t < prev_t:
            errors.append(f"{n}: l'orario {s['time']} è precedente alla tappa prima")
        prev_t = t if t is not None else prev_t
        photos = s.get("photos") or []
        if not photos and not s.get("photo_note"):
            warnings.append(f"{n}: nessuna foto e nessuna 'photo_note'")
        for u in photos:
            if not str(u).startswith("https://"):
                errors.append(f"{n}: URL foto non valido: {u}")
        if len(s.get("text") or []) < 2:
            warnings.append(f"{n}: descrizione molto breve (meno di 2 paragrafi)")
    # distanze: confronto con la stima in linea d'aria
    for a, b in zip(stops, stops[1:]):
        if b.get("leg") == "transit":
            continue
        km = first_km(b.get("dist"))
        if km:
            air = haversine_km(a, b)
            if air > 0.05 and not (0.8 * air <= km <= 2.2 * air + 0.2):
                warnings.append(
                    f"{b.get('name')}: distanza dichiarata {km} km, in linea d'aria {air:.2f} km (a piedi di solito 1.2-1.5x)")
    # waypoint Google (max 9)
    for b in (d.get("google") or {}).get("buttons") or []:
        if len(b.get("via_ids") or []) > 9:
            errors.append(f"Pulsante '{b.get('label')}': più di 9 tappe intermedie (limite Google Maps)")
        for ref in [b.get("from_id"), b.get("to_id")] + list(b.get("via_ids") or []):
            if ref not in ids:
                errors.append(f"Pulsante '{b.get('label')}': id sconosciuto '{ref}'")
    # mappa Google dell'intero percorso
    g = d.get("google") or {}
    for m in g.get("modes") or []:
        if m not in MODES:
            errors.append(f"google.modes: modalita' sconosciuta '{m}' (usa walking, transit, driving)")
    if g.get("default_mode") and g["default_mode"] not in (g.get("modes") or list(MODES)):
        errors.append(f"google.default_mode '{g['default_mode']}' non e' tra le modalita' attive")
    for ref in g.get("route_ids") or []:
        if ref not in ids:
            errors.append(f"google.route_ids: id sconosciuto '{ref}'")
    if len(stops) > 1 and not errors:
        rp = route_points(d)
        if len(rp) < 2:
            warnings.append("google.route_ids: servono almeno 2 punti per la mappa dell'intero percorso")
        elif len(rp) > 11:
            warnings.append(f"Mappa Google: {len(rp)} punti, il percorso a piedi/auto sara' diviso in {len(segments(d, 'walking'))} parti (limite di 9 tappe intermedie per link)")
    # sovrapposizione etichette sulla mappa schematica
    if len(stops) > 1 and all(isinstance(s.get("lat"), (int, float)) for s in stops):
        lats = [s["lat"] for s in stops]; lons = [s["lon"] for s in stops]
        k = math.cos(math.radians((min(lats) + max(lats)) / 2))
        span_x = (max(lons) - min(lons) + 0.006) * k
        span_y = max(lats) - min(lats) + 0.004
        pad, W = 70, 720
        H = max(380, min(780, round((W - 2 * pad) * span_y / span_x + 2 * pad)))
        sc = min((W - 2 * pad) / span_x, (H - 2 * pad) / span_y)
        pts = [((s["lon"] - (min(lons) - 0.003)) * k * sc, (max(lats) + 0.002 - s["lat"]) * sc) for s in stops]
        for i in range(len(stops)):
            for j in range(i + 1, len(stops)):
                dist = math.hypot(pts[i][0] - pts[j][0], pts[i][1] - pts[j][1])
                if dist < 36:
                    warnings.append(
                        f"Mappa: tappe {i+1} e {j+1} troppo vicine ({dist:.0f}px): cambia 'label_pos' (right/left/above/below) e guarda l'anteprima")
    return errors, warnings

def print_distances(d):
    stops = d["stops"]
    print(f"{'Da':28} {'A':28} {'linea d aria':>12} {'a piedi ~1.3x':>14}")
    tot = 0
    for a, b in zip(stops, stops[1:]):
        air = haversine_km(a, b)
        tot += air * 1.3
        print(f"{a['name'][:27]:28} {b['name'][:27]:28} {air:9.2f} km {air*1.3:11.2f} km")
    print(f"Totale stimato a piedi (se tutto a piedi): {tot:.1f} km")

def build(d, out):
    tpl = TEMPLATE.read_text(encoding="utf-8")
    blob = json.dumps(d, ensure_ascii=False).replace("</", "<\\/").replace("<!--", "<\\!--")
    title = (d.get("title") or "").replace("&", "&amp;").replace("<", "&lt;")
    html = tpl.replace("__TITLE__", title).replace("__GUIDE_JSON__", blob)
    Path(out).write_text(html, encoding="utf-8")

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("json_file")
    ap.add_argument("output", nargs="?")
    ap.add_argument("--distances", action="store_true")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--links", action="store_true")
    a = ap.parse_args()
    d = json.loads(Path(a.json_file).read_text(encoding="utf-8"))
    if a.distances:
        print_distances(d); return 0
    if a.links:
        print_links(d); return 0
    errors, warnings = check(d)
    for w in warnings: print("AVVISO:", w)
    for e in errors: print("ERRORE:", e)
    if errors:
        print(f"\n{len(errors)} errori: guida non generata."); return 1
    if a.check:
        print("Controlli superati."); return 0
    if not a.output:
        print("Indica il file di output."); return 2
    build(d, a.output)
    print(f"Guida generata: {a.output} ({len(d['stops'])} tappe, {len(warnings)} avvisi)")
    return 0

if __name__ == "__main__":
    sys.exit(main())
