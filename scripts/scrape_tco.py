"""
Scraper diario del Tipo de Cambio Oficial (TCO) del dólar en Bolivia.

Contexto: tras el cambio de política monetaria (junio 2026), el BCB dejó de
publicar el "valor referencial" y pasó a publicar el TCO en una plataforma
propia. El TCO es el promedio ponderado de las operaciones de COMPRA de divisas
de la banca. Su plataforma SOLO expone el reporte más reciente (para el próximo
día hábil); NO hay histórico descargable, así que la serie la construimos
nosotros capturando a diario.

⚠️ RIESGO DE PÉRDIDA DE DATOS: el CSV del BCB trae el DETALLE POR BANCO (TCO,
nº de transacciones y monto por entidad) y la distribución por nivel de precio.
Como no hay backfill, cada día no capturado se pierde para siempre. Por eso este
scraper persiste TODO:

  data/tco.csv            — serie diaria GLOBAL del TCO (fuente de verdad)
  data/tco_bancos.csv     — detalle POR BANCO por día (TCO, tx, monto)   [tidy]
  data/tco_raw/<fecha>.csv — copia VERBATIM del reporte del BCB (red de seguridad
                             sin pérdida: preserva incluso la distribución por
                             nivel de precio que aún no exponemos en tidy)
  data/tco.json           — dashboard: serie global + venta (+0,10) + USDT +
                             detalle por banco del día

Disposición BCB: el precio de VENTA oficial = TCO + 0,10 Bs (margen de 10 ctvs).

Fuente:
  https://www.bcb.gob.bo/tco_tcreferencial_descargar_csv.php   (CSV oficial; ; decimal coma, BOM)
"""

import csv
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests

# Reusar helpers del scraper principal: HTTP resiliente al WAF, parser numérico
# en formato BCB y el normalizador de nombres de banco (mismos alias).
from scrape_dolar import request_bcb, parse_num, alias_banco

# ── Config ─────────────────────────────────────────────────────────────────────
TCO_CSV_URL = "https://www.bcb.gob.bo/tco_tcreferencial_descargar_csv.php"
MARGEN_VENTA = 0.10  # disposición BCB: venta oficial = TCO + 10 ctvs

DATA_DIR   = Path(__file__).parent.parent / "data"
TCO_CSV    = DATA_DIR / "tco.csv"
TCO_BANCOS = DATA_DIR / "tco_bancos.csv"
TCO_RAW    = DATA_DIR / "tco_raw"
TCO_JSON   = DATA_DIR / "tco.json"
DOLAR_CSV  = DATA_DIR / "dolar.csv"          # para cruzar el USDT por fecha

CSV_FIELDS    = ["fecha", "tco", "vol_usd", "tx", "timestamp_utc"]
BANCOS_FIELDS = ["fecha", "banco", "tco", "tx", "monto_usd", "timestamp_utc"]


# ── Descarga + parseo del CSV oficial ──────────────────────────────────────────

def _orden_bancos(lineas: list[str]) -> list[tuple[int, str]]:
    """
    Lee la fila de cabecera ('Fecha;"TC...";"BANCO BISA";;"BANCO ...";;...;
    "TOTAL BANCOS";') y devuelve [(indice_columna, nombre_banco)] en orden,
    incluyendo 'TOTAL BANCOS' al final. Cada entidad ocupa 2 sub-columnas
    (N°, Monto), así que los nombres viven en índices pares desde el 2.
    """
    for linea in lineas:
        cols = [c.strip().strip('"') for c in linea.split(";")]
        if cols and cols[0] == "Fecha":
            return [(i, cols[i]) for i in range(2, len(cols), 2) if i < len(cols) and cols[i]]
    return []


def _es_total(nombre: str) -> bool:
    return nombre.upper().startswith("TOTAL")


def parse_reporte(texto: str) -> dict[str, dict]:
    """
    Parsea el CSV del BCB en una estructura por fecha:
      { fecha: {
          'tco': float, 'vol_usd': int|None, 'tx': int|None,
          'bancos': { alias: {'tco': float|None, 'tx': int|None, 'monto': int|None} },
      }}
    Usa el orden de bancos de la cabecera (robusto si el BCB reordena columnas).
    Las filas de distribución por nivel de precio se ignoran en el tidy
    (quedan preservadas en el archivo raw). Solo se devuelven fechas con TCO.
    """
    lineas = texto.splitlines()
    orden = _orden_bancos(lineas)
    if not orden:
        raise ValueError("No se encontró la cabecera de bancos en el CSV del BCB")

    fechas: dict[str, dict] = {}
    for linea in lineas:
        cols = [c.strip().strip('"') for c in linea.split(";")]
        if len(cols) < 3:
            continue
        fecha = cols[0]
        if len(fecha) != 10 or fecha[4] != "-" or not fecha[:4].isdigit():
            continue
        etiqueta = cols[1].upper()
        if etiqueta not in ("TCO", "TOTAL"):
            continue  # fila de distribución por precio → solo en raw
        g = fechas.setdefault(fecha, {"bancos": {}})

        for idx, nombre in orden:
            if etiqueta == "TCO":
                val = parse_num(cols[idx]) if idx < len(cols) else None
                if _es_total(nombre):
                    g["tco"] = val
                else:
                    g["bancos"].setdefault(alias_banco(nombre), {})["tco"] = val
            else:  # TOTAL
                tx    = parse_num(cols[idx])     if idx < len(cols)     else None
                monto = parse_num(cols[idx + 1]) if idx + 1 < len(cols) else None
                if _es_total(nombre):
                    g["tx"]      = int(tx)    if tx    else None
                    g["vol_usd"] = int(monto) if monto else None
                else:
                    b = g["bancos"].setdefault(alias_banco(nombre), {})
                    b["tx"]    = int(tx)    if tx    else None
                    b["monto"] = int(monto) if monto else None

    return {f: g for f, g in fechas.items() if g.get("tco") is not None}


def fetch_reporte() -> tuple[dict[str, dict], bytes]:
    """Descarga el CSV del BCB. Devuelve (estructura_parseada, bytes_crudos)."""
    r = request_bcb(TCO_CSV_URL, timeout=30)
    texto = r.content.decode("utf-8-sig")
    return parse_reporte(texto), r.content


# ── Archivo raw (red de seguridad sin pérdida) ──────────────────────────────────

def guardar_raw(fechas: list[str], crudo: bytes) -> None:
    """Guarda el reporte verbatim del BCB, una copia por fecha presente."""
    if not fechas:
        return
    TCO_RAW.mkdir(parents=True, exist_ok=True)
    for fecha in fechas:
        destino = TCO_RAW / f"{fecha}.csv"
        # Idempotente: si ya existe con el mismo contenido, no reescribir.
        if destino.exists() and destino.read_bytes() == crudo:
            continue
        destino.write_bytes(crudo)
        print(f"[OK] raw archivado: {destino.name}")


# ── CSV global (fuente de verdad) ───────────────────────────────────────────────

def leer_csv(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def agregar_global_csv(reporte: dict[str, dict], ts: str) -> list[dict]:
    """Agrega al tco.csv solo fechas nuevas (global). Devuelve el CSV completo."""
    existentes = leer_csv(TCO_CSV)
    fechas = {r["fecha"] for r in existentes}

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    escribir_header = not TCO_CSV.exists() or TCO_CSV.stat().st_size == 0
    agregadas = 0
    with open(TCO_CSV, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        if escribir_header:
            writer.writeheader()
        for fecha, g in sorted(reporte.items()):
            if fecha in fechas:
                continue
            registro = {
                "fecha":   fecha,
                "tco":     g["tco"],
                "vol_usd": g.get("vol_usd"),
                "tx":      g.get("tx"),
                "timestamp_utc": ts,
            }
            writer.writerow(registro)
            existentes.append(registro)
            fechas.add(fecha)
            agregadas += 1
            print(json.dumps(registro, ensure_ascii=False))

    print(f"[OK] tco.csv — {agregadas} fecha(s) nueva(s)" if agregadas
          else "[INFO] Sin fechas nuevas de TCO; se regenera el JSON.")
    return existentes


def agregar_bancos_csv(reporte: dict[str, dict], ts: str) -> list[dict]:
    """
    Agrega al tco_bancos.csv el detalle por banco de cada fecha nueva.
    Escribe un panel completo (una fila por banco presente en el reporte,
    con nulos donde el banco no operó). Dedup por (fecha, banco).
    """
    existentes = leer_csv(TCO_BANCOS)
    vistos = {(r["fecha"], r["banco"]) for r in existentes}

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    escribir_header = not TCO_BANCOS.exists() or TCO_BANCOS.stat().st_size == 0
    agregadas = 0
    with open(TCO_BANCOS, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=BANCOS_FIELDS)
        if escribir_header:
            writer.writeheader()
        for fecha, g in sorted(reporte.items()):
            for banco, b in sorted(g["bancos"].items()):
                if (fecha, banco) in vistos:
                    continue
                registro = {
                    "fecha":     fecha,
                    "banco":     banco,
                    "tco":       b.get("tco"),
                    "tx":        b.get("tx"),
                    "monto_usd": b.get("monto"),
                    "timestamp_utc": ts,
                }
                writer.writerow(registro)
                existentes.append(registro)
                vistos.add((fecha, banco))
                agregadas += 1

    print(f"[OK] tco_bancos.csv — {agregadas} fila(s) banco-día nueva(s)")
    return existentes


# ── USDT desde dolar.csv (para comparación) ─────────────────────────────────────

def usdt_por_fecha() -> dict[str, float]:
    """Mapa fecha → usdt_venta desde dolar.csv (si existe), para el overlay."""
    out = {}
    for row in leer_csv(DOLAR_CSV):
        v = row.get("usdt_venta")
        if v not in (None, ""):
            try:
                out[row["fecha"]] = float(v)
            except ValueError:
                pass
    return out


# ── JSON para el dashboard ──────────────────────────────────────────────────────

def exportar_json(global_rows: list[dict], bancos_rows: list[dict]) -> None:
    usdt = usdt_por_fecha()

    serie = []
    for r in sorted(global_rows, key=lambda x: x["fecha"]):
        try:
            tco = float(r["tco"])
        except (TypeError, ValueError):
            continue
        serie.append({
            "f": r["fecha"],
            "tco": tco,
            "tco_venta": round(tco + MARGEN_VENTA, 2),
            "usdt": usdt.get(r["fecha"]),
            "vol": int(r["vol_usd"]) if r.get("vol_usd") not in (None, "") else None,
            "tx":  int(r["tx"])      if r.get("tx")      not in (None, "") else None,
        })

    # Detalle por banco del último día con datos
    bancos_hoy = []
    fecha_hoy = serie[-1]["f"] if serie else None
    if fecha_hoy:
        for r in bancos_rows:
            if r["fecha"] != fecha_hoy:
                continue
            def _num(v, fn):
                return fn(v) if v not in (None, "") else None
            bancos_hoy.append({
                "banco": r["banco"],
                "tco":   _num(r.get("tco"), float),
                "tx":    _num(r.get("tx"), int),
                "monto": _num(r.get("monto_usd"), int),
            })
        # Más activos primero (por monto), inactivos al final
        bancos_hoy.sort(key=lambda x: -(x.get("monto") or 0))

    hoy = {}
    if serie:
        ult = serie[-1]
        ref = next((r for r in global_rows if r["fecha"] == ult["f"]), {})
        hoy = {
            **ult,
            "vol_usd": int(ref["vol_usd"]) if ref.get("vol_usd") not in (None, "") else None,
            "tx":      int(ref["tx"])      if ref.get("tx")      not in (None, "") else None,
        }

    # Serie POR BANCO sobre las fechas del TCO (para continuar el gráfico de
    # volumen por entidad y calcular promedios/cambios en la tabla).
    fechas_b = sorted({r["fecha"] for r in bancos_rows})
    bancos_set = sorted({r["banco"] for r in bancos_rows})
    idx = {(r["fecha"], r["banco"]): r for r in bancos_rows}

    def _n(v, fn):
        return fn(v) if v not in (None, "") else None

    bancos_series = {}
    for b in bancos_set:
        vol, tco_s, tx_s = [], [], []
        for f in fechas_b:
            r = idx.get((f, b))
            vol.append(_n(r and r.get("monto_usd"), int)   if r else None)
            tco_s.append(_n(r and r.get("tco"),     float) if r else None)
            tx_s.append(_n(r and r.get("tx"),       int)   if r else None)
        bancos_series[b] = {"vol": vol, "tco": tco_s, "tx": tx_s}

    out = {
        "actualizado": datetime.now(timezone.utc).isoformat(),
        "margen_venta": MARGEN_VENTA,
        "hoy": hoy,
        "serie": serie,
        "fecha_hoy": fecha_hoy,
        "bancos_hoy": bancos_hoy,
        "bancos_fechas": fechas_b,
        "bancos_series": bancos_series,
    }
    TCO_JSON.write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"[OK] tco.json — {len(serie)} registro(s), {len(bancos_hoy)} banco(s) hoy")


# ── Main ────────────────────────────────────────────────────────────────────────

def main() -> None:
    print("Obteniendo TCO...")
    # El WAF del BCB devuelve 403 intermitente a los runners de GitHub. Si tras
    # los reintentos sigue bloqueado, conservamos lo ya guardado (los CSV son la
    # verdad), regeneramos el JSON y salimos en verde (no tumbamos el workflow).
    try:
        reporte, crudo = fetch_reporte()
    except requests.RequestException as e:
        print(f"[WARN] TCO no disponible (se conserva último dato): {e}", file=sys.stderr)
        reporte = {}
    except Exception as e:  # parseo inesperado: degradar, no romper
        print(f"[WARN] No se pudo parsear el TCO (se conserva último dato): {e}", file=sys.stderr)
        reporte = {}

    if reporte:
        ts = datetime.now(timezone.utc).isoformat()
        guardar_raw(sorted(reporte.keys()), crudo)
        global_rows = agregar_global_csv(reporte, ts)
        bancos_rows = agregar_bancos_csv(reporte, ts)
    else:
        global_rows = leer_csv(TCO_CSV)
        bancos_rows = leer_csv(TCO_BANCOS)

    exportar_json(global_rows, bancos_rows)


if __name__ == "__main__":
    main()
