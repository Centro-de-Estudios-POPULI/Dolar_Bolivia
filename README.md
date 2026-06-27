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
│   ├── dolar.csv                      # Serie diaria referencial (fuente de verdad)
│   ├── tco.csv                        # Serie diaria del TCO (fuente de verdad)
│   ├── tco.json                       # JSON del TCO: TCO, venta (+0,10) y USDT
│   ├── historico.json                 # JSON para gráficos de evolución
│   └── bancos.json                    # JSON para gráfico por banco + tabla
├── .github/workflows/
│   ├── update_dolar.yml               # GitHub Actions: referencial, 5x/día lun-vie
│   └── update_tco.yml                 # GitHub Actions: TCO, diario 20:01 BOT
└── requirements.txt                   # requests, beautifulsoup4, lxml
```

## Automatización

GitHub Actions ejecuta el scraper **5 veces al día** en días hábiles (horario Bolivia):

| Hora BOT | Descripción |
|----------|-------------|
| 10:00 | Apertura matutina |
| 13:00 | Mediodía |
| 16:00 | Tarde |
| 19:00 | Cierre vespertino |
| 23:30 | Cierre final del día |

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
