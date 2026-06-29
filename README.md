# Dólar Referencial Bolivia

Dashboard de seguimiento diario del tipo de cambio del dólar en Bolivia. Combina datos oficiales del Banco Central de Bolivia (BCB) con el mercado paralelo (USDT/BOB en Binance P2P).

**[Ver dashboard](https://centro-de-estudios-populi.github.io/Dolar_Bolivia/)**

> **Cambio de política monetaria (2026):** el BCB publica ahora el **Tipo de Cambio Oficial (TCO)** en plataforma propia — el promedio ponderado de las operaciones de compra de divisas de la banca. El **valor referencial** anterior se conserva como registro histórico. Por disposición del BCB, el **precio de venta oficial = TCO + 0,10 Bs** (margen de 10 ctvs).

## Fuentes de datos

| Fuente | Qué aporta | Frecuencia |
|--------|-----------|------------|
| [BCB — TCO](https://www.bcb.gob.bo/tco_reporte_detalle_historico.php) | **Tipo de Cambio Oficial** (promedio ponderado de compra), vía CSV oficial. La plataforma solo expone el reporte más reciente (próximo día hábil); la serie se construye capturando a diario | Diaria |
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

- **`update_tco.yml`** — captura el TCO **varias veces al día** (20:01, 23:01, 02:01 y 07:01 BOT). El reporte del BCB es estático (forward-dated) y su WAF bloquea (403) de forma intermitente a los runners de GitHub; como **no hay histórico descargable**, reintentar en varias franjas evita perder el detalle por banco de un día. El scraper es idempotente.
- **`update_dolar.yml`** — corre **1x/día** (23:00 BOT) y solo mantiene viva la serie **USDT** (paralelo) por fecha de calendario. Si el BCB reactivara el referencial, también lo recogería.

> ⚠️ **Sin backfill:** la plataforma del TCO solo expone el reporte más reciente. Cada día no capturado se pierde para siempre, por eso el scraper persiste el detalle completo (`tco_bancos.csv`) y una copia cruda (`tco_raw/`).

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
