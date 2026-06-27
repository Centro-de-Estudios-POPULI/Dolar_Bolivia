"""
Scraper diario del Tipo de Cambio Oficial (TCO) del dólar en Bolivia.

Contexto: tras el cambio de política monetaria, el BCB publica el TCO en una
plataforma propia. El TCO es el promedio ponderado de las operaciones de COMPRA
de divisas de la banca (misma lógica que el tipo de cambio de compra que ya
calculábamos). Su plataforma SOLO expone el reporte más reciente (para el próximo
día hábil); no hay histórico descargable, así que la serie la construimos nosotros
capturando a diario (data/tco.csv es la fuente de verdad).

Disposición BCB: el precio de VENTA oficial = TCO + 0,10 Bs (margen de 10 ctvs).

Fuente:
  https://www.bcb.gob.bo/tco_tcreferencial_descargar_csv.php   (CSV oficial; ; decimal coma)

Salidas:
  data/tco.csv    — serie diaria del TCO (fuente de verdad)
  data/tco.json   — dashboard: TCO, TCO venta (+0,10) y USDT por fecha
"""

import csv
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests

# Reusar el helper resiliente al WAF y el parser numérico del scraper principal.
from scrape_dolar import request_bcb, parse_num

# ── Config ─────────────────────────────────────────────────────────────────────
TCO_CSV_URL = "https://www.bcb.gob.bo/tco_tcreferencial_descargar_csv.php"
MARGEN_VENTA = 0.10  # disposición BCB: venta oficial = TCO + 10 ctvs

DATA_DIR  = Path(__file__).parent.parent / "data"
TCO_CSV   = DATA_DIR / "tco.csv"
TCO_JSON  = DATA_DIR / "tco.json"
DOLAR_CSV = DATA_DIR / "dolar.csv"          # para cruzar el USDT por fecha
CSV_FIELDS = ["fecha", "tco", "vol_usd", "tx", "timestamp_utc"]


# ── Descarga + parseo del CSV oficial ──────────────────────────────────────────

def fetch_tco() -> list[dict]:
    """
    Descarga el CSV del BCB y extrae, por cada fecha presente, el TCO ponderado
    (fila 'TCO' → columna TOTAL BANCOS = último valor), más el total de
    transacciones y monto (fila 'TOTAL' → últimos dos valores).

    Devuelve [{fecha, tco, vol_usd, tx}]. Hoy la plataforma trae una sola fecha,
    pero parseamos en grupos por fecha por si algún día habilitan rangos.
    """
    r = request_bcb(TCO_CSV_URL, timeout=30)
    # El CSV viene con BOM y separador ';'
    lineas = r.content.decode("utf-8-sig").splitlines()

    grupos: dict[str, dict] = {}
    for linea in lineas:
        cols = linea.split(";")
        if len(cols) < 3:
            continue
        fecha = cols[0].strip().strip('"')
        # Solo filas de datos (col 0 = fecha ISO YYYY-MM-DD)
        if len(fecha) != 10 or fecha[4] != "-" or not fecha[:4].isdigit():
            continue
        etiqueta = cols[1].strip().strip('"').upper()
        valores = [parse_num(x) for x in cols[2:] if x.strip()]
        valores = [v for v in valores if v is not None]

        if etiqueta == "TCO" and valores:
            # Último valor no vacío = columna "TOTAL BANCOS" (TCO global)
            grupos.setdefault(fecha, {})["tco"] = valores[-1]
        elif etiqueta == "TOTAL" and len(valores) >= 2:
            # Últimos dos valores = nº de transacciones y monto totales
            g = grupos.setdefault(fecha, {})
            g["tx"] = int(valores[-2])
            g["vol_usd"] = int(valores[-1])

    filas = []
    for fecha, g in sorted(grupos.items()):
        if "tco" not in g:
            continue
        filas.append({
            "fecha":   fecha,
            "tco":     g["tco"],
            "vol_usd": g.get("vol_usd"),
            "tx":      g.get("tx"),
        })
    return filas


# ── CSV (fuente de verdad) ──────────────────────────────────────────────────────

def leer_csv() -> list[dict]:
    if not TCO_CSV.exists():
        return []
    with open(TCO_CSV, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def agregar_filas_csv(nuevas: list[dict]) -> list[dict]:
    """Agrega solo fechas que no estén ya en el CSV. Devuelve el CSV completo."""
    existentes = leer_csv()
    fechas = {r["fecha"] for r in existentes}
    ts = datetime.now(timezone.utc).isoformat()

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    escribir_header = not TCO_CSV.exists() or TCO_CSV.stat().st_size == 0
    agregadas = 0
    with open(TCO_CSV, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        if escribir_header:
            writer.writeheader()
        for fila in nuevas:
            if fila["fecha"] in fechas:
                continue
            registro = {
                "fecha":   fila["fecha"],
                "tco":     fila["tco"],
                "vol_usd": fila.get("vol_usd"),
                "tx":      fila.get("tx"),
                "timestamp_utc": ts,
            }
            writer.writerow(registro)
            existentes.append(registro)
            fechas.add(fila["fecha"])
            agregadas += 1
            print(json.dumps(registro, ensure_ascii=False))

    if agregadas == 0:
        print("[INFO] Sin fechas nuevas de TCO; se regenera el JSON.")
    else:
        print(f"[OK] {agregadas} fecha(s) de TCO agregadas a tco.csv")
    return existentes


# ── USDT desde dolar.csv (para comparación) ─────────────────────────────────────

def usdt_por_fecha() -> dict[str, float]:
    """Mapa fecha → usdt_venta desde dolar.csv (si existe), para el overlay."""
    if not DOLAR_CSV.exists():
        return {}
    out = {}
    with open(DOLAR_CSV, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            v = row.get("usdt_venta")
            if v not in (None, ""):
                try:
                    out[row["fecha"]] = float(v)
                except ValueError:
                    pass
    return out


# ── JSON para el dashboard ──────────────────────────────────────────────────────

def exportar_json(csv_rows: list[dict]) -> None:
    usdt = usdt_por_fecha()
    serie = []
    for r in sorted(csv_rows, key=lambda x: x["fecha"]):
        try:
            tco = float(r["tco"])
        except (TypeError, ValueError):
            continue
        serie.append({
            "f": r["fecha"],
            "tco": tco,
            "tco_venta": round(tco + MARGEN_VENTA, 2),
            "usdt": usdt.get(r["fecha"]),
        })

    hoy = {}
    if serie:
        ult = serie[-1]
        ref = next((r for r in csv_rows if r["fecha"] == ult["f"]), {})
        hoy = {
            **ult,
            "vol_usd": int(ref["vol_usd"]) if ref.get("vol_usd") not in (None, "") else None,
            "tx":      int(ref["tx"])      if ref.get("tx")      not in (None, "") else None,
        }

    out = {
        "actualizado": datetime.now(timezone.utc).isoformat(),
        "margen_venta": MARGEN_VENTA,
        "hoy": hoy,
        "serie": serie,
    }
    TCO_JSON.write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"[OK] tco.json — {len(serie)} registro(s)")


# ── Main ────────────────────────────────────────────────────────────────────────

def main() -> None:
    print("Obteniendo TCO...")
    # El WAF del BCB devuelve 403 intermitente a los runners de GitHub. Si tras
    # los reintentos sigue bloqueado, conservamos lo ya guardado (CSV = verdad),
    # regeneramos el JSON y salimos en verde (no tumbamos el workflow).
    try:
        nuevas = fetch_tco()
    except requests.RequestException as e:
        print(f"[WARN] TCO no disponible (se conserva último dato): {e}", file=sys.stderr)
        nuevas = []
    except Exception as e:  # parseo inesperado: degradar, no romper
        print(f"[WARN] No se pudo parsear el TCO (se conserva último dato): {e}", file=sys.stderr)
        nuevas = []

    csv_rows = agregar_filas_csv(nuevas) if nuevas else leer_csv()
    exportar_json(csv_rows)


if __name__ == "__main__":
    main()
