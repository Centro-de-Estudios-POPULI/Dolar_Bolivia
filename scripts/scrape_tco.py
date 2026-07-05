"""
Scraper diario del Tipo de Cambio Oficial (TCO) del dólar en Bolivia.

Contexto: tras el cambio de política monetaria (junio 2026), el BCB dejó de
publicar el "valor referencial" y pasó a publicar el TCO en una plataforma
propia. El TCO es el promedio ponderado de las operaciones de COMPRA de divisas
de la banca. Desde jul-2026 el reporte "serie de tiempo" del BCB acepta rango
`?desde=&hasta=` y devuelve TODA la serie desde el cambio de régimen: en cada
corrida bajamos el histórico completo (upsert idempotente) y cualquier día que
se haya perdido se AUTO-RECUPERA. Aun así persistimos nuestra propia copia
acumulada (los CSV) por si el BCB algún día recorta la ventana.

El CSV del BCB trae el DETALLE POR BANCO (TCO, nº de transacciones y monto por
entidad) y la distribución por nivel de precio. Persistimos TODO:

  data/tco.csv            — serie diaria GLOBAL del TCO (fuente de verdad)
  data/tco_bancos.csv     — detalle POR BANCO por día (TCO, tx, monto)   [tidy]
  data/tco_raw/<fecha>.csv — copia VERBATIM del reporte del BCB (red de seguridad
                             sin pérdida: preserva incluso la distribución por
                             nivel de precio que aún no exponemos en tidy)
  data/tco.json           — dashboard: serie global + venta (+0,10) + USDT +
                             detalle por banco del día

Disposición BCB: el precio de VENTA oficial = TCO + 0,10 Bs (margen de 10 ctvs).

Fuente:
  https://www.bcb.gob.bo/tco_tcreferencial_descargar_csv.php?desde=<ini>&hasta=<fin>
  (CSV oficial de la "serie de tiempo"; separador ';', decimal coma, BOM; cabecera
   con 'Fecha de corte' + 'Fecha de vigencia' y bloques N°/Monto por banco.)
"""

import csv
import json
import re
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import requests
from bs4 import BeautifulSoup

# Reusar helpers del scraper principal: HTTP resiliente al WAF, parser numérico
# en formato BCB y el normalizador de nombres de banco (mismos alias).
from scrape_dolar import request_bcb, parse_num, alias_banco

# ── Config ─────────────────────────────────────────────────────────────────────
TCO_CSV_URL = "https://www.bcb.gob.bo/tco_tcreferencial_descargar_csv.php"
# Primera sesión del TCO tras el cambio de régimen (fecha de corte). Pedimos desde
# aquí para reconstruir/mantener toda la serie en cada corrida.
REGIME_START = "2026-06-26"
BOT = timezone(timedelta(hours=-4))  # hora Bolivia (para acotar 'hasta')
MARGEN_VENTA = 0.10  # disposición BCB: venta oficial = TCO + 10 ctvs


def tco_url() -> str:
    """URL del CSV de la serie completa: desde el cambio de régimen hasta hoy+1
    (el endpoint clampa 'hasta' a la última sesión publicada)."""
    hasta = (datetime.now(BOT).date() + timedelta(days=1)).isoformat()
    return f"{TCO_CSV_URL}?desde={REGIME_START}&hasta={hasta}"

# Vigencia (CÓMO asignamos qué TCO rige cada día): el TCO que fija la sesión de un
# día (fecha de corte) se vuelve la referencia operativa el DÍA CALENDARIO SIGUIENTE
# y rige hasta que se publique el próximo — incluidos SÁBADO y DOMINGO. Así la sesión
# del VIERNES rige todo el fin de semana (sáb + dom) y hasta el lunes, tal como lo
# muestra el propio BCB (p.ej. corte viernes 3-jul → "Vigencia: sábado 4-jul"). Por
# eso vig = corte + 1 día CALENDARIO (NO el próximo día hábil). El relleno de los días
# sin sesión (findes/feriados) con el último TCO vigente lo hace el front (carry-fwd).
def dia_siguiente(fecha_iso: str) -> str:
    """Día calendario siguiente a `fecha_iso` (desde cuándo rige ese TCO)."""
    return (date.fromisoformat(fecha_iso) + timedelta(days=1)).isoformat()

DATA_DIR   = Path(__file__).parent.parent / "data"
TCO_CSV    = DATA_DIR / "tco.csv"
TCO_BANCOS = DATA_DIR / "tco_bancos.csv"
TCO_RAW    = DATA_DIR / "tco_raw"
TCO_JSON   = DATA_DIR / "tco.json"
DOLAR_CSV  = DATA_DIR / "dolar.csv"          # para cruzar el USDT por fecha

CSV_FIELDS    = ["fecha", "tco", "vol_usd", "tx", "timestamp_utc"]
BANCOS_FIELDS = ["fecha", "banco", "tco", "tx", "monto_usd", "timestamp_utc"]


# ── Descarga + parseo del CSV oficial ──────────────────────────────────────────

def _es_cabecera(cols: list[str]) -> list[tuple[int, str]]:
    """Si `cols` es la fila de cabecera de bancos, devuelve [(idx, nombre)] de cada
    entidad + 'TOTAL BANCOS'; si no, []. Se reconoce por tener ≥2 celdas 'BANCO …'
    o 'TOTAL …'."""
    ents = [(i, c) for i, c in enumerate(cols)
            if c and (c.upper().startswith("BANCO") or c.upper().startswith("TOTAL"))]
    return ents if len(ents) >= 2 else []


def _find_header(lineas: list[str]) -> tuple[list[tuple[int, str]], int | None]:
    """
    Localiza la cabecera de bancos y devuelve (orden, tc_col):
      orden  = [(indice_columna, nombre)] de cada entidad + 'TOTAL BANCOS'.
      tc_col = índice de la columna de etiqueta/precio ('TC (En Bs/USD)') — la que
               en las filas de dato trae 'TCO', 'TOTAL' o el precio negociado.
    Se ancla en las columnas 'BANCO …' (NO en posiciones fijas), así que sobrevive
    a que el BCB agregue columnas al inicio — como en jul-2026, cuando insertó
    'Fecha de vigencia' y los bancos pasaron del índice 2 al 3. Cada entidad ocupa
    2 sub-columnas (N°, Monto).
    """
    for linea in lineas:
        cols = [c.strip().strip('"') for c in linea.split(";")]
        ents = _es_cabecera(cols)
        if ents:
            return ents, ents[0][0] - 1
    return [], None


def _es_total(nombre: str) -> bool:
    return nombre.upper().startswith("TOTAL")


def parse_reporte(texto: str) -> dict[str, dict]:
    """
    Parsea el CSV del BCB en una estructura por fecha:
      { fecha: {
          'tco': float, 'vol_usd': int|None, 'tx': int|None,
          'bancos': { alias: {'tco': float|None, 'tx': int|None, 'monto': int|None} },
          'dist':   { alias: [ (precio, n_tx, monto), ... ] },  # distribución por precio
      }}
    Usa el orden de bancos de la cabecera (robusto si el BCB reordena columnas).
    Las filas de distribución por nivel de precio (cada TC negociado, con nº de
    transacciones por banco) se recogen en 'dist' para el boxplot. Solo se
    devuelven fechas con TCO.
    """
    lineas = texto.splitlines()
    orden, tc_col = _find_header(lineas)
    if not orden or tc_col is None:
        raise ValueError("No se encontró la cabecera de bancos en el CSV del BCB")

    fechas: dict[str, dict] = {}
    for linea in lineas:
        cols = [c.strip().strip('"') for c in linea.split(";")]
        if len(cols) <= tc_col:
            continue
        fecha = cols[0]
        if len(fecha) != 10 or fecha[4] != "-" or not fecha[:4].isdigit():
            continue
        g = fechas.setdefault(fecha, {"bancos": {}, "dist": {}})
        etiqueta = cols[tc_col].upper()

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
            # Fila de distribución: la columna de etiqueta trae un TC negociado; por
            # banco viene el nº de transacciones y el monto a ese precio.
            precio = parse_num(cols[tc_col])
            if precio is None:
                continue
            for idx, nombre in orden:
                if _es_total(nombre):
                    continue
                ntx = parse_num(cols[idx]) if idx < len(cols) else None
                if not ntx:
                    continue
                mto = parse_num(cols[idx + 1]) if idx + 1 < len(cols) else None
                g["dist"].setdefault(alias_banco(nombre), []).append(
                    (precio, int(ntx), int(mto) if mto else 0))

    return {f: g for f, g in fechas.items() if g.get("tco") is not None}


def fetch_csv() -> bytes:
    """Descarga el CSV de la serie completa del BCB (bytes crudos)."""
    return request_bcb(tco_url(), timeout=30).content


# ── Página interactiva (fuente fresca; el CSV se atrasa los fines de semana) ─────

DETALLE_URL = "https://www.bcb.gob.bo/tco_reporte_detalle_historico.php"


def _decode_bcb(raw: bytes) -> str:
    """El CSV del BCB viene en utf-8(-sig); la página interactiva en cp1252. Elegimos
    la decodificación que NO deja caracteres de reemplazo (acentos de los bancos)."""
    u = raw.decode("utf-8-sig", errors="replace")
    return u if "�" not in u else raw.decode("cp1252", errors="replace")


def fetch_detalle_page(hdr_line: str):
    """
    Raspa la PÁGINA interactiva del TCO para capturar la sesión más reciente que el
    CSV descargable aún no publica (el CSV se atrasa los findes; la página no).
    Devuelve (reporte_parcial, pseudo_csv_bytes) — ({}, b'') si algo falla.

    La tabla de la página NO trae columnas de fecha (van en el encabezado) y su fila
    'TCO' trae 1 celda por banco (las de distribución/TOTAL traen 2: N°/Monto). Por
    eso reconstruimos líneas tipo-CSV `corte;vig;<TC>;<N°;Monto por banco…>` usando
    `hdr_line` = la cabecera REAL del CSV (mismos nombres/orden de banco → mismos
    alias) y reusamos parse_reporte.
    """
    try:
        raw = request_bcb(DETALLE_URL, timeout=30).content
        html = _decode_bcb(raw)
        fechas = re.findall(r"\d{4}-\d{2}-\d{2}", html)
        if not fechas:
            return {}, b""
        corte = max(fechas)
        vig = dia_siguiente(corte)
        tabla = BeautifulSoup(html, "html.parser").find("table")
        if tabla is None:
            return {}, b""
        lineas = [hdr_line]
        for r in tabla.find_all("tr")[2:]:            # [0],[1] = cabeceras de la tabla
            celdas = [c.get_text(strip=True) for c in r.find_all(["td", "th"])]
            if not celdas:
                continue
            if celdas[0].upper() == "TCO":            # 1 celda/banco → re-insertar subcol vacía
                exp = [celdas[0]]
                for v in celdas[1:]:
                    exp += [v, ""]
                celdas = exp
            lineas.append(";".join([corte, vig] + celdas))
        pseudo = ("\n".join(lineas) + "\n")
        return parse_reporte(pseudo), pseudo.encode("utf-8")
    except Exception as e:  # la página es un EXTRA; si falla, seguimos con el CSV
        print(f"[WARN] no se pudo leer la página del TCO (se usa solo el CSV): {e}",
              file=sys.stderr)
        return {}, b""


# ── Archivo raw (red de seguridad sin pérdida) ──────────────────────────────────

def guardar_raw_serie(crudo: bytes) -> None:
    """
    Archiva el reporte verbatim, UNA copia por fecha de corte. El CSV de la serie
    trae todas las sesiones juntas; lo partimos en `tco_raw/<fecha>.csv` (bloque de
    cabecera + filas de esa sesión) para tener una copia DURABLE de cada día aunque
    el BCB algún día recorte la ventana. Idempotente (no reescribe si no cambió).
    """
    lineas = crudo.decode("utf-8-sig", errors="replace").splitlines()
    cab_idx = next((i for i, l in enumerate(lineas)
                    if _es_cabecera([c.strip().strip('"') for c in l.split(";")])), None)
    if cab_idx is None:
        return
    cabecera = lineas[:cab_idx + 1]
    por_fecha: dict[str, list[str]] = {}
    for l in lineas[cab_idx + 1:]:
        f = (l.split(";", 1)[0]).strip().strip('"')
        if len(f) == 10 and f[4] == "-" and f[:4].isdigit():
            por_fecha.setdefault(f, []).append(l)
    TCO_RAW.mkdir(parents=True, exist_ok=True)
    for fecha, filas in por_fecha.items():
        destino = TCO_RAW / f"{fecha}.csv"
        contenido = ("\n".join(cabecera + filas) + "\n").encode("utf-8")
        if destino.exists() and destino.read_bytes() == contenido:
            continue
        destino.write_bytes(contenido)
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


def boxplot_stats(pairs: list[tuple[float, int]]) -> dict:
    """
    Estadísticas de caja y bigotes ponderadas por MONTO (USD) — igual que el BCB
    calcula el TCO — a partir de pares (precio, monto). Así la media coincide con
    el TCO oficial del banco. Bigotes a 1,5·IQR; outliers = precios fuera del rango.
    """
    pairs = sorted(pairs, key=lambda x: x[0])
    q1, q2, q3 = _wquantile(pairs, .25), _wquantile(pairs, .50), _wquantile(pairs, .75)
    iqr = q3 - q1
    lo_lim, hi_lim = q1 - 1.5 * iqr, q3 + 1.5 * iqr
    dentro = [p for p, _ in pairs if lo_lim <= p <= hi_lim]
    lo = min(dentro) if dentro else q1
    hi = max(dentro) if dentro else q3
    outliers = sorted({p for p, _ in pairs if p < lo_lim or p > hi_lim})
    pesos = sum(w for _, w in pairs)
    mean = sum(p * w for p, w in pairs) / pesos if pesos else q2  # ponderada por monto
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
        # Ponderar por MONTO (USD), igual que el BCB: así la media (triángulo)
        # coincide con el TCO oficial del banco y el TCO global queda coherente.
        pares = [(p, m) for (p, n, m) in niveles if p is not None and m]
        if not pares:
            continue
        b = g["bancos"].get(banco, {})
        bancos_box.append({"banco": banco, "tco": b.get("tco"), "tx": b.get("tx"),
                           **boxplot_stats(pares)})
    # Orden ascendente por la media mostrada (triángulo / número azul).
    bancos_box.sort(key=lambda x: x["mean"])
    return {"fecha": fecha, "vig": dia_siguiente(fecha),
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
            "vig": dia_siguiente(r["fecha"]),   # día calendario desde el que rige este TCO
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

def _regenerar_desde_disco() -> None:
    """Regenera el JSON con los CSV ya guardados (sin datos frescos)."""
    exportar_json(leer_csv(TCO_CSV), leer_csv(TCO_BANCOS), None)


def main() -> None:
    print("Obteniendo TCO (serie completa)...")
    # Dos modos de fallo, tratados MUY distinto:
    #  • WAF/red (request_bcb agota reintentos): transitorio y benigno. Conservamos
    #    lo guardado, regeneramos el JSON y salimos en VERDE (no tumbamos el run).
    #  • HTTP 200 pero el CSV no parsea o no trae sesiones (el BCB cambió el
    #    formato): NO es benigno. Regeneramos el JSON con lo que hay y FALLAMOS el
    #    run (rojo → notificación) para enterarnos y arreglar el parser. Antes esto
    #    degradaba en silencio y el TCO quedó congelado ~2 semanas sin que nadie lo
    #    notara (jul-2026: el BCB insertó 'Fecha de vigencia' y rompió el parser).
    reporte: dict[str, dict] = {}
    crudo = b""
    try:
        crudo = fetch_csv()
    except requests.RequestException as e:
        print(f"[WARN] TCO no disponible (WAF/red; se conserva último dato): {e}",
              file=sys.stderr)
    else:
        try:
            reporte = parse_reporte(crudo.decode("utf-8-sig"))
        except Exception as e:
            print(f"::error::TCO: el CSV del BCB respondió 200 pero no parsea "
                  f"(¿cambió el formato otra vez?): {e}")
            _regenerar_desde_disco()
            sys.exit(1)
        if not reporte:
            print("::error::TCO: el CSV parseó pero no trajo ninguna sesión con TCO "
                  "(posible cambio en las etiquetas de fila del BCB).")
            _regenerar_desde_disco()
            sys.exit(1)

        # La página interactiva va más FRESCA que el CSV descargable (que se atrasa
        # los fines de semana): tomamos de ahí la(s) sesión(es) que el CSV aún no
        # trae (p.ej. la del viernes por la noche y durante todo el finde).
        hdr_line = next((l for l in crudo.decode("utf-8-sig").splitlines()
                         if _es_cabecera([c.strip().strip('"') for c in l.split(";")])), None)
        if hdr_line:
            rep_page, pseudo_page = fetch_detalle_page(hdr_line)
            nuevas = sorted(f for f in rep_page if f not in reporte)
            for f in nuevas:
                reporte[f] = rep_page[f]     # el CSV manda; la página solo AGREGA lo que falta
            if nuevas:
                print(f"[OK] página: +{len(nuevas)} sesión(es) no presentes en el CSV: {nuevas}")
                guardar_raw_serie(pseudo_page)

    if reporte:
        ts = datetime.now(timezone.utc).isoformat()
        guardar_raw_serie(crudo)
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
