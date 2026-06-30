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
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import requests

# Reusar helpers del scraper principal: HTTP resiliente al WAF, parser numérico
# en formato BCB y el normalizador de nombres de banco (mismos alias).
from scrape_dolar import request_bcb, parse_num, alias_banco

# ── Config ─────────────────────────────────────────────────────────────────────
TCO_CSV_URL = "https://www.bcb.gob.bo/tco_tcreferencial_descargar_csv.php"
MARGEN_VENTA = 0.10  # disposición BCB: venta oficial = TCO + 10 ctvs

# Vigencia: el TCO que fija la sesión de un día RIGE el día hábil siguiente. Para
# graficar cada TCO en el día que rige, saltamos fines de semana y feriados
# nacionales (lista mantenible; un feriado faltante solo desfasa 1 día esa vez).
BO_FERIADOS = {
    "2026-01-01", "2026-01-22", "2026-02-16", "2026-02-17", "2026-04-03",
    "2026-05-01", "2026-06-04", "2026-06-21", "2026-08-06", "2026-10-12",
    "2026-11-02", "2026-12-25",
    "2027-01-01", "2027-01-22",
}


def siguiente_dia_habil(fecha_iso: str) -> str:
    """Día hábil siguiente a `fecha_iso` (salta sábado/domingo y feriados BO)."""
    d = date.fromisoformat(fecha_iso)
    while True:
        d += timedelta(days=1)
        if d.weekday() >= 5 or d.isoformat() in BO_FERIADOS:
            continue
        return d.isoformat()

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
          'dist':   { alias: [ (precio, n_tx), ... ] },   # distribución por nivel de precio
      }}
    Usa el orden de bancos de la cabecera (robusto si el BCB reordena columnas).
    Las filas de distribución por nivel de precio (cada TC negociado, con nº de
    transacciones por banco) se recogen en 'dist' para el boxplot. Solo se
    devuelven fechas con TCO.
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
        g = fechas.setdefault(fecha, {"bancos": {}, "dist": {}})
        etiqueta = cols[1].upper()

        if etiqueta == "TCO":
            for idx, nombre in orden:
                val = parse_num(cols[idx]) if idx < len(cols) else None
                if _es_total(nombre):
                    g["tco"] = val
                else:
                    g["bancos"].setdefault(alias_banco(nombre), {})["tco"] = val
        elif etiqueta == "TOTAL":
            for idx, nombre in orden:
                tx    = parse_num(cols[idx])     if idx < len(cols)     else None
                monto = parse_num(cols[idx + 1]) if idx + 1 < len(cols) else None
                if _es_total(nombre):
                    g["tx"]      = int(tx)    if tx    else None
                    g["vol_usd"] = int(monto) if monto else None
                else:
                    b = g["bancos"].setdefault(alias_banco(nombre), {})
                    b["tx"]    = int(tx)    if tx    else None
                    b["monto"] = int(monto) if monto else None
        else:
            # Fila de distribución: cols[1] es un TC negociado; por banco viene el
            # nº de transacciones a ese precio. Acumulamos (precio, n_tx) por banco.
            precio = parse_num(cols[1])
            if precio is None:
                continue
            for idx, nombre in orden:
                if _es_total(nombre):
                    continue
                ntx = parse_num(cols[idx]) if idx < len(cols) else None
                if not ntx:
                    continue
                g["dist"].setdefault(alias_banco(nombre), []).append((precio, int(ntx)))

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


def _canon(v) -> str:
    """Forma canónica para comparar/escribir un valor (None → cadena vacía)."""
    return "" if v is None else str(v)


def upsert_csv(path: Path, fields: list[str], key_fields: list[str],
               nuevos: list[dict], ts: str) -> tuple[list[dict], int]:
    """
    Upsert idempotente sobre un CSV. `nuevos` = filas con valores tipados (sin
    timestamp). Inserta claves nuevas y REEMPLAZA filas cuyos valores cambian
    (comparando todo menos timestamp_utc) — así el cierre de las 20:00 refresca
    la captura parcial intradía. No reescribe si nada cambió (evita commits de
    ruido). Devuelve (filas_resultantes, n_cambios).
    """
    existentes = leer_csv(path)
    by_key = {tuple(r[k] for k in key_fields): r for r in existentes}
    val_fields = [f for f in fields if f not in key_fields and f != "timestamp_utc"]
    cambios = 0
    for nv in nuevos:
        key = tuple(_canon(nv[k]) for k in key_fields)
        row = {**{f: _canon(nv.get(f)) for f in fields if f != "timestamp_utc"},
               "timestamp_utc": ts}
        prev = by_key.get(key)
        if prev is None or any(prev.get(f, "") != row[f] for f in val_fields):
            by_key[key] = row
            cambios += 1
    if cambios:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        filas = sorted(by_key.values(), key=lambda r: tuple(r[k] for k in key_fields))
        with open(path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fields)
            writer.writeheader()
            writer.writerows(filas)
    return leer_csv(path), cambios


def agregar_global_csv(reporte: dict[str, dict], ts: str) -> list[dict]:
    """Upsert del tco.csv global (refresca con el cierre). Devuelve el CSV completo."""
    nuevos = [
        {"fecha": f, "tco": g["tco"], "vol_usd": g.get("vol_usd"), "tx": g.get("tx")}
        for f, g in sorted(reporte.items())
    ]
    filas, cambios = upsert_csv(TCO_CSV, CSV_FIELDS, ["fecha"], nuevos, ts)
    print(f"[OK] tco.csv — {cambios} fecha(s) nueva(s)/actualizada(s)" if cambios
          else "[INFO] tco.csv sin cambios; se regenera el JSON.")
    return filas


def agregar_bancos_csv(reporte: dict[str, dict], ts: str) -> list[dict]:
    """
    Upsert del tco_bancos.csv (detalle por banco). Panel completo (una fila por
    banco presente, con nulos donde no operó); refresca con el cierre por (fecha,
    banco). Devuelve el CSV completo.
    """
    nuevos = [
        {"fecha": f, "banco": banco, "tco": b.get("tco"),
         "tx": b.get("tx"), "monto_usd": b.get("monto")}
        for f, g in sorted(reporte.items())
        for banco, b in sorted(g["bancos"].items())
    ]
    filas, cambios = upsert_csv(TCO_BANCOS, BANCOS_FIELDS, ["fecha", "banco"], nuevos, ts)
    print(f"[OK] tco_bancos.csv — {cambios} fila(s) banco-día nueva(s)/actualizada(s)")
    return filas


# ── Distribución por banco (boxplot ponderado por nº de transacciones) ───────────

def _wquantile(pairs: list[tuple[float, int]], q: float) -> float:
    """Percentil ponderado (nearest-rank) sobre [(precio, peso)] ordenado asc."""
    total = sum(w for _, w in pairs)
    if total <= 0:
        return pairs[0][0]
    umbral = q * total
    acum = 0
    for precio, w in pairs:
        acum += w
        if acum >= umbral:
            return precio
    return pairs[-1][0]


def boxplot_stats(niveles: list[tuple[float, int]]) -> dict:
    """
    Estadísticas de caja y bigotes ponderadas por nº de transacciones a partir de
    los niveles (precio, n_tx) de un banco. Bigotes a 1,5·IQR; outliers = niveles
    de precio fuera de ese rango.
    """
    pairs = sorted(niveles, key=lambda x: x[0])
    q1, q2, q3 = _wquantile(pairs, .25), _wquantile(pairs, .50), _wquantile(pairs, .75)
    iqr = q3 - q1
    lo_lim, hi_lim = q1 - 1.5 * iqr, q3 + 1.5 * iqr
    dentro = [p for p, _ in pairs if lo_lim <= p <= hi_lim]
    lo = min(dentro) if dentro else q1
    hi = max(dentro) if dentro else q3
    outliers = sorted({p for p, _ in pairs if p < lo_lim or p > hi_lim})
    pesos = sum(w for _, w in pairs)
    mean = sum(p * w for p, w in pairs) / pesos if pesos else q2  # ponderada por nº tx
    r = lambda x: round(x, 4)
    return {"q1": r(q1), "q2": r(q2), "q3": r(q3), "lo": r(lo), "hi": r(hi),
            "mean": r(mean), "outliers": [r(o) for o in outliers]}


def construir_dist_hoy(reporte: dict[str, dict]) -> dict | None:
    """
    Distribución de la sesión más reciente para el boxplot: por banco activo, su
    caja/bigotes/outliers + TCO y nº de transacciones. Orden ascendente por TCO.
    """
    if not reporte:
        return None
    fecha = max(reporte.keys())
    g = reporte[fecha]
    bancos_box = []
    for banco, niveles in g.get("dist", {}).items():
        niveles = [(p, n) for (p, n) in niveles if p is not None and n]
        if not niveles:
            continue
        b = g["bancos"].get(banco, {})
        bancos_box.append({"banco": banco, "tco": b.get("tco"), "tx": b.get("tx"),
                           **boxplot_stats(niveles)})
    # Orden ascendente por la media mostrada (triángulo / número azul).
    bancos_box.sort(key=lambda x: x["mean"])
    return {"fecha": fecha, "vig": siguiente_dia_habil(fecha),
            "tco_oficial": g.get("tco"), "bancos": bancos_box}


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

def exportar_json(global_rows: list[dict], bancos_rows: list[dict],
                  dist_hoy: dict | None = None) -> None:
    usdt = usdt_por_fecha()

    # Si esta corrida no trajo reporte (WAF), conservar el último boxplot conocido.
    if dist_hoy is None and TCO_JSON.exists():
        try:
            dist_hoy = json.loads(TCO_JSON.read_text(encoding="utf-8")).get("dist_hoy")
        except Exception:
            dist_hoy = None

    serie = []
    for r in sorted(global_rows, key=lambda x: x["fecha"]):
        try:
            tco = float(r["tco"])
        except (TypeError, ValueError):
            continue
        serie.append({
            "f": r["fecha"],
            "vig": siguiente_dia_habil(r["fecha"]),   # día hábil en que rige este TCO
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
        "dist_hoy": dist_hoy,
    }
    TCO_JSON.write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    nb = len(dist_hoy["bancos"]) if dist_hoy else 0
    print(f"[OK] tco.json — {len(serie)} registro(s), {len(bancos_hoy)} banco(s) hoy, "
          f"boxplot {nb} banco(s)")


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
        dist_hoy = construir_dist_hoy(reporte)
    else:
        global_rows = leer_csv(TCO_CSV)
        bancos_rows = leer_csv(TCO_BANCOS)
        dist_hoy = None

    exportar_json(global_rows, bancos_rows, dist_hoy)


if __name__ == "__main__":
    main()
