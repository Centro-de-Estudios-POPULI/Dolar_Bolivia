"""
Backfill histórico (one-shot).
Extrae TODA la historia disponible del BCB y de mauforonda/dolares,
y genera los archivos de datos.

Uso: python scripts/backfill_historico.py
"""

import csv
import json
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import requests
from bs4 import BeautifulSoup

# Reutilizar funciones del scraper principal
sys.path.insert(0, str(Path(__file__).parent))
from scrape_dolar import (
    BCB_VENTA_SVG, BCB_V2_HTML,
    MAU_BUY_CSV, MAU_SELL_CSV,
    HEADERS_BCB, CSV_FILE, CSV_FIELDS, DATA_DIR,
    parse_precio, parse_fecha_es, parse_num,
    fetch_parse_v2,
    exportar_historico_json, exportar_bancos_json,
    MONTHS_ES,
)


def fetch_venta_historico() -> list[tuple[str, float]]:
    """Extrae todos los pares (fecha, precio_venta) del SVG v1."""
    r = requests.get(BCB_VENTA_SVG, headers=HEADERS_BCB, timeout=20)
    r.raise_for_status()
    xml_text = re.sub(r'\s+xmlns(?::[^=]+)?="[^"]+"', "", r.text)
    root = ET.fromstring(xml_text)
    textos = [e.text.strip() for e in root.iter("text") if e.text and e.text.strip()]

    pares = []
    fecha_actual = None
    for t in textos:
        f = parse_fecha_es(t)
        if f:
            fecha_actual = f
            continue
        if fecha_actual:
            p = parse_precio(t)
            if p is not None:
                pares.append((fecha_actual, p))
                fecha_actual = None
    return pares


def fetch_usdt_historico() -> tuple[dict[str, float], dict[str, float]]:
    """
    Descarga buy.csv y sell.csv de mauforonda/dolares y calcula el VWAP
    promedio diario para cada fecha.
    Timestamps usan -04:00 (BOT) — la fecha en el timestamp coincide con
    la fecha publicada por el BCB.

    Retorna:
      usdt_venta_por_fecha  — buy.csv  (usuario compra USDT, paga BOB)
      usdt_compra_por_fecha — sell.csv (usuario vende USDT, recibe BOB)
    """
    def agg_vwap(url: str) -> dict[str, float]:
        print(f"  Descargando {url.split('/')[-1]}…")
        r = requests.get(url, headers=HEADERS_BCB, timeout=60)
        r.raise_for_status()
        per_date: dict[str, list[float]] = {}
        for line in r.text.splitlines()[1:]:
            if not line:
                continue
            parts = line.split(",")
            if len(parts) < 4 or not parts[3]:
                continue
            fecha = parts[0][:10]  # "2026-05-06"
            try:
                per_date.setdefault(fecha, []).append(float(parts[3]))
            except ValueError:
                pass
        return {f: round(sum(v) / len(v), 4) for f, v in per_date.items()}

    usdt_venta  = agg_vwap(MAU_BUY_CSV)
    usdt_compra = agg_vwap(MAU_SELL_CSV)
    return usdt_venta, usdt_compra


def escribir_csv_historico(
    venta_hist: list[tuple],
    compra_total: dict,
    usdt_venta: dict[str, float],
    usdt_compra: dict[str, float],
) -> list[dict]:
    """
    Escribe el CSV histórico completo combinando BCB + USDT mauforonda.
    Los datos USDT de mauforonda tienen prioridad sobre valores vacíos;
    no sobreescriben un valor ya existente en el CSV.
    """
    filas_existentes: dict[str, dict] = {}
    if CSV_FILE.exists():
        with open(CSV_FILE, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                filas_existentes[row["fecha"]] = row

    todas_fechas = sorted(
        {f for f, _ in venta_hist} | set(compra_total.keys())
    )

    filas_finales = []
    for fecha in todas_fechas:
        existente = filas_existentes.get(fecha, {})
        venta  = next((p for f, p in venta_hist if f == fecha), None)
        compra = compra_total.get(fecha)

        # USDT: usar mauforonda si la celda existente está vacía
        usdt_v = (
            existente.get("usdt_venta") or
            (str(usdt_venta[fecha])  if fecha in usdt_venta  else "")
        )
        usdt_c = (
            existente.get("usdt_compra") or
            (str(usdt_compra[fecha]) if fecha in usdt_compra else "")
        )

        fila = {
            "fecha":         fecha,
            "bcb_venta":     venta  if venta  is not None else existente.get("bcb_venta", ""),
            "bcb_compra":    compra if compra is not None else existente.get("bcb_compra", ""),
            "usdt_venta":    usdt_v,
            "usdt_compra":   usdt_c,
            "timestamp_utc": existente.get("timestamp_utc", ""),
        }
        filas_finales.append(fila)

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(CSV_FILE, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        writer.writeheader()
        writer.writerows(filas_finales)

    return filas_finales


def main() -> None:
    print("Descargando historial de venta (SVG v1)...")
    venta_hist = fetch_venta_historico()
    print(f"  {len(venta_hist)} registros de venta encontrados.")

    print("Descargando historial de compra + volúmenes (HTML v2)...")
    v2 = fetch_parse_v2()
    print(f"  {len(v2['compra_total'])} registros de compra (promedio ponderado).")
    print(f"  {len(v2['vol_total'])} registros de volumen.")

    print("Descargando historial USDT (mauforonda/dolares)...")
    usdt_venta, usdt_compra = fetch_usdt_historico()
    print(f"  {len(usdt_venta)} días con dato USDT venta.")
    print(f"  {len(usdt_compra)} días con dato USDT compra.")

    print("Escribiendo CSV histórico...")
    csv_rows = escribir_csv_historico(venta_hist, v2["compra_total"], usdt_venta, usdt_compra)
    print(f"  {len(csv_rows)} filas en total.")

    print("Exportando JSON...")
    exportar_historico_json(csv_rows, v2)
    exportar_bancos_json(v2)

    print("\nMuestra de datos (primeros y últimos 3):")
    for r in csv_rows[:3] + csv_rows[-3:]:
        print(
            f"  {r['fecha']} | venta={r['bcb_venta']} | "
            f"compra={r['bcb_compra']} | usdt_v={r['usdt_venta']}"
        )


if __name__ == "__main__":
    main()
