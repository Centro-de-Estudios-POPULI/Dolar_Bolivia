# Dólar Referencial Bolivia

Dashboard de seguimiento diario del tipo de cambio del dólar en Bolivia. Combina datos oficiales del Banco Central de Bolivia (BCB) con el mercado paralelo (USDT/BOB en Binance P2P).

**[Ver dashboard](https://centro-de-estudios-populi.github.io/Dolar_Bolivia/)**

> **Cambio de política monetaria (2026):** el BCB publica ahora el **Tipo de Cambio Oficial (TCO)** en plataforma propia — el promedio ponderado de las operaciones de compra de divisas de la banca. El **valor referencial** anterior se conserva como registro histórico. Por disposición del BCB, el **precio de venta oficial = TCO + 0,10 Bs** (margen de 10 ctvs).

## Fuentes de datos

| Fuente | Qué aporta | Frecuencia |
|--------|-----------|------------|
| [BCB — TCO](https://www.bcb.gob.bo/tco_reporte_detalle_historico.php) | **Tipo de Cambio Oficial** (promedio ponderado de compra), vía CSV oficial. Desde jul-2026 el endpoint acepta `?desde=&hasta=` y devuelve **toda** la serie: en cada corrida se baja el histórico completo y cualquier día perdido se auto-recupera | Diaria |
| [BCB — SVG v1](https://www.bcb.gob.bo/valor_referencial_venta_svg.php) | Precio de venta referencial (histórico) | Diaria |
| [BCB — HTML v2](https://www.bcb.gob.bo/valor_referencial_compra_svg_v2.php) | Precio de compra ponderado, volúmenes y transacciones por banco | Diaria |
| [mauforonda/dolares](https://github.com/mauforonda/dolares) | Mediana de ofertas USDT/BOB en Binance P2P (~cada 30 min) | Intra-día |

### Nota sobre USDT

Se usa la **mediana** de las ofertas listadas en Binance P2P (columna `median` de mauforonda), no el VWAP, porque este último se infla por ofertas outlier a precios irreales (20+ Bs). La mediana refleja el precio de mercado que un usuario real encuentra en la plataforma.

## Estructura

```
├── index.html                         # Dashboard (ECharts 5.4.3)
├── scripts/
│   ├── scrape_dolar.py                # Scraper diario (BCB referencial + USDT)
│   ├── scrape_tco.py                  # Scraper diario del Tipo de Cambio Oficial (TCO)
│   └── backfill_historico.py          # Recálculo histórico completo
├── data/
│   ├── dolar.csv                      # Serie diaria referencial + USDT (fuente de verdad)
│   ├── tco.csv                        # Serie diaria GLOBAL del TCO (fuente de verdad)
│   ├── tco_bancos.csv                 # Detalle POR BANCO del TCO por día (TCO, tx, monto)
│   ├── tco_raw/<fecha>.csv            # Copia verbatim del reporte del BCB (red de seguridad sin pérdida)
│   ├── tco.json                       # JSON del TCO: serie global + venta (+0,10) + USDT + detalle por banco
│   ├── historico.json                 # JSON para gráficos de evolución
│   └── bancos.json                    # JSON del referencial por banco (histórico, congelado)
├── .github/workflows/
│   ├── update_dolar.yml               # GitHub Actions: USDT diario (referencial congelado), 1x/día
│   └── update_tco.yml                 # GitHub Actions: TCO, varias veces/día (tolera el WAF del BCB)
└── requirements.txt                   # requests, beautifulsoup4, lxml
```

## Automatización

Tras el cambio de régimen (jun-2026) el **valor referencial** del BCB quedó congelado; el **TCO** es ahora la serie oficial viva.

- **`update_tco.yml`** — captura el TCO **varias veces al día** (19:17 BOT con espera dentro del job hasta el cierre de las 20:05, más reintentos a las 20:38, 21:23, 23:07, 08:19, 11:07 y 11:52). El WAF del BCB bloquea (403) de forma intermitente a los runners de GitHub, así que reintentar en varias franjas evita quedarse sin el detalle del día. El scraper es idempotente y, además, cada corrida rebaja la serie completa: un día perdido se recupera solo.
- **`update_dolar.yml`** — corre **1x/día** (23:00 BOT) y solo mantiene viva la serie **USDT** (paralelo) por fecha de calendario. Si el BCB reactivara el referencial, también lo recogería.

> **Auto-recuperación:** desde jul-2026 el CSV del BCB acepta rango de fechas, así que cada corrida rebaja la serie completa y **rellena sola** cualquier sesión que se hubiera perdido. Aun así se conserva la copia propia en `data/tco_raw/<fecha>.csv` por si el BCB algún día recorta la ventana.

### Cuando el BCB no publica los totales

El reporte trae dos vistas del mismo día: la fila **TOTAL** (nº de transacciones y monto por banco) y las filas de **distribución** (esas mismas operaciones abiertas por nivel de precio). La segunda contiene a la primera.

El BCB deja huecos en la primera, y cada vez mayores: el **20 y 21 de julio de 2026** publicó el detalle por banco pero dejó vacía la columna *TOTAL BANCOS*; el **31 de julio de 2026** dejó la **fila TOTAL entera vacía** (30 de 30 celdas) con la distribución completa. Sin tratamiento, esa sesión aparecía con **0 transacciones y 0 volumen** en los 14 bancos: se caían el volumen del día, la tabla del reporte y la serie de volumen por entidad — aunque el dato estaba publicado, solo que sin sumar.

El scraper **reconstruye los totales sumando la distribución**. Verificado contra los 24 días que sí traen fila TOTAL: el nº de transacciones coincide **exacto en los 24**, y el monto difiere entre 0 y 4 USD sobre decenas de millones (0,000 %) por el redondeo con que el BCB publica cada nivel de precio. Si más tarde el BCB publica la fila TOTAL, el upsert la reemplaza por la oficial: la reconstrucción nunca pisa un dato bueno.

Las sesiones reconstruidas se marcan en `tco.json` (`reconstruidas[]` y `rec: true` en la serie) y el dashboard lo advierte al pie del bloque de volumen, para no presentar como sumado por el emisor algo que sumamos nosotros.

## Ejecución local

```bash
pip install -r requirements.txt
python scripts/scrape_dolar.py            # Referencial + USDT (diaria)
python scripts/scrape_tco.py              # Tipo de Cambio Oficial (TCO)
python scripts/backfill_historico.py      # Recálculo histórico completo
```

## Tecnologías

- **Frontend**: ECharts 5.4.3, Inter + IBM Plex Mono, modo claro/oscuro
- **Backend**: Python 3.12, requests, BeautifulSoup4, lxml
- **Hosting**: GitHub Pages
- **CI/CD**: GitHub Actions

---

Desarrollado por [Centro de Estudios Populi](https://populi.org.bo)
