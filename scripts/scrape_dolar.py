"""
Extrae el valor referencial del dólar BCB (venta y compra) y el precio USDT/Binance.
Guarda/actualiza datos en data/dolar.csv.

Fuentes:
  BCB venta:  https://www.bcb.gob.bo/valor_referencial_venta_svg.php  (SVG/XML)
  BCB compra: https://www.bcb.gob.bo/valor_referencial_compra_svg.php (SVG/XML)
  USDT:       https://bo.dolarapi.com/v1/dolares/binance              (JSON)
"""

import csv
import json
import re
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

import requests

# ── Endpoints ─────────────────────────────────────────────────────────────────
BCB_VENTA_SVG  = "https://www.bcb.gob.bo/valor_referencial_venta_svg.php"
BCB_COMPRA_SVG = "https://www.bcb.gob.bo/valor_referencial_compra_svg.php"
BINANCE_API    = "https://bo.dolarapi.com/v1/dolares/binance"

DATA_FILE  = Path(__file__).parent.parent / "data" / "dolar.csv"
FIELDNAMES = ["fecha", "bcb_venta", "bcb_compra", "usdt_venta", "usdt_compra", "timestamp_utc"]

MONTHS_ES = {
    "enero": "01", "febrero": "02", "marzo": "03", "abril": "04",
    "mayo": "05", "junio": "06", "julio": "07", "agosto": "08",
    "septiembre": "09", "octubre": "10", "noviembre": "11", "diciembre": "12",
}

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    )
}

# Namespace SVG
SVG_NS = {"svg": "http://www.w3.org/2000/svg"}


# ── Helpers ───────────────────────────────────────────────────────────────────

def fetch_svg(url: str) -> ET.Element:
    """Descarga y parsea un SVG del BCB."""
    r = requests.get(url, headers=HEADERS, timeout=20)
    r.raise_for_status()
    # Eliminar namespace para simplificar XPath
    xml_text = re.sub(r'\s+xmlns(?::[^=]+)?="[^"]+"', "", r.text)
    return ET.fromstring(xml_text)


def svg_texts(root: ET.Element) -> list[str]:
    """Extrae el texto de todos los elementos <text> del SVG."""
    texts = []
    for elem in root.iter("text"):
        t = (elem.text or "").strip()
        if t:
            texts.append(t)
    return texts


def parse_precio_bo(texto: str, min_v: float = 5.0, max_v: float = 25.0) -> float | None:
    """Extrae el primer número flotante en rango válido para BOB/USD."""
    for m in re.finditer(r'\b(\d{1,2}[,\.]\d{2,4})\b', texto):
        val = float(m.group(1).replace(',', '.'))
        if min_v < val < max_v:
            return val
    return None


def parse_fecha_es(texto: str) -> str | None:
    """Convierte 'DD de Mes de YYYY' o 'YYYY-MM-DD' a 'YYYY-MM-DD'."""
    pat = re.compile(
        r'\b(\d{1,2})\s+de\s+(' + '|'.join(MONTHS_ES.keys()) + r')\s+de\s+(\d{4})\b',
        re.IGNORECASE,
    )
    m = pat.search(texto)
    if m:
        dia, mes_str, anio = m.groups()
        return f"{anio}-{MONTHS_ES[mes_str.lower()]}-{dia.zfill(2)}"
    m2 = re.search(r'\b(\d{4}-\d{2}-\d{2})\b', texto)
    return m2.group(1) if m2 else None


# ── Scrapers BCB ──────────────────────────────────────────────────────────────

def get_bcb_venta() -> tuple[float | None, str | None]:
    """
    Obtiene la venta más reciente del SVG del BCB.
    El SVG contiene una tabla histórica; buscamos el último par (fecha, precio).
    """
    root = fetch_svg(BCB_VENTA_SVG)
    texts = svg_texts(root)

    # Buscar "Última cotización" o el último número válido + fecha
    last_precio: float | None = None
    last_fecha: str | None = None

    for t in texts:
        precio = parse_precio_bo(t)
        if precio is not None:
            last_precio = precio
        fecha = parse_fecha_es(t)
        if fecha is not None:
            last_fecha = fecha

    return last_precio, last_fecha


def get_bcb_compra() -> float | None:
    """
    Obtiene el promedio ponderado de compra del día desde el SVG de compra.
    """
    root = fetch_svg(BCB_COMPRA_SVG)
    texts = svg_texts(root)

    # Buscar el texto "Promedio Ponderado" y tomar el precio que le sigue
    for i, t in enumerate(texts):
        if "promedio" in t.lower() and "ponderado" in t.lower():
            # El precio suele estar en el mismo elemento o en el siguiente
            precio = parse_precio_bo(t)
            if precio is not None:
                return precio
            # Buscar en los próximos 3 elementos
            for j in range(i + 1, min(i + 4, len(texts))):
                precio = parse_precio_bo(texts[j])
                if precio is not None:
                    return precio

    # Fallback: primer precio válido encontrado
    for t in texts:
        precio = parse_precio_bo(t)
        if precio is not None:
            return precio
    return None


# ── USDT ──────────────────────────────────────────────────────────────────────

def get_usdt() -> dict:
    """Obtiene compra/venta de USDT desde DolarApi (fuente: Binance P2P Bolivia)."""
    try:
        r = requests.get(BINANCE_API, timeout=15)
        r.raise_for_status()
        d = r.json()
        return {"venta": d.get("venta"), "compra": d.get("compra")}
    except Exception as e:
        print(f"[WARN] USDT: {e}", file=sys.stderr)
        return {"venta": None, "compra": None}


# ── CSV ───────────────────────────────────────────────────────────────────────

def fecha_ya_existe(fecha: str) -> bool:
    if not DATA_FILE.exists():
        return False
    with open(DATA_FILE, newline="", encoding="utf-8") as f:
        return any(row.get("fecha") == fecha for row in csv.DictReader(f))


def guardar_fila(fila: dict) -> None:
    DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
    escribir_header = not DATA_FILE.exists() or DATA_FILE.stat().st_size == 0
    with open(DATA_FILE, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        if escribir_header:
            writer.writeheader()
        writer.writerow(fila)


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    venta, fecha = get_bcb_venta()
    compra       = get_bcb_compra()
    usdt         = get_usdt()

    fecha = fecha or datetime.now(timezone.utc).strftime("%Y-%m-%d")

    if fecha_ya_existe(fecha):
        print(f"[INFO] Fecha {fecha} ya registrada — sin cambios.")
        return

    fila = {
        "fecha":       fecha,
        "bcb_venta":   venta,
        "bcb_compra":  compra,
        "usdt_venta":  usdt.get("venta"),
        "usdt_compra": usdt.get("compra"),
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
    }

    guardar_fila(fila)
    print(json.dumps(fila, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
