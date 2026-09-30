# Tipo de Cambio Oficial · Bolivia

Dashboard de seguimiento diario del tipo de cambio del dólar en Bolivia. Combina el Tipo de Cambio Oficial del Banco Central de Bolivia (BCB), la compra y venta que publica cada banco y el mercado paralelo (USDT/BOB en Binance P2P).

**[Ver dashboard](https://centro-de-estudios-populi.github.io/Dolar_Bolivia/)**

> **Cambio de política monetaria (2026):** desde el 26-jun-2026 el BCB publica el **Tipo de Cambio Oficial (TCO)**, que resume las operaciones de compra de dólares de la banca. Fue la **media** ponderada por monto hasta la sesión del 24-sep-2026 y es la **mediana** ponderada por monto desde la del 25-sep. Hasta entonces el **precio de venta oficial era TCO + 0,10 Bs**; desde la R.D. 142/2026 **cada banco fija y publica su propia compra y venta**, que este repo captura en `data/bancos_tc.csv`. El **valor referencial** anterior se conserva como registro histórico.

## Fuentes de datos

| Fuente | Qué aporta | Frecuencia |
|--------|-----------|------------|
| [BCB — TCO](https://www.bcb.gob.bo/bcb_tco_publico_detalle_historico.php) | **Tipo de Cambio Oficial** (mediana ponderada de las compras desde el 25-sep-2026; antes, media), vía CSV oficial `bcb_tco_publico_descargar_csv.php`. Acepta `?desde=&hasta=` y devuelve **toda** la serie: en cada corrida se baja el histórico completo y cualquier día perdido se auto-recupera | Diaria (se publica de noche) |
| Webs de los 14 bancos | **Compra y venta publicadas** por cada entidad (BISA, BCP, Nación Argentina, Económico, FIE, Fortaleza, Ganadero, Mercantil Santa Cruz, BNB, PRODEM, PYME de la Comunidad, Ecofuturo, BancoSol, Unión). Siete lo sirven en el HTML y siete en un JSON/XML propio; `scrape_bancos_tc.py` los lee sin navegador | 4× día |
| [BCB — SVG v1](https://www.bcb.gob.bo/valor_referencial_venta_svg.php) | Precio de venta referencial (histórico) | Diaria |
| [BCB — HTML v2](https://www.bcb.gob.bo/valor_referencial_compra_svg_v2.php) | Precio de compra ponderado, volúmenes y transacciones por banco | Diaria |
| [mauforonda/dolares](https://github.com/mauforonda/dolares) | Mediana de ofertas USDT/BOB en Binance P2P (~cada 30 min) | Intra-día |

### Nota sobre USDT

Se usa la **mediana** de las ofertas listadas en Binance P2P (columna `median` de mauforonda), no el VWAP, porque este último se infla por ofertas outlier a precios irreales (20+ Bs). La mediana refleja el precio de mercado que un usuario real encuentra en la plataforma.

### Variación por sesión

El dashboard muestra cuánto se movió el tipo de cambio en cada sesión respecto de la anterior, en centavos de boliviano. Dos criterios:

- **Cada variación se fecha en el día en que RIGE**, no en el de la sesión que la fijó: el BCB publica el TCO de cada sesión por la noche y rige desde el día calendario siguiente (el tramo exacto lo declara el propio BCB en la columna *Vigencia*). Así el gráfico queda alineado con las series de precio, que también se grafican por fecha de vigencia. Los días rellenados por *carry-forward* (fines de semana y feriados) no son sesiones y no generan barra.
- **El salto referencial → TCO (jun-2026) no se computa.** Son dos regímenes distintos y su diferencia no sería una variación de mercado; el corte se marca en el gráfico. Por lo mismo, la variación acumulada solo se expresa en % cuando el rango elegido no cruza ese corte.

## Estructura

```
├── index.html                         # Dashboard (ECharts 5.4.3)
├── scripts/
│   ├── scrape_dolar.py                # Scraper diario (BCB referencial + USDT)
│   ├── scrape_tco.py                  # Scraper diario del Tipo de Cambio Oficial (TCO)
│   ├── scrape_bancos_tc.py            # Compra y venta que publica cada banco en su web
│   └── backfill_historico.py          # Recálculo histórico completo
├── data/
│   ├── dolar.csv                      # Serie diaria referencial + USDT (fuente de verdad)
│   ├── tco.csv                        # Serie diaria GLOBAL del TCO (fuente de verdad)
│   ├── tco_bancos.csv                 # Detalle POR BANCO del TCO por día (TCO, tx, monto)
│   ├── tco_raw/<fecha>.csv            # Copia verbatim del reporte del BCB (red de seguridad sin pérdida)
│   ├── tco_dist/<fecha>.json          # Caja y bigotes por banco de cada sesión (selector de día del boxplot)
│   ├── tco.json                       # JSON del TCO: serie global (método, media y mediana, vigencia) + USDT + detalle por banco
│   ├── bancos_tc.csv                  # Observaciones de compra/venta por banco (una fila cuando cambia, al menos una por día)
│   ├── bancos_tc.json                 # Última cotización de cada banco + compra efectiva según el BCB + serie de medianas
│   ├── historico.json                 # JSON para gráficos de evolución
│   └── bancos.json                    # JSON del referencial por banco (histórico, congelado)
├── .github/workflows/
│   ├── update_dolar.yml               # GitHub Actions: USDT diario (referencial congelado), 1x/día
│   ├── update_tco.yml                 # GitHub Actions: TCO, varias veces por noche y por la mañana (tolera el WAF del BCB)
│   └── update_bancos_tc.yml           # GitHub Actions: compra/venta de los bancos, 4× día
└── requirements.txt                   # requests, beautifulsoup4, lxml
```

## Automatización

Tras el cambio de régimen (jun-2026) el **valor referencial** del BCB quedó congelado; el **TCO** es ahora la serie oficial viva.

- **`update_tco.yml`** — captura el TCO **de noche** (20:38, 22:53, 00:37, 01:43 y 03:13 BOT) y lo confirma **por la mañana** (08:19 y 11:07). Desde la sesión del 25-sep-2026 el BCB ya no publica a las ~20:00: la sesión aparece entre las ~22:45 y la 01:30 BOT (medido en los logs de 70 corridas y con una sonda cada 4 minutos). El WAF del BCB bloquea (403) de forma intermitente a los runners de GitHub, así que reintentar en varias franjas evita quedarse sin el detalle del día. El scraper es idempotente y cada corrida rebaja la serie completa: un día perdido se recupera solo. El paso de commit corre **aunque el scraper termine en rojo**, porque el scraper exporta primero y falla después.
- **`update_bancos_tc.yml`** — lee la compra y la venta de los 14 bancos **4 veces al día** (07:47, 10:13, 14:13 y 18:13 BOT). Guarda una observación por banco cuando la cotización cambia y al menos una por día. Un banco que no responde se lista sin número; si no responde ninguno, la corrida va a rojo.
- **`update_dolar.yml`** — mantiene viva la serie **USDT** (paralelo) por fecha de calendario y **recalcula cada día con sus 24 horas** de snapshots. Si el BCB reactivara el referencial, también lo recogería.

> **Auto-recuperación:** desde jul-2026 el CSV del BCB acepta rango de fechas, así que cada corrida rebaja la serie completa y **rellena sola** cualquier sesión que se hubiera perdido. Aun así se conserva la copia propia en `data/tco_raw/<fecha>.csv` por si el BCB algún día recorta la ventana.

### Elegir el día del boxplot (sección 08)

La distribución de tipos de cambio por banco se puede ver de **cualquier sesión desde el cambio de régimen**, no solo de la última. El scraper archiva la caja y bigotes de cada día en `data/tco_dist/<fecha>.json` — un archivo por sesión, no un blob que crece.

Así el dashboard abre igual de rápido que antes (la última sesión ya viaja dentro de `tco.json`, en `dist_hoy`) y solo baja el archivo del día que el usuario elige, que además queda en caché. `tco.json` lleva únicamente la **lista** de sesiones disponibles (`dist_fechas`) para armar el selector.

Elegir la última equivale a *seguir la última*: el auto-refresh salta solo al día nuevo. Si hay una sesión pasada en pantalla, la vista se queda ahí y el gráfico lo advierte.

> `python scripts/scrape_tco.py --desde-raw` rehace `tco_dist/` y `tco.json` desde el archivo local `tco_raw/`, sin red. Es el backfill de las sesiones anteriores a que existiera el selector y la vía de recuperación si el BCB recorta la ventana del endpoint.

### Septiembre de 2026: el BCB cambió el formato, el método y la vigencia

Tres cambios que el BCB introdujo sin aviso entre el 25 y el 26 de septiembre de 2026, y cómo los toma el scraper:

- **Números como texto de Excel.** Desde el 26-sep el CSV escribe cada monto como `="1.765"` (en el archivo, con las comillas duplicadas del formato CSV). El parser anterior no lo entendía y el *upsert* dejó **sin volumen ni transacciones las 64 sesiones y los 896 banco-día** en una sola corrida que terminó en verde. El parser ahora usa el lector CSV real y quita el envoltorio `="…"`; la recuperación se verificó contra la versión anterior al borrado: **62 de 62 sesiones y 868 de 868 banco-día idénticos**. Y el *upsert* ya no deja que un valor vacío pise uno guardado: lo conserva y pone la corrida en rojo.
- **El TCO pasó a ser la MEDIANA ponderada por monto.** La nota metodológica del BCB dice: *«TCO: Corresponde a la mediana ponderada por monto de los tipos de cambio de las operaciones de compra de dólares realizadas por los Bancos Múltiples, Bancos PyME, Banco Público con sus clientes y el Banco Central de Bolivia»*. La fecha sale de los datos: recalculando cada sesión desde su distribución por nivel de precio, **hasta la sesión del 24-sep el TCO publicado es exactamente la media ponderada** (64 de 64, y cada banco su propia media) y **desde la del 25-sep es exactamente la mediana** (el total y 13/13 y 14/14 bancos). `METODO_MEDIANA_DESDE` declara la fecha y cada corrida la verifica: si el TCO publicado deja de coincidir con el estadístico declarado, la corrida termina en rojo. `tco.csv` guarda los dos estadísticos (`media_pond`, `mediana_pond`) y el `metodo` de cada sesión, así se ve cuánto movió el TCO el cambio de método (28-sep: mediana **12,02** = oficial · media 11,97).
- **La vigencia viene declarada.** La columna *Vigencia* trae un día (`2026-09-29`) o un rango cuando la sesión cubre un fin de semana o un feriado (`2026-09-26 al 2026-09-28`). Se guarda como `vig_desde`/`vig_hasta` y manda sobre la regla del «día calendario siguiente», que queda de respaldo.

### El USDT diario es el promedio del día completo

Cada fila de `dolar.csv` se escribía una sola vez, en la corrida que la creaba — y como GitHub atrasa el cron de la noche hasta pasada la medianoche, esa corrida creaba el día nuevo con apenas la primera hora de snapshots de mauforonda y nadie lo volvía a tocar. Medido el 29-sep-2026: **124 de 233 días** distintos del promedio del día completo, el peor el 18-sep (**11,58** guardado contra **11,92** real). Ahora cada corrida recalcula todos los días con lo que publica mauforonda: el día anterior queda cerrado con sus 24 horas y el de hoy es «lo que va del día». Un valor que la fuente ya no trae nunca borra el guardado.

### Compra y venta en los bancos: lo publicado no es lo transado

Desde la R.D. 142/2026 cada banco fija su precio de compra y de venta y lo publica en su web. `scrape_bancos_tc.py` lo captura cuatro veces al día y, al lado, guarda lo que ese banco **efectivamente pagó** en sus compras de la última sesión según el reporte del BCB (su TCO: mediana ponderada por monto). La diferencia es grande: el 29-sep-2026 BancoSol publicaba compra **10,82** y en la sesión del 28-sep compró a una mediana de **12,33**; Mercantil Santa Cruz publicaba 10,77 y compró a 12,00. La «pizarra» es lo que el banco ofrece al público en ventanilla; el reporte del BCB recoge todas sus operaciones, incluidas las de montos grandes. Ganadero no publica una compra propia (sólo el TCO y una venta de referencia): se guarda su venta y la compra queda vacía. Ningún banco distingue canal ni monto en su web.

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
python scripts/scrape_bancos_tc.py        # Compra y venta de cada banco
python scripts/backfill_historico.py      # Recálculo histórico completo
```

## Tecnologías

- **Frontend**: ECharts 5.4.3, Inter + IBM Plex Mono, modo claro/oscuro
- **Backend**: Python 3.12, requests, BeautifulSoup4, lxml
- **Hosting**: GitHub Pages
- **CI/CD**: GitHub Actions

---

Desarrollado por [Centro de Estudios Populi](https://populi.org.bo)
