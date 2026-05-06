"""
Backfill histórico (one-shot): extrae TODA la tabla del SVG de venta del BCB
(desde 1-dic-2025 hasta hoy) y guarda cada fila en data/dolar.csv.

La compra histórica no está disponible en el SVG compra (solo muestra el día actual),
por lo que usdt_* y bcb_compra quedan vacíos para fechas pasadas.

Uso: python scripts/backfill_historico.py
"""

import csv
import re
import xml.etree.ElementTree as ET
from pathlib import Path

import requests

BCB_VENTA_SVG = "https://www.bcb.gob.bo/valor_referencial_venta_svg.php"
DATA_FILE = Path(__file__).parent.parent / "data" / "dolar.csv"
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


def parse_fecha_es(texto: str) -> str | None:
    pat = re.compile(
        r'\b(\d{1,2})\s+de\s+(' + '|'.join(MONTHS_ES.keys()) + r')\s+de\s+(\d{4})\b',
        re.IGNORECASE,
    )
    m = pat.search(texto)
    if m:
        dia, mes_str, anio = m.groups()
        return f"{anio}-{MONTHS_ES[mes_str.lower()]}-{dia.zfill(2)}"
    return None


def parse_precio(texto: str) -> float | None:
    for m in re.finditer(r'\b(\d{1,2}[,\.]\d{2,4})\b', texto):
        val = float(m.group(1).replace(',', '.'))
        if 5.0 < val < 25.0:
            return val
    return None


def fetch_tabla_historica() -> list[dict]:
    """
    Descarga el SVG de venta y extrae todos los pares (fecha, precio).
    El SVG contiene una tabla con dos columnas: fecha y valor Bs/$us.
    """
    r = requests.get(BCB_VENTA_SVG, headers=HEADERS, timeout=30)
    r.raise_for_status()

    # Remover namespaces para simplificar iter
    xml_text = re.sub(r'\s+xmlns(?::[^=]+)?="[^"]+"', "", r.text)
    root = ET.fromstring(xml_text)

    # Extraer todos los textos del SVG
    texts = []
    for elem in root.iter("text"):
        t = (elem.text or "").strip()
        if t:
            texts.append(t)

    # Reconstruir pares (fecha, precio) recorriendo los textos secuencialmente.
    # La tabla tiene alternancia: fecha → precio → fecha → precio ...
    registros = []
    fecha_actual = None
    for t in texts:
        fecha = parse_fecha_es(t)
        if fecha:
            fecha_actual = fecha
            continue
        if fecha_actual:
            precio = parse_precio(t)
            if precio is not None:
                registros.append({"fecha": fecha_actual, "bcb_venta": precio})
                fecha_actual = None  # Resetear para el siguiente par

    return registros


def main() -> None:
    print("Descargando historial desde BCB...")
    registros = fetch_tabla_historica()
    print(f"Filas encontradas en SVG: {len(registros)}")

    # Leer fechas ya existentes
    fechas_existentes: set[str] = set()
    if DATA_FILE.exists():
        with open(DATA_FILE, newline="", encoding="utf-8") as f:
            fechas_existentes = {r["fecha"] for r in csv.DictReader(f)}

    nuevos = [r for r in registros if r["fecha"] not in fechas_existentes]
    if not nuevos:
        print("No hay datos nuevos que agregar.")
        return

    # Ordenar por fecha ascendente
    nuevos.sort(key=lambda x: x["fecha"])

    DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
    escribir_header = not DATA_FILE.exists() or DATA_FILE.stat().st_size == 0

    with open(DATA_FILE, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        if escribir_header:
            writer.writeheader()
        for r in nuevos:
            writer.writerow({
                "fecha":       r["fecha"],
                "bcb_venta":   r["bcb_venta"],
                "bcb_compra":  "",
                "usdt_venta":  "",
                "usdt_compra": "",
                "timestamp_utc": "",
            })

    print(f"Guardadas {len(nuevos)} filas históricas.")
    print("\nPrimeras 5 filas:")
    for r in nuevos[:5]:
        print(f"  {r['fecha']} | venta={r['bcb_venta']}")
    print("Últimas 5 filas:")
    for r in nuevos[-5:]:
        print(f"  {r['fecha']} | venta={r['bcb_venta']}")


if __name__ == "__main__":
    main()
