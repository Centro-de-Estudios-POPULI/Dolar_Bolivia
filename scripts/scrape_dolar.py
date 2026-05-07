"""
Scraper diario del dólar en Bolivia.

Fuentes:
  BCB venta:      valor_referencial_venta_svg.php       (SVG — precio + historial)
  BCB compra:     valor_referencial_compra_svg_v2.php   (HTML — precio + volumen por banco)
  USDT:           mauforonda/dolares (buy.csv / sell.csv, promedio VWAP diario)

Salidas:
  data/dolar.csv        — serie diaria (fuente de verdad)
  data/historico.json   — dashboard: evolución de precios y volúmenes
  data/bancos.json      — dashboard: stacked area por banco + tabla de hoy
"""

import csv
import json
import re
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

import requests
from bs4 import BeautifulSoup

# ── Endpoints ─────────────────────────────────────────────────────────────────
BCB_VENTA_SVG  = "https://www.bcb.gob.bo/valor_referencial_venta_svg.php"
BCB_V2_HTML    = "https://www.bcb.gob.bo/valor_referencial_compra_svg_v2.php"
MAU_BUY_CSV    = "https://raw.githubusercontent.com/mauforonda/dolares/master/buy.csv"
MAU_SELL_CSV   = "https://raw.githubusercontent.com/mauforonda/dolares/master/sell.csv"

DATA_DIR   = Path(__file__).parent.parent / "data"
CSV_FILE   = DATA_DIR / "dolar.csv"
HIST_JSON  = DATA_DIR / "historico.json"
BANCOS_JSON = DATA_DIR / "bancos.json"
CSV_FIELDS = ["fecha", "bcb_venta", "bcb_compra", "usdt_venta", "usdt_compra", "timestamp_utc"]

HEADERS_BCB = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    )
}

MONTHS_ES = {
    "ene": 1, "feb": 2, "mar": 3, "abr": 4, "may": 5, "jun": 6,
    "jul": 7, "ago": 8, "sep": 9, "oct": 10, "nov": 11, "dic": 12,
}

# Nombre corto para leyendas y tooltips
BANCO_ALIAS = {
    "BANCO BISA": "BISA",
    "BANCO DE CRÉDITO": "BCR",
    "BANCO DE CREDITO": "BCR",
    "BANCO DE LA NACIÓN ARGENTINA": "BNA",
    "BANCO DE LA NACION ARGENTINA": "BNA",
    "BANCO ECONÓMICO": "ECONÓMICO",
    "BANCO ECONOMICO": "ECONÓMICO",
    "BANCO FIE": "FIE",
    "BANCO FORTALEZA": "FORTALEZA",
    "BANCO GANADERO": "GANADERO",
    "BANCO MERCANTIL SANTA CRUZ": "MERCANTIL",
    "BANCO NACIONAL DE BOLIVIA": "BNB",
    "BANCO PRODEM": "PRODEM",
    "BANCO PYME DE LA COMUNIDAD": "PYME COM.",
    "BANCO PYME ECOFUTURO": "ECOFUTURO",
    "BANCO SOLIDARIO": "SOLIDARIO",
    "BANCO UNIÓN": "UNIÓN",
    "BANCO UNION": "UNIÓN",
}

# ── Utilidades numéricas y de fecha ───────────────────────────────────────────

def parse_num(texto: str) -> float | None:
    """
    Parsea números en formato BCB Bolivia:
    - Con coma: decimal ('8,84' → 8.84; '10.789,50' → 10789.50)
    - Sin coma, punto único con 3 dígitos tras él: miles ('1.310' → 1310)
    - Sin coma, punto único con 1-2 dígitos tras él: decimal ('8.84' → 8.84)
    - Múltiples puntos sin coma: miles ('10.789.184' → 10789184)
    """
    texto = (texto or "").replace("\xa0", "").strip()
    if not texto or texto in ("—", "-", ""):
        return None
    if "," in texto:
        # Formato europeo: punto=miles, coma=decimal
        cleaned = texto.replace(".", "").replace(",", ".")
    else:
        partes = texto.split(".")
        if len(partes) > 2:
            # Múltiples puntos → separadores de miles
            cleaned = texto.replace(".", "")
        elif len(partes) == 2 and len(partes[1]) == 3:
            # Un punto con exactamente 3 dígitos → separador de miles (p.ej. "1.310")
            cleaned = texto.replace(".", "")
        else:
            # Un punto con 1-2 dígitos → decimal (p.ej. "8.84")
            cleaned = texto
    try:
        return float(cleaned)
    except ValueError:
        return None


def parse_precio(texto: str) -> float | None:
    """Extrae primer número en rango válido BOB/USD (5–25)."""
    for m in re.finditer(r"\b(\d{1,2}[,\.]\d{2,4})\b", texto):
        val = float(m.group(1).replace(",", "."))
        if 5.0 < val < 25.0:
            return val
    return None


def parse_fecha_es(texto: str) -> str | None:
    """Convierte 'D de Mes de YYYY' → 'YYYY-MM-DD'."""
    pat = re.compile(
        r"\b(\d{1,2})\s+de\s+(" + "|".join(MONTHS_ES.keys()) + r")\s+de\s+(\d{4})\b",
        re.IGNORECASE,
    )
    m = pat.search(texto)
    if m:
        dia, mes_str, anio = m.groups()
        return f"{anio}-{MONTHS_ES[mes_str.lower()]:02d}-{int(dia):02d}"
    return None


def resolver_fechas_header(celdas, start_month: int, start_year: int) -> list[str | None]:
    """
    Convierte encabezados '1-dic', '5-ene'... en fechas ISO, manejando cambio de año.
    """
    fechas = []
    mes_prev = start_month
    anio = start_year
    for celda in celdas:
        texto = celda.get_text(strip=True)
        m = re.match(r"^(\d{1,2})-(\w{3})$", texto)
        if not m:
            fechas.append(None)
            continue
        dia = int(m.group(1))
        mes_abr = m.group(2).lower()
        mes = MONTHS_ES.get(mes_abr)
        if mes is None:
            fechas.append(None)
            continue
        if mes < mes_prev:
            anio += 1
        mes_prev = mes
        fechas.append(f"{anio}-{mes:02d}-{dia:02d}")
    return fechas


def alias_banco(nombre: str) -> str:
    nombre_up = nombre.upper().strip()
    return BANCO_ALIAS.get(nombre_up, nombre_up.replace("BANCO ", "")[:14])


# ── Parsers de fuentes BCB ─────────────────────────────────────────────────────

def fetch_venta_hoy() -> tuple[float | None, str | None]:
    """Obtiene precio de venta del día desde el SVG v1."""
    r = requests.get(BCB_VENTA_SVG, headers=HEADERS_BCB, timeout=20)
    r.raise_for_status()
    xml_text = re.sub(r'\s+xmlns(?::[^=]+)?="[^"]+"', "", r.text)
    root = ET.fromstring(xml_text)
    textos = [e.text.strip() for e in root.iter("text") if e.text and e.text.strip()]
    venta, fecha = None, None
    for t in textos:
        p = parse_precio(t)
        if p is not None:
            venta = p
        f = parse_fecha_es(t)
        if f is not None:
            fecha = f
    return venta, fecha


def parse_tabla_pivot(tabla, start_month: int, start_year: int, etiqueta_total: str):
    """
    Parsea una tabla pivot HTML del BCB (bancos × fechas).
    Devuelve (por_banco, totales):
      por_banco: {fecha: {alias_banco: valor}}
      totales:   {fecha: valor}   ← fila 'BANCOS (...)'
    """
    filas = tabla.find_all("tr")
    if not filas:
        return {}, {}
    header_celdas = filas[0].find_all(["td", "th"])[1:]  # Skip "Entidad Bancaria"
    fechas = resolver_fechas_header(header_celdas, start_month, start_year)

    por_banco: dict[str, dict[str, float]] = {}
    totales: dict[str, float] = {}

    for fila in filas[1:]:
        celdas = fila.find_all(["td", "th"])
        if not celdas:
            continue
        nombre_raw = celdas[0].get_text(strip=True).upper()
        es_total = etiqueta_total in nombre_raw

        for i, celda in enumerate(celdas[1:]):
            if i >= len(fechas) or fechas[i] is None:
                continue
            fecha = fechas[i]
            val = parse_num(celda.get_text(strip=True))
            if val is None:
                continue
            if es_total:
                totales[fecha] = val
            else:
                alias = alias_banco(nombre_raw)
                por_banco.setdefault(fecha, {})[alias] = val

    return por_banco, totales


def fetch_parse_v2() -> dict:
    """Descarga y parsea el HTML v2 del BCB (compra, montos, transacciones)."""
    r = requests.get(BCB_V2_HTML, headers=HEADERS_BCB, timeout=30)
    r.raise_for_status()
    html = r.text
    soup = BeautifulSoup(html, "lxml")
    tablas = soup.find_all("table")

    # Detectar meses/años de inicio desde fechas rango en HTML ("DD/MM/YYYY")
    rango_matches = re.findall(r"\b(\d{2})/(\d{2})/(\d{4})\b", html)
    starts = [(int(mm), int(yyyy)) for dd, mm, yyyy in rango_matches[::2]] if rango_matches else []
    defaults = [(12, 2025), (1, 2026), (4, 2026)]
    starts = (starts + defaults)[:3]

    compra_banco, compra_total = {}, {}
    vol_banco, vol_total = {}, {}
    tx_banco, tx_total = {}, {}

    if len(tablas) > 0:
        compra_banco, compra_total = parse_tabla_pivot(
            tablas[0], starts[0][0], starts[0][1], "PROMEDIO PONDERADO"
        )
    if len(tablas) > 1:
        vol_banco, vol_total = parse_tabla_pivot(
            tablas[1], starts[1][0], starts[1][1], "MONTOS TOTALES"
        )
    if len(tablas) > 2:
        tx_banco, tx_total = parse_tabla_pivot(
            tablas[2], starts[2][0], starts[2][1], "NÚMERO DE TRANSACCIONES"
        )

    return {
        "compra_total": compra_total,
        "vol_total": vol_total,
        "tx_total": tx_total,
        "compra_banco": compra_banco,
        "vol_banco": vol_banco,
        "tx_banco": tx_banco,
    }


# ── USDT desde mauforonda/dolares ────────────────────────────────────────────

def _mau_daily_vwap(url: str, fecha: str) -> float | None:
    """Descarga el CSV de mauforonda y promedia el VWAP del día indicado."""
    r = requests.get(url, headers=HEADERS_BCB, timeout=40)
    r.raise_for_status()
    vwaps = []
    for line in r.text.splitlines()[1:]:
        if not line.startswith(fecha):
            continue
        parts = line.split(",")
        if len(parts) >= 5 and parts[4]:
            try:
                vwaps.append(float(parts[4]))
            except ValueError:
                pass
    return round(sum(vwaps) / len(vwaps), 4) if vwaps else None


def fetch_usdt(fecha: str) -> dict:
    """
    usdt_venta  = promedio VWAP diario de buy.csv  (usuario compra USDT, paga BOB)
    usdt_compra = promedio VWAP diario de sell.csv (usuario vende USDT, recibe BOB)
    Fuente: mauforonda/dolares (actualizado ~c/30 min).
    Timestamps en BOT (-04:00) — coinciden directamente con la fecha BCB.
    """
    try:
        return {
            "venta":  _mau_daily_vwap(MAU_BUY_CSV,  fecha),
            "compra": _mau_daily_vwap(MAU_SELL_CSV, fecha),
        }
    except Exception as e:
        print(f"[WARN] mauforonda USDT: {e}", file=sys.stderr)
        return {"venta": None, "compra": None}


# ── CSV ───────────────────────────────────────────────────────────────────────

def leer_csv() -> list[dict]:
    if not CSV_FILE.exists():
        return []
    with open(CSV_FILE, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def guardar_fila_csv(fila: dict) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    escribir_header = not CSV_FILE.exists() or CSV_FILE.stat().st_size == 0
    with open(CSV_FILE, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        if escribir_header:
            writer.writeheader()
        writer.writerow(fila)


# ── Exportar JSON ─────────────────────────────────────────────────────────────

def exportar_historico_json(csv_rows: list[dict], v2: dict) -> None:
    """Genera data/historico.json combinando CSV + historial de compra/volumen del v2."""
    csv_by_fecha = {r["fecha"]: r for r in csv_rows}

    # Unir todas las fechas conocidas
    all_fechas = sorted(
        set(csv_by_fecha.keys()) | set(v2["compra_total"].keys())
    )

    serie = []
    for f in all_fechas:
        row = csv_by_fecha.get(f, {})
        # Compra: preferir CSV (dato final del día), fallback al v2
        compra_raw = row.get("bcb_compra") or v2["compra_total"].get(f)
        compra = float(compra_raw) if compra_raw not in (None, "") else v2["compra_total"].get(f)

        venta_raw = row.get("bcb_venta")
        venta = float(venta_raw) if venta_raw not in (None, "") else None

        vol = v2["vol_total"].get(f)
        tx  = v2["tx_total"].get(f)
        usdt = row.get("usdt_venta")

        serie.append({
            "f": f,
            "v": venta,
            "c": float(compra) if compra else None,
            "vol": int(vol) if vol else None,
            "tx":  int(tx)  if tx  else None,
            "usdt": float(usdt) if usdt not in (None, "") else None,
        })

    hoy = serie[-1] if serie else {}
    out = {
        "actualizado": datetime.now(timezone.utc).isoformat(),
        "hoy": hoy,
        "serie": serie,
    }
    HIST_JSON.write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"[OK] historico.json — {len(serie)} registros")


def exportar_bancos_json(v2: dict) -> None:
    """Genera data/bancos.json con series de volumen por banco y tabla de hoy."""
    vol_banco = v2["vol_banco"]
    compra_banco = v2["compra_banco"]
    tx_banco = v2["tx_banco"]

    if not vol_banco:
        print("[WARN] Sin datos de volumen por banco.", file=sys.stderr)
        return

    fechas_vol = sorted(vol_banco.keys())

    # Ranking por volumen total acumulado
    ranking: dict[str, float] = {}
    for bancos in vol_banco.values():
        for banco, vol in bancos.items():
            ranking[banco] = ranking.get(banco, 0) + vol

    TOP_N = 7
    orden = sorted(ranking, key=lambda b: -ranking[b])
    top = orden[:TOP_N]
    otros = orden[TOP_N:]

    # Series para stacked area
    series: dict[str, list] = {}
    for banco in top:
        series[banco] = [vol_banco.get(f, {}).get(banco, 0) for f in fechas_vol]
    if otros:
        series["OTROS"] = [
            sum(vol_banco.get(f, {}).get(b, 0) for b in otros)
            for f in fechas_vol
        ]

    # Serie de compra promedio ponderado alineada a las fechas de volumen
    compra_series = [v2["compra_total"].get(f) for f in fechas_vol]

    # Tabla de hoy (último día con datos de volumen)
    fecha_hoy = fechas_vol[-1]
    todos_bancos = sorted(
        {b for d in vol_banco.values() for b in d} |
        {b for d in compra_banco.values() for b in d}
    )
    tabla_hoy = []
    for banco in todos_bancos:
        compra = compra_banco.get(fecha_hoy, {}).get(banco)
        vol    = vol_banco.get(fecha_hoy, {}).get(banco)
        tx     = tx_banco.get(fecha_hoy, {}).get(banco)
        if vol or compra:
            tabla_hoy.append({
                "banco":  banco,
                "compra": compra,
                "vol":    int(vol) if vol else None,
                "tx":     int(tx)  if tx  else None,
            })
    tabla_hoy.sort(key=lambda x: -(x.get("vol") or 0))

    bancos_leyenda = top + (["OTROS"] if otros else [])
    out = {
        "bancos": bancos_leyenda,
        "fechas": fechas_vol,
        "series": series,
        "compra_serie": compra_series,
        "tabla_hoy": tabla_hoy,
        "fecha_hoy": fecha_hoy,
    }
    BANCOS_JSON.write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"[OK] bancos.json — {len(fechas_vol)} fechas, {len(bancos_leyenda)} bancos")


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    print("Obteniendo datos...")

    # 1. BCB — determinar fecha antes de pedir USDT
    venta, fecha = fetch_venta_hoy()
    v2 = fetch_parse_v2()

    fecha_v2 = max(v2["compra_total"].keys()) if v2["compra_total"] else None
    compra = v2["compra_total"].get(fecha_v2) if fecha_v2 else None
    fecha = fecha or fecha_v2 or datetime.now(timezone.utc).strftime("%Y-%m-%d")

    # 2. USDT (mauforonda/dolares, VWAP diario para la fecha BCB)
    usdt = fetch_usdt(fecha)

    # 3. Verificar si ya existe en CSV
    csv_rows = leer_csv()
    fechas_existentes = {r["fecha"] for r in csv_rows}

    if fecha not in fechas_existentes:
        nueva_fila = {
            "fecha":       fecha,
            "bcb_venta":   venta,
            "bcb_compra":  compra,
            "usdt_venta":  usdt.get("venta"),
            "usdt_compra": usdt.get("compra"),
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        }
        guardar_fila_csv(nueva_fila)
        csv_rows.append(nueva_fila)
        print(json.dumps(nueva_fila, indent=2, ensure_ascii=False))
    else:
        print(f"[INFO] {fecha} ya en CSV — actualizando JSON.")

    # 4. Exportar JSON
    exportar_historico_json(csv_rows, v2)
    exportar_bancos_json(v2)


if __name__ == "__main__":
    main()
