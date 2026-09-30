"""
Scraper diario del Tipo de Cambio Oficial (TCO) del dólar en Bolivia.

Contexto: tras el cambio de política monetaria (junio 2026), el BCB dejó de
publicar el "valor referencial" y pasó a publicar el TCO en una plataforma
propia. El TCO resume las operaciones de COMPRA de divisas de la banca: fue la
MEDIA ponderada por monto hasta la sesión del 24-sep-2026 y es la MEDIANA
ponderada por monto desde la del 25-sep (ver `METODO_MEDIANA_DESDE`).
Desde jul-2026 el reporte "serie de tiempo" del BCB acepta rango
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
  data/tco_dist/<fecha>.json — caja y bigotes por banco de CADA sesión (el
                             dashboard deja elegir qué día ver en el boxplot)
  data/tco.json           — dashboard: serie global + venta (+0,10, mientras
                             existió) + USDT + detalle por banco del día

Hasta sep-2026 el precio de VENTA oficial era TCO + 0,10 Bs por disposición del
BCB; desde la R.D. 142/2026 cada banco fija el suyo (ver `MARGEN_VENTA_HASTA` y
`scrape_bancos_tc.py`).

Fuente:
  https://www.bcb.gob.bo/bcb_tco_publico_descargar_csv.php?desde=<ini>&hasta=<fin>
  (antes `tco_tcreferencial_descargar_csv.php`, congelado por el BCB el 2026-08-20)
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
# ⚠️⚠️ EL BCB MUDÓ EL REPORTE (2026-08-27). Los endpoints `tco_*` dejaron de
#   avanzar: desde el 21-ago responden 200, con datos, pero su ventana se quedó
#   clavada en el 2026-08-20 —el CSV devuelve «Rango de fecha de corte;…;
#   2026-08-20», la página declara `max="2026-08-20"` y el Excel tira 500 «No
#   existen datos de detalle»—. La serie siguió publicándose todo ese tiempo en
#   `bcb_tco_publico_*`, la ruta nueva, que el 27-ago traía 41 sesiones contra
#   las 37 de la vieja.
#
#   ⇒ NO FUE UNA CAÍDA: fue una MUDANZA, y por eso no la vio nadie. Un 404 se
#     nota; una URL que sigue contestando 200 con el último dato bueno se lee
#     como «hoy no hubo sesión». Seis días hábiles perdidos en verde.
#
#   Se prueban las dos rutas EN ORDEN y se usa la que traiga la serie más larga
#   (`elegir_fuente`), así que el día que el BCB vuelva a mudar —o revierta— el
#   scraper se acomoda solo en vez de congelarse otros seis días. Y para que un
#   silencio así no vuelva a pasar callado, `main()` revisa además la ANTIGÜEDAD
#   de la última sesión y pone la corrida en rojo si se estanca (ver `alerta_estancada`).
TCO_FUENTES = [
    # (nombre, URL del CSV de la serie, URL de la página de detalle)
    ("bcb_tco_publico",
     "https://www.bcb.gob.bo/bcb_tco_publico_descargar_csv.php",
     "https://www.bcb.gob.bo/bcb_tco_publico_detalle_historico.php"),
    ("tco_tcreferencial",
     "https://www.bcb.gob.bo/tco_tcreferencial_descargar_csv.php",
     "https://www.bcb.gob.bo/tco_reporte_detalle_historico.php"),
]
TCO_CSV_URL = TCO_FUENTES[0][1]      # se reasigna en elegir_fuente()
# Primera sesión del TCO tras el cambio de régimen (fecha de corte). Pedimos desde
# aquí para reconstruir/mantener toda la serie en cada corrida.
REGIME_START = "2026-06-26"
BOT = timezone(timedelta(hours=-4))  # hora Bolivia (para acotar 'hasta')
MARGEN_VENTA = 0.10  # disposición BCB: venta oficial = TCO + 10 ctvs
# ★ HASTA CUÁNDO EXISTIÓ ESA «VENTA OFICIAL». La R.D. 88/2026, Art. 6, definía
#   el «valor referencial de venta» = TCO + 0,10 como TOPE: «Las Entidades
#   Financieras no podrán vender USD por encima del valor referencial de venta».
#   La R.D. 142/2026 (fechada el 24-sep, publicada el 25-sep) derogó la R.D. 88
#   entera y no fija ningún tope; la Circular ASFI/983/2026 (29-sep) abrogó el
#   control de la venta y obliga a cada banco a publicar su compra y su venta
#   (las captura `scrape_bancos_tc.py`). Última sesión con tope: la del 24-sep,
#   la última calculada con la R.D. 88. Después `tco_venta` va vacío: el precio
#   de venta de hoy está en data/bancos_tc.json, no se inventa.
MARGEN_VENTA_HASTA = "2026-09-24"


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
TCO_DIST   = DATA_DIR / "tco_dist"
TCO_JSON   = DATA_DIR / "tco.json"
DOLAR_CSV  = DATA_DIR / "dolar.csv"          # para cruzar el USDT por fecha

# Las columnas nuevas van al final (antes del timestamp) para no mover las que
# ya leía cualquiera que baje el CSV. vig_desde/vig_hasta = el tramo de vigencia
# que declara el BCB; metodo = qué estadístico es el TCO de esa sesión;
# media_pond/mediana_pond = los dos, recalculados de la distribución.
CSV_FIELDS    = ["fecha", "tco", "vol_usd", "tx", "vig_desde", "vig_hasta",
                 "metodo", "media_pond", "mediana_pond", "timestamp_utc"]
BANCOS_FIELDS = ["fecha", "banco", "tco", "tx", "monto_usd", "timestamp_utc"]


# ── Descarga + parseo del CSV oficial ──────────────────────────────────────────

def _celdas(linea: str) -> list[str]:
    # ⚠️ Desde el 2026-09-26 el BCB escribe cada número como fórmula de texto de
    #    Excel —en el archivo, comilla-igual-comilla-comilla-1.765-comilla x3—,
    #    que un lector CSV entrega como `="1.765"` (así Excel no lo convierte en
    #    fecha ni le come los ceros). El viejo `c.strip().strip('"')` dejaba
    #    `=""1.765`, que ya no es un número, y el volumen y las transacciones de
    #    TODA la serie quedaron vacíos en una sola corrida, en verde. Se usa el
    #    lector CSV de verdad (respeta las comillas) y se quita el envoltorio
    #    `="…"`; con el formato viejo da lo mismo que antes.
    """Separa una línea del CSV del BCB en celdas limpias."""
    fila = next(csv.reader([linea], delimiter=";"), [])
    out = []
    for c in fila:
        c = c.strip()
        if c.startswith("="):
            c = c[1:].strip()
        out.append(c.strip('"').strip())
    return out


def _parse_vigencia(texto: str) -> tuple[str | None, str | None]:
    """'2026-09-29' → (29, 29); '2026-09-26 al 2026-09-28' → (26, 28).

    El BCB declara el tramo de vigencia de cada sesión: un día suelto entre
    semana, o un rango cuando la sesión cubre un fin de semana o un feriado
    (el viernes 25-sep rige del sábado 26 al lunes 28). Es lo que el BCB
    DECLARA, así que manda sobre cualquier regla que calculemos nosotros."""
    fechas = re.findall(r"\d{4}-\d{2}-\d{2}", texto or "")
    if not fechas:
        return None, None
    return fechas[0], fechas[-1]


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
        ents = _es_cabecera(_celdas(linea))
        if ents:
            return ents, ents[0][0] - 1
    return [], None


def _col_vigencia(lineas: list[str]) -> int | None:
    """Índice de la columna 'Vigencia' en la cabecera (None si el reporte no la trae)."""
    for linea in lineas:
        cols = _celdas(linea)
        if _es_cabecera(cols):
            return next((i for i, c in enumerate(cols) if "VIGENCIA" in c.upper()), None)
    return None


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
    vig_col = _col_vigencia(lineas)

    fechas: dict[str, dict] = {}
    for linea in lineas:
        cols = _celdas(linea)
        if len(cols) <= tc_col:
            continue
        fecha = cols[0]
        if len(fecha) != 10 or fecha[4] != "-" or not fecha[:4].isdigit():
            continue
        g = fechas.setdefault(fecha, {"bancos": {}, "dist": {}})
        if vig_col is not None and "vig" not in g and vig_col < len(cols):
            desde, hasta = _parse_vigencia(cols[vig_col])
            if desde:
                g["vig"], g["vig_hasta"] = desde, hasta
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

    # ── Reconstruccion de los totales cuando el BCB no los publica ──────────
    #
    # El reporte trae DOS vistas del mismo dia: la fila TOTAL (nº de tx y monto
    # por banco) y las filas de DISTRIBUCION (esas mismas operaciones abiertas
    # por nivel de precio). La segunda contiene a la primera, asi que los
    # totales siempre se pueden reconstruir sumando la distribucion.
    #
    # Hace falta porque el BCB deja huecos, y cada vez mas grandes:
    #   · 2026-07-20 y 07-21: publico el detalle por banco pero dejo vacia la
    #     columna TOTAL BANCOS -> faltaba el total del dia.
    #   · 2026-07-31: dejo la fila TOTAL **entera** vacia (30/30 celdas) con la
    #     distribucion completa -> quedaban en cero las tx y el monto de los 14
    #     bancos, el volumen del dia, la tabla del reporte y la serie de volumen
    #     por entidad. El dato estaba publicado; solo no estaba sumado.
    #
    # Verificado contra los 24 dias que si traen fila TOTAL: el nº de
    # transacciones reconstruido coincide EXACTO en los 24, y el monto difiere
    # entre 0 y 4 USD sobre decenas de millones (0,000%) por el redondeo con que
    # el BCB publica cada nivel de precio. Si mas tarde el BCB publica la fila
    # TOTAL, el upsert la reemplaza por la oficial: la reconstruccion nunca
    # pisa un dato bueno.
    for f, g in fechas.items():
        bancos, dist = g.get("bancos", {}), g.get("dist", {})
        recon = []
        for banco, niveles in dist.items():
            b = bancos.setdefault(banco, {})
            if b.get("tx") is None and niveles:
                b["tx"] = sum(n for _, n, _ in niveles)
                recon.append(banco)
            if b.get("monto") is None and niveles:
                b["monto"] = sum(m for _, _, m in niveles)
        if recon:
            g["reconstruido"] = True
            print(f"[INFO] {f}: el BCB no publico la fila TOTAL; se reconstruyeron "
                  f"tx/monto de {len(recon)} banco(s) sumando la distribucion por "
                  f"nivel de precio.", file=sys.stderr)
        # Total del dia: sumar los bancos (ya completos tras lo anterior).
        if g.get("vol_usd") is None:
            montos = [b["monto"] for b in bancos.values() if b.get("monto")]
            if montos:
                g["vol_usd"] = sum(montos)
        if g.get("tx") is None:
            txs = [b["tx"] for b in bancos.values() if b.get("tx")]
            if txs:
                g["tx"] = sum(txs)

        # Los dos estadísticos, recalculados de la distribución: la MEDIA y la
        # MEDIANA ponderadas por monto, del día y de cada banco. Uno de los dos es
        # el TCO oficial según la fecha (ver METODO_MEDIANA_DESDE); el otro dice
        # cuánto habría dado el otro método ese mismo día.
        todos = [(p, m) for niveles in dist.values() for p, _, m in niveles if m]
        g["media"], g["mediana"] = media_pond(todos), mediana_pond(todos)
        for banco, niveles in dist.items():
            pares = [(p, m) for p, _, m in niveles if m]
            b = bancos.setdefault(banco, {})
            b["media"], b["mediana"] = media_pond(pares), mediana_pond(pares)

    return {f: g for f, g in fechas.items() if g.get("tco") is not None}


# ── Método de cálculo del TCO ───────────────────────────────────────────────────
#
# ★ DESDE LA SESIÓN DEL VIERNES 25-SEP-2026 EL TCO ES LA MEDIANA PONDERADA POR
#   MONTO, no la media: R.D. 142/2026, Art. 5.I (publicada el 25-sep), que además
#   fija la publicación «a horas 23:30» (Art. 5.IV) y la ventana de operaciones
#   de 17:01 del día hábil anterior a 17:00. La nota metodológica de la página
#   del BCB lo dice así:
#   «TCO: Corresponde a la mediana ponderada por monto de los tipos de cambio de
#   las operaciones de compra de dólares realizadas por los Bancos Múltiples,
#   Bancos PyME, Banco Público con sus clientes y el Banco Central de Bolivia».
#
#   La FECHA no la dice la nota; sale de los datos. Recalculando cada sesión de
#   la serie completa (26-jun → 28-sep) desde su distribución por nivel de
#   precio: hasta el 24-sep el TCO publicado es EXACTAMENTE la media ponderada
#   (64 de 64 sesiones, y cada banco su propia media); desde el 25-sep es
#   EXACTAMENTE la mediana (el total y 13/13 y 14/14 bancos), y la media ya no
#   coincide. Ej.: 28-sep → mediana 12,02 (= oficial) · media 11,97.
#
#   Se DECLARA la fecha (no se infiere en cada corrida) y cada corrida la
#   VERIFICA: si el TCO publicado deja de coincidir con el estadístico declarado,
#   el método cambió otra vez y hay que enterarse (ver `verificar_metodo`).
METODO_MEDIANA_DESDE = "2026-09-25"


def metodo_de(fecha: str) -> str:
    return "mediana" if fecha >= METODO_MEDIANA_DESDE else "media"


def media_pond(pares: list[tuple[float, float]]) -> float | None:
    """Media de los precios ponderada por monto."""
    peso = sum(m for _, m in pares)
    return round(sum(p * m for p, m in pares) / peso, 4) if peso else None


def mediana_pond(pares: list[tuple[float, float]]) -> float | None:
    """Mediana ponderada por monto: el primer precio (de menor a mayor) en el que
    el monto acumulado alcanza la mitad del total. Es la variante que reproduce
    el TCO publicado por el BCB en todas las sesiones desde el 25-sep."""
    pares = sorted(pares)
    peso = sum(m for _, m in pares)
    if not peso:
        return None
    acum = 0
    for p, m in pares:
        acum += m
        if acum >= peso / 2:
            return round(p, 4)
    return round(pares[-1][0], 4)


def verificar_metodo(reporte: dict[str, dict]) -> list[str]:
    """Sesiones cuyo TCO publicado NO coincide con el estadístico declarado.

    Tolerancia de medio centavo: el BCB publica con dos decimales y la media se
    recalcula sobre montos ya redondeados."""
    malas = []
    for f, g in sorted(reporte.items()):
        esperado = g.get(metodo_de(f))
        if esperado is None or g.get("tco") is None:
            continue
        if abs(g["tco"] - esperado) > 0.006:
            malas.append(f"{f}: publicado {g['tco']} · {metodo_de(f)} recalculada "
                         f"{esperado} · la otra {g.get('media' if metodo_de(f) == 'mediana' else 'mediana')}")
    return malas


def fetch_csv() -> bytes:
    """Descarga el CSV de la serie completa del BCB (bytes crudos)."""
    return request_bcb(tco_url(), timeout=30).content


# ── Página interactiva (fuente fresca; el CSV se atrasa los fines de semana) ─────

DETALLE_URL = TCO_FUENTES[0][2]      # se reasigna en elegir_fuente()


def _fechas_de_corte(crudo: bytes) -> set[str]:
    """Las fechas de corte de un CSV, sin parsearlo entero: alcanza para comparar
    dos fuentes y quedarse con la que llega más lejos."""
    txt = crudo.decode("utf-8-sig", errors="replace")
    return set(re.findall(r'(?m)^"?(\d{4}-\d{2}-\d{2})"?;', txt))


def elegir_fuente() -> bytes:
    """Baja la serie de cada ruta conocida y se queda con la que llega MÁS LEJOS.

    ⚠️ NO alcanza con «la primera que responda 200»: eso es exactamente lo que
       falló el 2026-08-27. La ruta vieja seguía respondiendo 200, con una serie
       válida y bien formada, sólo que congelada en el 2026-08-20, mientras la
       nueva ya iba por el 26. Entre una fuente viva y una muerta el código de
       estado no distingue nada — la única señal es HASTA DÓNDE LLEGA.

    Si la primera fuente ya viene fresca no se molesta a la segunda: son ~190 KB
    por pedido y cada uno es una tirada más contra el WAF del BCB.
    """
    global TCO_CSV_URL, DETALLE_URL
    hoy = datetime.now(BOT).date()
    mejor = None
    for nombre, csv_url, det_url in TCO_FUENTES:
        TCO_CSV_URL = csv_url
        try:
            crudo = fetch_csv()
        except requests.RequestException as e:
            print(f"[WARN] fuente «{nombre}» no respondió: {e}", file=sys.stderr)
            continue
        fechas = _fechas_de_corte(crudo)
        if not fechas:
            print(f"[WARN] fuente «{nombre}»: 200 pero sin fechas de corte",
                  file=sys.stderr)
            continue
        ultima = max(fechas)
        print(f"[INFO] fuente «{nombre}»: {len(fechas)} sesiones, hasta {ultima}")
        if mejor is None or ultima > mejor[3]:
            mejor = (nombre, crudo, det_url, ultima)
        # fresca = como mucho el finde de atraso; no hace falta probar la otra
        if (hoy - date.fromisoformat(ultima)).days <= 3:
            break
    if mejor is None:
        # ninguna respondió: es el modo WAF/red, que `main` trata como benigno
        raise requests.RequestException(
            "ninguna ruta del BCB devolvió una serie usable")
    nombre, crudo, det_url, ultima = mejor
    TCO_CSV_URL = next(x[1] for x in TCO_FUENTES if x[0] == nombre)
    DETALLE_URL = det_url
    if nombre != TCO_FUENTES[0][0]:
        print(f"[INFO] se usa la ruta de respaldo «{nombre}» (llega hasta {ultima})")
    return crudo


def dias_habiles_entre(desde_iso: str, hasta: date) -> int:
    """Días hábiles transcurridos, sin contar el de partida. No sabe de feriados
    bolivianos a propósito: el umbral de la alerta ya deja margen para uno."""
    d, n = date.fromisoformat(desde_iso), 0
    while d < hasta:
        d += timedelta(days=1)
        if d.weekday() < 5:
            n += 1
    return n


# Cuántos días hábiles puede quedarse quieta la serie antes de gritar. El TCO se
# publica al cierre (~20:00 BOT), así que durante todo un día hábil lo normal es
# tener la sesión de AYER: 1 no es anomalía y 2 tampoco (un feriado suelto). A
# partir de 3 ya no hay explicación inocente.
MAX_DIAS_QUIETA = 3


def _decode_bcb(raw: bytes) -> str:
    """El CSV del BCB viene en utf-8(-sig); la página interactiva en cp1252. Elegimos
    la decodificación que NO deja caracteres de reemplazo (acentos de los bancos)."""
    u = raw.decode("utf-8-sig", errors="replace")
    return u if "�" not in u else raw.decode("cp1252", errors="replace")


_MESES = {m: i for i, m in enumerate(
    ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto",
     "septiembre", "octubre", "noviembre", "diciembre"], 1)}


def _vigencia_de_pagina(html: str, corte: str) -> str:
    """El tramo de vigencia que la página escribe en castellano («Vigencia:
    martes 29 de septiembre de 2026», o un rango si cubre un fin de semana),
    como 'AAAA-MM-DD al AAAA-MM-DD' para que lo lea `_parse_vigencia`. Si no se
    entiende, el día calendario siguiente al corte: el CSV trae después el
    tramo oficial y el upsert lo completa."""
    txt = re.sub(r"<[^>]+>", " ", html)
    i = txt.find("Vigencia:")
    if i >= 0:
        frag = txt[i:i + 160].lower()
        completas = re.findall(r"(\d{1,2}) de ([a-záéíóú]+) de (\d{4})", frag)
        fechas = []
        for d, mes, a in completas:
            if mes in _MESES:
                fechas.append(date(int(a), _MESES[mes], int(d)))
        if fechas:
            hasta = fechas[-1]
            desde = fechas[0]
            if len(fechas) == 1:     # «sábado 26 al lunes 28 de septiembre de 2026»
                sueltos = re.findall(r"(\d{1,2})\s+al\s", frag)
                if sueltos:
                    d = int(sueltos[0])
                    desde = hasta.replace(day=d) if d <= hasta.day else \
                        (hasta.replace(day=1) - timedelta(days=1)).replace(day=d)
            return f"{desde.isoformat()} al {hasta.isoformat()}"
    return dia_siguiente(corte)


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
        # La sesión que muestra la página es el `value` del selector de fecha;
        # la fecha más alta del HTML queda de respaldo (la página también lista
        # en `data-fechas` todas las sesiones disponibles).
        m = re.search(r'id="fecha"[^>]*value="(\d{4}-\d{2}-\d{2})"', html)
        fechas = re.findall(r"\d{4}-\d{2}-\d{2}", html)
        if not m and not fechas:
            return {}, b""
        corte = m.group(1) if m else max(fechas)
        vig = _vigencia_de_pagina(html, corte)
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
    cab_idx = next((i for i, l in enumerate(lineas) if _es_cabecera(_celdas(l))), None)
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


# Celdas que una corrida quiso VACIAR y la guarda conservó (ver `upsert_csv`).
# `main` las cuenta al final: si hay alguna, la corrida termina en rojo.
VACIADOS: list[str] = []


def upsert_csv(path: Path, fields: list[str], key_fields: list[str],
               nuevos: list[dict], ts: str) -> tuple[list[dict], int]:
    """
    Upsert idempotente sobre un CSV. `nuevos` = filas con valores tipados (sin
    timestamp). Inserta claves nuevas y REEMPLAZA filas cuyos valores cambian
    (comparando todo menos timestamp_utc) — así la sesión que se publica tarde
    refresca una captura parcial. No reescribe si nada cambió (evita commits de
    ruido). Devuelve (filas_resultantes, n_cambios).

    ★ UN DATO QUE YA TENÍAMOS NO SE BORRA PORQUE HOY NO SE PUDO LEER.
      El 2026-09-26 el BCB empezó a escribir los números como `="1.765"`; el
      parser no los entendió, devolvió vacío, y este upsert —que reemplazaba la
      fila entera— dejó sin volumen ni transacciones las 64 sesiones de la serie
      y los 896 banco-día, en una corrida que terminó en verde. Ahora un valor
      vacío nunca pisa uno lleno: se conserva el guardado y se anota en
      `VACIADOS`, y la corrida termina en rojo para que alguien mire el formato.
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
        if prev is not None:
            for f in val_fields:
                if row[f] == "" and (prev.get(f) or "") != "":
                    row[f] = prev[f]
                    VACIADOS.append(f"{path.name} {'/'.join(key)} {f}")
        if prev is None or any((prev.get(f) or "") != row[f] for f in val_fields):
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
        {"fecha": f, "tco": g["tco"], "vol_usd": g.get("vol_usd"), "tx": g.get("tx"),
         "vig_desde": g.get("vig"), "vig_hasta": g.get("vig_hasta"),
         "metodo": metodo_de(f), "media_pond": g.get("media"),
         "mediana_pond": g.get("mediana")}
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
    calcula el TCO — a partir de pares (precio, monto). La media coincide con el
    TCO oficial del banco hasta el 24-sep y la mediana (q2, misma regla que
    `mediana_pond`) desde el 25-sep. Bigotes a 1,5·IQR; outliers = precios fuera
    del rango.
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


def construir_dist(reporte: dict[str, dict], fecha: str) -> dict | None:
    """
    Distribución de UNA sesión para el boxplot: por banco activo, su
    caja/bigotes/outliers + TCO y nº de transacciones. Orden ascendente por TCO.
    """
    g = reporte.get(fecha)
    if not g:
        return None
    metodo = metodo_de(fecha)
    bancos_box = []
    for banco, niveles in g.get("dist", {}).items():
        # Ponderar por MONTO (USD), igual que el BCB: hasta el 24-sep la media
        # (triángulo) es el TCO oficial del banco; desde el 25-sep lo es la
        # mediana (la línea dentro de la caja). `metodo` le dice al front cuál.
        pares = [(p, m) for (p, n, m) in niveles if p is not None and m]
        if not pares:
            continue
        b = g["bancos"].get(banco, {})
        bancos_box.append({"banco": banco, "tco": b.get("tco"), "tx": b.get("tx"),
                           **boxplot_stats(pares)})
    # Orden ascendente por el TCO oficial de cada banco (número azul).
    oficial = "q2" if metodo == "mediana" else "mean"
    bancos_box.sort(key=lambda x: x["tco"] if x["tco"] is not None else x[oficial])
    return {"fecha": fecha, "vig": g.get("vig") or dia_siguiente(fecha),
            "vig_hasta": g.get("vig_hasta"), "metodo": metodo,
            "tco_oficial": g.get("tco"), "vol_usd": g.get("vol_usd"),
            "tx": g.get("tx"), "bancos": bancos_box}


def construir_dist_hoy(reporte: dict[str, dict]) -> dict | None:
    """Distribución de la sesión más reciente (la que abre el dashboard)."""
    return construir_dist(reporte, max(reporte)) if reporte else None


def fechas_dist() -> list[str]:
    """Sesiones con distribución archivada (asc) — alimenta el selector de día."""
    return sorted(p.stem for p in TCO_DIST.glob("*.json")) if TCO_DIST.exists() else []


def guardar_dist_series(reporte: dict[str, dict]) -> list[str]:
    """
    Archiva la caja y bigotes de CADA sesión en `tco_dist/<fecha>.json`, un
    archivo por día.

    Un archivo por sesión (y no un blob único con toda la serie) por dos razones:
    el dashboard sólo baja el día que el usuario elige — la carga inicial no
    crece con la historia —, y cada corrida agrega un archivo nuevo en vez de
    reescribir uno cada vez más grande. Idempotente: no toca lo ya escrito si el
    contenido no cambió. Devuelve las fechas disponibles en disco (asc).
    """
    TCO_DIST.mkdir(parents=True, exist_ok=True)
    nuevas = 0
    for fecha in sorted(reporte):
        d = construir_dist(reporte, fecha)
        if not d or not d["bancos"]:
            continue
        destino = TCO_DIST / f"{fecha}.json"
        contenido = json.dumps(d, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        if destino.exists() and destino.read_bytes() == contenido:
            continue
        destino.write_bytes(contenido)
        nuevas += 1
    fechas = fechas_dist()
    print(f"[OK] tco_dist — {nuevas} sesión(es) nueva(s)/actualizada(s), "
          f"{len(fechas)} disponible(s) para el selector del boxplot")
    return fechas


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

def _totales_por_banco(bancos_rows: list[dict]) -> dict[str, dict]:
    """Suma volumen y transacciones de todos los bancos, por fecha.

    Red de seguridad para cuando el reporte del BCB trae el detalle por banco
    pero deja vacia la columna TOTAL BANCOS (visto 2026-07-20 y 07-21): el
    volumen y las tx del dia existen, solo hay que sumarlos.
    """
    agg: dict[str, dict] = {}
    for r in bancos_rows:
        a = agg.setdefault(r["fecha"], {"vol": 0, "tx": 0, "n": 0})
        m, t = r.get("monto_usd"), r.get("tx")
        if m not in (None, ""):
            a["vol"] += int(float(m)); a["n"] += 1
        if t not in (None, ""):
            a["tx"] += int(float(t))
    return agg


def exportar_json(global_rows: list[dict], bancos_rows: list[dict],
                  dist_hoy: dict | None = None,
                  recon_fechas: tuple = (),
                  dist_fechas: list[str] | None = None) -> None:
    """`recon_fechas`: sesiones cuyos totales se reconstruyeron desde la
    distribución porque el BCB no publicó la fila TOTAL. Se marcan en la serie
    (`rec: true`) para que el dashboard lo pueda advertir en vez de mostrar el
    dato como si viniera sumado por el emisor."""
    usdt = usdt_por_fecha()
    sum_banco = _totales_por_banco(bancos_rows)

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
        vol = int(r["vol_usd"]) if r.get("vol_usd") not in (None, "") else None
        tx  = int(r["tx"])      if r.get("tx")      not in (None, "") else None
        # el total global falta pero el detalle por banco existe: se reconstruye
        sb = sum_banco.get(r["fecha"])
        if vol is None and sb and sb["n"]:
            vol = sb["vol"]
        if tx is None and sb and sb["tx"]:
            tx = sb["tx"]
        def _f(v):
            return float(v) if v not in (None, "") else None
        item = {
            "f": r["fecha"],
            # tramo en que rige: el que declara el BCB (día calendario siguiente
            # a la sesión, y hasta el lunes si cae viernes o antes de feriado)
            "vig": r.get("vig_desde") or dia_siguiente(r["fecha"]),
            "vig_hasta": r.get("vig_hasta") or None,
            "tco": tco,
            "metodo": r.get("metodo") or metodo_de(r["fecha"]),
            "media": _f(r.get("media_pond")),
            "mediana": _f(r.get("mediana_pond")),
            "tco_venta": (round(tco + MARGEN_VENTA, 2)
                          if r["fecha"] <= MARGEN_VENTA_HASTA else None),
            "usdt": usdt.get(r["fecha"]),
            "vol": vol,
            "tx":  tx,
        }
        if r["fecha"] in recon_fechas:
            item["rec"] = True
        serie.append(item)

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
        # ult ya trae vol/tx reconstruidos desde los bancos cuando el total
        # global falta, asi que el KPI de la ultima sesion nunca queda vacio
        hoy = {**ult, "vol_usd": ult.get("vol"), "tx": ult.get("tx")}

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
        # última sesión con «venta oficial = TCO + 0,10»; después, cada banco fija la suya
        "margen_venta_hasta": MARGEN_VENTA_HASTA,
        # desde qué sesión el TCO es la mediana ponderada (antes, la media)
        "metodo_mediana_desde": METODO_MEDIANA_DESDE,
        "hoy": hoy,
        "serie": serie,
        "fecha_hoy": fecha_hoy,
        "bancos_hoy": bancos_hoy,
        "bancos_fechas": fechas_b,
        "bancos_series": bancos_series,
        "dist_hoy": dist_hoy,
        # Sólo la LISTA de sesiones con boxplot disponible (unos bytes): el
        # selector del dashboard se arma con esto y baja `tco_dist/<fecha>.json`
        # únicamente cuando el usuario cambia de día.
        "dist_fechas": dist_fechas if dist_fechas is not None else fechas_dist(),
        "reconstruidas": sorted(recon_fechas),
    }
    TCO_JSON.write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    nb = len(dist_hoy["bancos"]) if dist_hoy else 0
    print(f"[OK] tco.json — {len(serie)} registro(s), {len(bancos_hoy)} banco(s) hoy, "
          f"boxplot {nb} banco(s)"
          + (f", {len(recon_fechas)} sesión(es) con totales reconstruidos" if recon_fechas else ""))


# ── Main ────────────────────────────────────────────────────────────────────────

def _regenerar_desde_disco() -> None:
    """Regenera el JSON con los CSV ya guardados (sin datos frescos)."""
    exportar_json(leer_csv(TCO_CSV), leer_csv(TCO_BANCOS), None)


def reporte_desde_raw() -> dict[str, dict]:
    """
    Reconstruye el reporte completo desde el archivo local `tco_raw/`, sin red.
    Cada raw es el bloque verbatim de una sesión (cabecera + filas), así que
    parsea con el mismo `parse_reporte` del CSV en vivo. Es el backfill de las
    sesiones anteriores a que existiera `tco_dist/`, y la vía de recuperación si
    el BCB algún día recorta la ventana del endpoint.
    """
    rep: dict[str, dict] = {}
    for p in sorted(TCO_RAW.glob("*.csv")):
        try:
            rep.update(parse_reporte(_decode_bcb(p.read_bytes())))
        except Exception as e:
            print(f"[WARN] {p.name}: no se pudo parsear ({e})", file=sys.stderr)
    return rep


def regenerar_desde_raw() -> None:
    """`--desde-raw`: rehace tco_dist/ y tco.json con el archivo local (offline)."""
    reporte = reporte_desde_raw()
    if not reporte:
        print("::error::no hay sesiones parseables en data/tco_raw/")
        sys.exit(1)
    print(f"[OK] archivo local: {len(reporte)} sesión(es) leída(s) de tco_raw/")
    dist_fechas = guardar_dist_series(reporte)
    exportar_json(leer_csv(TCO_CSV), leer_csv(TCO_BANCOS),
                  construir_dist_hoy(reporte), _fechas_reconstruidas(reporte),
                  dist_fechas)


def _fechas_reconstruidas(reporte: dict[str, dict]) -> tuple:
    return tuple(f for f, g in reporte.items() if g.get("reconstruido"))


def main() -> None:
    if "--desde-raw" in sys.argv:
        regenerar_desde_raw()
        return
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
        crudo = elegir_fuente()
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
                         if _es_cabecera(_celdas(l))), None)
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
        dist_fechas = guardar_dist_series(reporte)
        dist_hoy = construir_dist_hoy(reporte)
        recon = _fechas_reconstruidas(reporte)
    else:
        global_rows = leer_csv(TCO_CSV)
        bancos_rows = leer_csv(TCO_BANCOS)
        dist_fechas = fechas_dist()
        dist_hoy = None
        recon = ()

    exportar_json(global_rows, bancos_rows, dist_hoy, recon, dist_fechas)

    # Todo lo que sigue va DESPUÉS de exportar: el sitio queda con el mejor dato
    # disponible y recién entonces la corrida se pone en rojo si algo no cierra.
    fallas = []

    # ★ Un dato guardado que esta corrida no supo leer (ver `upsert_csv`): es la
    #   huella de un cambio de formato del BCB, como el `="…"` del 26-sep.
    if VACIADOS:
        print(f"::error::El BCB devolvió vacías {len(VACIADOS)} celda(s) que ya "
              f"teníamos con dato; se conservaron las guardadas. ¿Cambió el formato "
              f"del CSV? Primeras: {VACIADOS[:5]}")
        fallas.append("vaciados")

    # ★ El TCO publicado tiene que ser el estadístico que declaramos. Si deja de
    #   serlo, el BCB cambió el método otra vez y el dashboard lo estaría
    #   describiendo mal.
    malas = verificar_metodo(reporte)
    if malas:
        ultima_sesion = max(reporte)
        grave = any(x.startswith(ultima_sesion) for x in malas)
        print(("::error::" if grave else "::warning::")
              + f"TCO: {len(malas)} sesión(es) no coinciden con el método declarado "
              f"(mediana desde {METODO_MEDIANA_DESDE}): {malas[-3:]}")
        if grave:
            fallas.append("metodo")
    elif reporte:
        print(f"[OK] método verificado en {len(reporte)} sesiones "
              f"(media hasta {METODO_MEDIANA_DESDE}, mediana desde entonces)")

    # ★ LA SERIE TIENE QUE AVANZAR, Y SI NO AVANZA HAY QUE ENTERARSE.
    #   Las guardas de arriba cubren «no parsea» y «no trae sesiones»; ninguna
    #   cubre el modo que de verdad pasó: el BCB mudó la URL, la vieja siguió
    #   devolviendo 200 con una serie perfecta —sólo que congelada— y el
    #   scraper reportó «sin datos nuevos» en verde durante seis días hábiles.
    #   Para el runner eso es indistinguible de un feriado largo; la única
    #   diferencia está en el CALENDARIO, así que la medimos acá.
    #   Va al final y después de exportar: el JSON se publica igual —vale más un
    #   sitio con el último dato bueno que uno vacío—, pero la corrida termina en
    #   rojo y manda notificación.
    ultima = max((r["fecha"] for r in global_rows if r.get("fecha")), default=None)
    if ultima:
        quieta = dias_habiles_entre(ultima, datetime.now(BOT).date())
        if quieta > MAX_DIAS_QUIETA:
            print(f"::error::TCO estancado: la última sesión es del {ultima}, "
                  f"hace {quieta} días hábiles. El CSV responde pero no avanza — "
                  f"revisar si el BCB volvió a mudar el reporte (ver TCO_FUENTES).")
            fallas.append("estancado")
        else:
            print(f"[OK] última sesión {ultima} · {quieta} día(s) hábil(es) de atraso")
    if fallas:
        sys.exit(1)


if __name__ == "__main__":
    main()
