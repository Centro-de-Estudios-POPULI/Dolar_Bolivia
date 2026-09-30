"""
Tipo de cambio de COMPRA y VENTA que publica cada banco en su sitio web.

Contexto: hasta septiembre de 2026 regía un precio de venta único por
disposición del BCB (TCO + 0,10 Bs). Desde entonces cada entidad fija y publica
su propia compra y su propia venta —«tipos de cambio diferenciados»—, y el BCB
sigue publicando el TCO (mediana ponderada de las compras de la banca) como
referencia oficial. Lo que paga o cobra la ventanilla de cada banco lo dice el
banco, así que se lo pregunta a cada uno.

Salidas:
  data/bancos_tc.csv  — OBSERVACIONES (fuente de verdad, sólo se agrega): una
                        fila por banco cada vez que su cotización cambia, y al
                        menos una por día aunque no cambie, con la hora de captura.
  data/bancos_tc.json — dashboard: la última cotización de cada banco, el
                        resumen del día (medianas y rangos entre bancos) y la
                        serie diaria de esas medianas.

Reglas (las mismas del resto del repo):
  · Un banco que no responde, o que responde algo que no es una cotización, se
    lista SIN número. Nunca se rellena con un valor escrito a mano ni con el de
    otro banco.
  · Cada valor pasa por un control de rango antes de guardarse: una cotización
    fuera de 5–30 Bs/USD o con la compra por encima de la venta no se guarda.
  · Si NINGÚN banco responde, la corrida termina en rojo (después de exportar).
"""

import csv
import json
import re
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path

import ssl

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter

BOT = timezone(timedelta(hours=-4))
DATA_DIR = Path(__file__).parent.parent / "data"
OBS_CSV = DATA_DIR / "bancos_tc.csv"
OUT_JSON = DATA_DIR / "bancos_tc.json"
TCO_JSON = DATA_DIR / "tco.json"
# usdt_*: algunos bancos (BISA, BCP) cotizan también USDT; se guarda si lo publican.
CAMPOS = ["fecha", "hora", "banco", "compra", "venta", "oficial",
          "usdt_compra", "usdt_venta", "timestamp_utc"]

# UA de navegador en TODOS los pedidos: el WAF de Ganadero (Radware) rechaza el
# de python-requests y el de curl.
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/128.0 Safari/537.36")
TIMEOUT = 60


class _CifradosAmplios(HTTPAdapter):
    """El BCP sólo negocia un cifrado RSA sin ECDHE (AES256-GCM-SHA384), que el
    contexto SSL por defecto de Python excluye: la conexión muere con
    ConnectionResetError, también en GitHub Actions. Se reabre la lista."""

    def init_poolmanager(self, *a, **kw):
        ctx = ssl.create_default_context()
        ctx.set_ciphers("DEFAULT")
        kw["ssl_context"] = ctx
        return super().init_poolmanager(*a, **kw)


S = requests.Session()
S.headers.update({"User-Agent": UA, "Accept-Language": "es-BO,es;q=0.9"})
S.mount("https://www.bcp.com.bo", _CifradosAmplios())


def _num(v) -> float | None:
    """'12,37' / '12.37' / 12.37 → 12.37; cualquier otra cosa → None."""
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip().replace("Bs", "").replace(" ", "")
    if not s:
        return None
    if "," in s and "." in s:          # 1.234,56
        s = s.replace(".", "").replace(",", ".")
    else:
        s = s.replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return None


# ── Extractores por banco ───────────────────────────────────────────────────
# Cada uno devuelve {"compra": float, "venta": float, "oficial": float|None}.
# Si el banco cambia su sitio, el extractor lanza y el banco queda sin dato
# ese día (con el motivo), sin afectar a los demás.

BANCOS: list[dict] = []   # se completa abajo: {banco, nombre, fuente, fetch}


def banco(alias: str, nombre: str, fuente: str):
    def deco(fn):
        BANCOS.append({"banco": alias, "nombre": nombre, "fuente": fuente, "fetch": fn})
        return fn
    return deco


def _texto(html: str, sep: str = " ") -> str:
    return BeautifulSoup(html, "lxml").get_text(sep, strip=True)


def _primero(texto: str) -> float | None:
    """El primer número de un texto: «Bs. 12.12» → 12.12, «12,32 Bs.» → 12.32.
    (Quitar todo lo que no sea dígito rompía «Bs. 12.12» en «.12.12».)"""
    m = re.search(r"\d+(?:[.,]\d+)?", texto or "")
    return _num(m.group(0)) if m else None


def _re(patron: str, texto: str) -> float:
    m = re.search(patron, texto, re.I)
    if not m:
        raise ValueError(f"no aparece «{patron}»")
    return _num(m.group(1))


@banco("BISA", "Banco BISA", "https://www.bisa.com/")
def bisa():
    # La web (Angular) lee este XML; trae USD/BOB y también USDT («UST»).
    r = S.get("https://sjoven.bisa.com/assets/cotizaciones.xml", timeout=TIMEOUT)
    r.raise_for_status()
    ns = "{http://www.bisa.com/Commons/ObtenerCotizaciones/2.0}"
    cot = {}
    for c in ET.fromstring(r.content).iter(ns + "Cotizacion"):
        par = f"{c.findtext(ns + 'Moneda')}/{c.findtext(ns + 'MonedaCambio')}"
        cot[par] = (_num(c.findtext(ns + "ValorCompra")), _num(c.findtext(ns + "ValorVenta")))
    usd, ust = cot["USD/BOB"], cot.get("UST/BOB", (None, None))
    return {"compra": usd[0], "venta": usd[1], "usdt_compra": ust[0], "usdt_venta": ust[1]}


@banco("BCR", "Banco de Crédito (BCP)", "https://www.bcp.com.bo/")
def bcp():
    # Cinta de la portada: «Dólar Compra: 11.52», «USDT Venta: 12.25»…
    r = S.get("https://www.bcp.com.bo/", timeout=TIMEOUT)
    r.raise_for_status()
    d = {}
    for s in BeautifulSoup(r.text, "lxml").select(".marquee-content span"):
        k, _, v = s.get_text(" ", strip=True).partition(":")
        d[k.strip().lower()] = _primero(v)
    return {"compra": d.get("dólar compra"), "venta": d.get("dólar venta"),
            "usdt_compra": d.get("usdt compra"), "usdt_venta": d.get("usdt venta")}


@banco("BNA", "Banco de la Nación Argentina", "https://www.bna.com.bo/")
def bna():
    r = S.get("https://www.bna.com.bo/Home/CargarCotizaciones", timeout=TIMEOUT,
              headers={"Referer": "https://www.bna.com.bo/"})
    r.raise_for_status()
    usd = next(x for x in r.json() if x.get("moneda") == "USD")
    return {"compra": _num(usd["compra"]), "venta": _num(usd["venta"])}


@banco("ECONÓMICO", "Banco Económico", "https://www.baneco.com.bo/")
def economico():
    r = S.get("https://www.baneco.com.bo/GetTipoCambio", timeout=TIMEOUT)
    r.raise_for_status()
    j = r.json()
    return {"compra": _num(j["Compra"]), "venta": _num(j["Venta"]), "oficial": _num(j.get("Oficial"))}


@banco("FIE", "Banco FIE", "https://www.bancofie.com.bo/")
def fie():
    # POST con Referer (sin él da 404; con GET, 405). `documento` es texto libre.
    r = S.post("https://www.bancofie.com.bo/api/tcl", data="", timeout=TIMEOUT,
               headers={"Referer": "https://www.bancofie.com.bo/", "Origin": "https://www.bancofie.com.bo"})
    r.raise_for_status()
    doc = r.json()["resultado"]["documento"]
    return {"compra": _re(r"D[óo]lar Compra\s*:\s*([\d.,]+)", doc),
            "venta": _re(r"D[óo]lar Venta\s*:\s*([\d.,]+)", doc),
            "oficial": _re(r"D[óo]lar Oficial\s*:\s*([\d.,]+)", doc)}


@banco("FORTALEZA", "Banco Fortaleza", "https://www.bancofortaleza.com.bo/")
def fortaleza():
    # El HTML sólo trae marcadores vacíos que el JS rellena: se lee el proxy.
    r = S.get("https://www.bancofortaleza.com.bo/proxy-exchange.php", timeout=TIMEOUT,
              headers={"Accept": "application/json"})
    r.raise_for_status()
    j = r.json()["response"]
    return {"compra": _num(j["buyExchange"]), "venta": _num(j["saleExchange"]),
            "oficial": _num(j.get("officialExchange"))}


@banco("GANADERO", "Banco Ganadero", "https://www.bg.com.bo/personas/")
def ganadero():
    # Ganadero NO publica compra propia: muestra «T. Cambio Oficial» (el TCO) y
    # un «Valor Ref. Venta USD». Se guarda la venta y la compra queda vacía:
    # que la clave interna se llame `dcompra` no dice que sea su compra.
    r = S.get("https://www.bg.com.bo/personas/", timeout=TIMEOUT)
    r.raise_for_status()
    m = re.search(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', r.text, re.S)
    if not m:
        raise ValueError("sin __NEXT_DATA__")
    ind = {x["key"]: x["valor"] for x in json.loads(m.group(1))["props"]["pageProps"]["indicadores"]}
    return {"compra": None, "venta": _primero(ind["refVenta"]),
            "oficial": _primero(ind["dcompra"])}


@banco("MERCANTIL", "Banco Mercantil Santa Cruz", "https://www.bmsc.com.bo/")
def mercantil():
    r = S.get("https://backportal.bmsc.com.bo:1443/api/bmscservices/tipotre", timeout=TIMEOUT)
    r.raise_for_status()
    j = r.json()
    return {"compra": _num(j["compra"]), "venta": _num(j["venta"]), "oficial": _num(j.get("oficial"))}


@banco("BNB", "Banco Nacional de Bolivia", "https://www.bnb.com.bo/")
def bnb():
    # La portada redirige a http:// y esa dirección responde vacío: se pide
    # directo la página https y sin seguir redirecciones.
    r = S.get("https://www.bnb.com.bo/PortalBNB/Principal/BancaPersonas", timeout=TIMEOUT,
              allow_redirects=False)
    r.raise_for_status()
    t = _texto(r.text)
    return {"compra": _re(r"D[óo]lar Compra\s+([\d.,]+)", t),
            "venta": _re(r"D[óo]lar Venta\s+([\d.,]+)", t),
            "oficial": _re(r"D[óo]lar Oficial\s+([\d.,]+)", t)}


@banco("PRODEM", "Banco PRODEM", "https://www.prodem.bo/")
def prodem():
    r = S.get("https://www.prodem.bo/Inicio", timeout=TIMEOUT)
    r.raise_for_status()
    s = BeautifulSoup(r.text, "lxml")
    g = lambda i: _primero(s.select_one("#" + i).get_text())
    return {"compra": g("prodem-compra"), "venta": g("prodem-venta"), "oficial": g("dolar-bcb")}


@banco("PYME COM.", "Banco PYME de la Comunidad", "https://www.bco.com.bo/")
def comunidad():
    r = S.get("https://www.bco.com.bo/", timeout=TIMEOUT)
    r.raise_for_status()
    box = BeautifulSoup(r.text, "lxml").select_one("div.csc-tc")
    filas = {tr.th.get_text(strip=True).rstrip(":"): tr.td.get_text(strip=True)
             for tr in box.select("table.csc-tc__tabla tr") if tr.th and tr.td}
    return {"compra": _primero(filas["Compra"]),
            "venta": _primero(filas["Venta"]),
            "oficial": _primero(box.select_one(".csc-tc__titulo").get_text())}


@banco("ECOFUTURO", "Banco PYME Ecofuturo", "https://www.bancoecofuturo.com.bo/")
def ecofuturo():
    # La portada pesa ~40 MB (imágenes en base64) y el bloque está hacia el final.
    r = S.get("https://www.bancoecofuturo.com.bo/", timeout=180)
    r.raise_for_status()
    i = r.text.find("cotizacion-titulo")
    if i < 0:
        raise ValueError("sin bloque de cotización")
    d = {}
    for row in BeautifulSoup(r.text[max(0, i - 2000):i + 3000], "lxml").select("div.home-tarifario"):
        labs = row.select("label")
        if len(labs) == 2:
            d[labs[0].get_text(strip=True).rstrip(":")] = _primero(labs[1].get_text())
    return {"compra": d.get("Compra"), "venta": d.get("Venta"), "oficial": d.get("Oficial")}


@banco("SOLIDARIO", "BancoSol", "https://www.bancosol.com.bo/")
def bancosol():
    # Texto escrito a mano en el CMS: el formato puede variar.
    r = S.get("https://www.bancosol.com.bo/", timeout=TIMEOUT)
    r.raise_for_status()
    sec = BeautifulSoup(r.text, "lxml").select_one("section.indicadores-economicos")
    if sec is None:
        raise ValueError("sin sección de indicadores")
    t = sec.get_text(" ", strip=True)
    return {"compra": _re(r"cambio compra\s*:?\s*([\d.,]+)", t),
            "venta": _re(r"cambio venta\s*:?\s*([\d.,]+)", t),
            "oficial": _re(r"oficial[^:]*:\s*([\d.,]+)", t)}


@banco("UNIÓN", "Banco Unión", "https://www.bancounion.com.bo/")
def union():
    # Pie «Información Financiera», texto a mano: «Dólar Oficial 12,02 Compra BOB: 11,02 / Venta 12,12».
    r = S.get("https://www.bancounion.com.bo/", timeout=TIMEOUT)
    r.raise_for_status()
    m = re.search(r"D[óo]lar Oficial\s*([\d.,]+)\s*Compra BOB:\s*([\d.,]+)\s*/\s*Venta\s*([\d.,]+)",
                  _texto(r.text))
    if not m:
        raise ValueError("sin bloque de tipo de cambio")
    return {"compra": _num(m.group(2)), "venta": _num(m.group(3)), "oficial": _num(m.group(1))}


# ── Validación ──────────────────────────────────────────────────────────────

def validar(c: dict) -> str | None:
    """Motivo por el que una cotización NO es creíble, o None si lo es. La venta
    es obligatoria; la compra no (Ganadero no la publica), pero si está tiene
    que estar en rango y no por encima de la venta."""
    compra, venta = c.get("compra"), c.get("venta")
    if venta is None:
        return "no publica precio de venta"
    for k in ("compra", "venta", "oficial", "usdt_compra", "usdt_venta"):
        v = c.get(k)
        if v is not None and not 5 <= v <= 30:
            return f"{k} fuera de rango ({v})"
    if compra is not None and compra > venta:
        return f"compra {compra} mayor que venta {venta}"
    return None


def _fmt(v) -> str:
    """Forma canónica de un valor en el CSV (12.37, 12, vacío)."""
    if v is None or v == "":
        return ""
    if isinstance(v, (int, float)):
        return f"{float(v):.4f}".rstrip("0").rstrip(".")
    return str(v)


# ── Almacenamiento ──────────────────────────────────────────────────────────

def leer_obs() -> list[dict]:
    if not OBS_CSV.exists():
        return []
    with open(OBS_CSV, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def agregar_obs(obs: list[dict], nuevas: list[dict]) -> int:
    """Agrega una observación por banco si es la primera del día o si la
    cotización cambió desde la última guardada. Devuelve cuántas agregó."""
    ultima = {}
    for r in obs:
        ultima[r["banco"]] = r
    agregar = []
    for n in nuevas:
        u = ultima.get(n["banco"])
        igual = u is not None and all((u.get(k) or "") == _fmt(n.get(k))
                                      for k in CAMPOS if k not in ("fecha", "hora", "banco", "timestamp_utc"))
        if u is None or u["fecha"] != n["fecha"] or not igual:
            agregar.append({k: _fmt(n.get(k)) for k in CAMPOS})
    if agregar:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        nuevo = not OBS_CSV.exists() or OBS_CSV.stat().st_size == 0
        with open(OBS_CSV, "a", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=CAMPOS, lineterminator="\n")
            if nuevo:
                w.writeheader()
            w.writerows(agregar)
        obs.extend(agregar)
    return len(agregar)


# ── Exportación para el dashboard ───────────────────────────────────────────

def _mediana(xs: list[float]) -> float | None:
    xs = sorted(x for x in xs if x is not None)
    if not xs:
        return None
    k = len(xs) // 2
    return xs[k] if len(xs) % 2 else round((xs[k - 1] + xs[k]) / 2, 4)


def _f(v):
    return float(v) if v not in (None, "") else None


def exportar(obs: list[dict], estado: dict[str, str]) -> None:
    ultima: dict[str, dict] = {}
    for r in obs:
        ultima[r["banco"]] = r            # el CSV está en orden de captura
    # Una cotización de hace más de tres días ya no describe «hoy»: se lista con
    # su fecha pero no entra al gráfico ni a las medianas del día.
    limite = (datetime.now(BOT).date() - timedelta(days=3)).isoformat()

    # Lo que cada banco COMPRÓ de verdad en la última sesión del BCB (su TCO:
    # mediana ponderada de sus compras efectivas). Contra la pizarra dice mucho:
    # el 28-sep BancoSol publicaba compra 10,82 y compró a una mediana de 12,33.
    efectiva, sesion = {}, None
    try:
        t = json.loads(TCO_JSON.read_text(encoding="utf-8"))
        sesion = t.get("fecha_hoy")
        efectiva = {x["banco"]: x.get("tco") for x in t.get("bancos_hoy", []) if x.get("tco") is not None}
    except (OSError, ValueError):
        pass

    filas = []
    for b in BANCOS:
        r = ultima.get(b["banco"])
        fila = {"banco": b["banco"], "nombre": b["nombre"], "fuente": b["fuente"],
                "compra_efectiva": efectiva.get(b["banco"])}
        if r and r["fecha"] >= limite:
            fila.update({k: _f(r.get(k)) for k in ("compra", "venta", "oficial", "usdt_compra", "usdt_venta")})
            fila.update({"fecha": r["fecha"], "hora": r["hora"]})
        elif r:
            fila.update({"ultima_fecha": r["fecha"], "motivo": f"sin cotización desde el {r['fecha']}"})
        if estado.get(b["banco"]):
            fila["motivo"] = estado[b["banco"]]
        filas.append(fila)

    # Serie diaria: la última cotización de cada banco en cada día.
    por_dia: dict[str, dict[str, dict]] = {}
    for r in obs:
        por_dia.setdefault(r["fecha"], {})[r["banco"]] = r
    serie = []
    for f in sorted(por_dia):
        rs = list(por_dia[f].values())
        comp = [v for v in (_f(r["compra"]) for r in rs) if v is not None]
        vent = [v for v in (_f(r["venta"]) for r in rs) if v is not None]
        serie.append({"f": f, "n": len(rs),
                      "compra_med": _mediana(comp), "venta_med": _mediana(vent),
                      "compra_min": min(comp, default=None), "compra_max": max(comp, default=None),
                      "venta_min": min(vent, default=None), "venta_max": max(vent, default=None)})

    hoy = datetime.now(BOT).date().isoformat()
    out = {
        "actualizado": datetime.now(timezone.utc).isoformat(),
        "fecha": max((r["fecha"] for r in obs), default=None),
        "hoy": hoy,
        "sesion_bcb": sesion,       # sesión de la que sale `compra_efectiva`
        "bancos": filas,
        "serie": serie,
    }
    OUT_JSON.write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    con = [f for f in filas if f.get("venta") is not None]
    print(f"[OK] bancos_tc.json — {len(con)}/{len(filas)} bancos con cotización, "
          f"{len(serie)} día(s) de serie")


def main() -> None:
    ahora = datetime.now(BOT)
    ts = datetime.now(timezone.utc).isoformat()
    nuevas, estado = [], {}
    for b in BANCOS:
        try:
            c = b["fetch"]()
            motivo = validar(c)
        except Exception as e:                       # noqa: BLE001 — cada banco es independiente
            c, motivo = {}, f"no respondió ({type(e).__name__})"
        if motivo:
            estado[b["banco"]] = motivo
            print(f"[WARN] {b['banco']}: {motivo}", file=sys.stderr)
            continue
        nuevas.append({"fecha": ahora.date().isoformat(), "hora": ahora.strftime("%H:%M"),
                       "banco": b["banco"], "timestamp_utc": ts,
                       **{k: c.get(k) for k in ("compra", "venta", "oficial", "usdt_compra", "usdt_venta")}})
        print(f"[OK] {b['banco']:<10} compra {_fmt(c.get('compra')) or '—':>6} · venta {_fmt(c['venta']):>6}"
              + (f" · oficial {_fmt(c['oficial'])}" if c.get("oficial") else "")
              + (f" · USDT {_fmt(c.get('usdt_compra'))}/{_fmt(c.get('usdt_venta'))}" if c.get("usdt_venta") else ""))

    obs = leer_obs()
    n = agregar_obs(obs, nuevas)
    print(f"[OK] bancos_tc.csv — {n} observación(es) nueva(s)")
    exportar(obs, estado)

    if not nuevas:
        print("::error::Ningún banco devolvió una cotización válida: ¿cambiaron los sitios "
              "o falló la red del runner?")
        sys.exit(1)
    if estado:
        print(f"::warning::{len(estado)} banco(s) sin cotización en esta corrida: "
              + "; ".join(f"{k}: {v}" for k, v in estado.items()))


if __name__ == "__main__":
    main()
