/* ════════════════════════════════════════════════════════════════════════════
   POPULI · Molde de monitores v2.3 — piezas comunes de los gráficos (window.PM).

   FUENTE ÚNICA: populi-marca/monitor/monitor.js. Cada repo tiene una COPIA en
   embed/comun/ que escribe `python populi-marca/monitor/sincronizar.py`.
   Ningún número se escribe acá: cada gráfico lee sus ../data/*.json.

   Convenciones (una sola para todo el ecosistema):
   - Números es-BO: coma decimal, punto de miles, signo menos tipográfico.
     «4.148» · «549,9» · «−2,1» · «5,0%» (sin espacio, como escribe la casa).
   - Fechas en minúscula: mensual «ago 2026» · diaria «4 oct 2026» ·
     semanal «sem 4 · sep 2026» · trimestral «T2 2026» · anual «2026».
   - Eje de tiempo: rótulos DERECHOS, nunca girados, en la misma tipografía que el
     eje Y; el año se escribe una vez (en enero, más oscuro que los meses), marcas
     menores entre rótulos, y la escala (días, meses, trimestres o años cada
     1/2/5/10) se elige por el ancho real del lienzo: no se pisan ni en un teléfono.
   - El gráfico avisa su alto a la página que lo incrusta (protocolo POPULI:
     {populiEmbed:'height', height}); una pregunta abierta agranda el iframe.
   - v2.3: el gráfico se dibuja (y las cifras cuentan) cuando entra en pantalla; cada
     cifra puede llevar su minilínea de tendencia; descarga de imagen y de datos.
   ════════════════════════════════════════════════════════════════════════════ */
(function () {
  'use strict';
  var PM = { version: '2.3.1' };
  var root = document.documentElement;

  // ── Paleta de la marca (populi-marca/paleta.json), por familias ────────────
  PM.C = {
    rojo: '#C71E1D', rojoClaro: '#E8706B', rojoOscuro: '#8B1A1A',
    turquesa: '#0A9396', menta: '#94D2BD', petroleo: '#005F73',
    oro: '#EE9B00', arena: '#E9D8A6', oroOscuro: '#A86E00',
    naranja: '#E57D22', tierra: '#DF5D25', granate: '#9B2226',
    tinta: '#001219', pizarra: '#5C6B70', gris: '#8A9699', tenue: '#C9CDCE',
    // tintes de la paleta (TINT_* de paleta.py): rellenos por signo, nunca series
    tintRojo: '#F8E5E3', tintTurquesa: '#D9EAE6', tintRojoOscuro: '#271515', tintTurquesaOscuro: '#132627'
  };
  // Relleno por signo contra una referencia (el 0 de un crecimiento, el 100 de un nivel): SÓLIDO y con los
  // tintes oficiales, el mismo en todos los gráficos. Sobre la referencia, turquesa; debajo, rojo. En oscuro,
  // la pareja derivada a la misma distancia visual de la tarjeta. La línea encima va en tinta.
  PM.tinte = function (arriba) {
    var C = PM.C;
    return PM.dk() ? (arriba ? C.tintTurquesaOscuro : C.tintRojoOscuro) : (arriba ? C.tintTurquesa : C.tintRojo);
  };

  PM.dk = function () { return root.getAttribute('data-theme') === 'dark'; };
  var exportando = false;   // mientras se dibuja la imagen de descarga: formato de PC aunque sea un teléfono
  PM.pequeno = function () { return !exportando && window.innerWidth <= 640; };
  PM.var = function (n) { return getComputedStyle(root).getPropertyValue(n).trim(); };

  function mezclar(base, extra) {             // mezcla profunda de objetos planos de opciones
    if (!extra) return base;
    Object.keys(extra).forEach(function (k) {
      var v = extra[k];
      if (v && typeof v === 'object' && !Array.isArray(v) && typeof v !== 'function' &&
          base[k] && typeof base[k] === 'object' && !Array.isArray(base[k])) base[k] = mezclar(base[k], v);
      else base[k] = v;
    });
    return base;
  }
  PM.mezclar = mezclar;

  // ── Color de serie y color de TEXTO ────────────────────────────────────────
  // Una serie puede declarar su tono para el tema oscuro: {claro:'#005F73', oscuro:'#0A9396'}.
  // La tinta se invierte sola.
  PM.col = function (c) {
    if (c && typeof c === 'object') return PM.dk() ? (c.oscuro || c.claro) : c.claro;
    if (PM.dk() && (c === '#001219' || c === '#0D1B2A')) return '#E2E8F0';
    return c;
  };
  // Una cifra «en el color de la serie» tiene que leerse: en claro los tonos pálidos bajan a su
  // paso oscuro de la misma familia; en oscuro los tonos hondos suben (regla 6 de la marca).
  var TX_CLARO = { '#EE9B00': '#A86E00', '#E9D8A6': '#A86E00', '#94D2BD': '#0A9396', '#E8706B': '#C71E1D',
                   '#E57D22': '#DF5D25', '#8A9699': '#5C6B70', '#C9CDCE': '#5C6B70', '#6E7A7D': '#5C6B70' };
  var TX_OSCURO = { '#C71E1D': '#E8706B', '#9B2226': '#E8706B', '#8B1A1A': '#E8706B', '#005F73': '#94D2BD',
                    '#001219': '#E2E8F0', '#0D1B2A': '#E2E8F0', '#5C6B70': '#8A9699', '#3A4549': '#8A9699', '#6E7A7D': '#8A9699' };
  // Lo que no está en las tablas y queda casi invisible (un negro sobre la tarjeta oscura, un gris
  // niebla sobre la clara) se lee en pizarra.
  function txOscuro(c) { return TX_OSCURO[c] || (lum(c) < 0.035 ? '#8A9699' : c); }
  function txClaro(c) { return TX_CLARO[c] || (lum(c) > 0.55 ? '#5C6B70' : c); }
  PM.tx = function (c) { c = String(PM.col(c)).toUpperCase(); return PM.dk() ? txOscuro(c) : txClaro(c); };
  PM.txTip = function (c) { return txOscuro(String(PM.col(c)).toUpperCase()); };   // el tooltip es oscuro en los dos temas
  function lum(hex) {
    var n = parseInt(String(hex).slice(1), 16);
    var f = function (x) { x /= 255; return x <= 0.03928 ? x / 12.92 : Math.pow((x + 0.055) / 1.055, 2.4); };
    return 0.2126 * f(n >> 16) + 0.7152 * f((n >> 8) & 255) + 0.0722 * f(n & 255);
  }
  PM.sobre = function (c) { return lum(PM.col(c)) > 0.3 ? '#001219' : '#FFFFFF'; };   // texto sobre un relleno de ese color
  PM.rgba = function (c, a) {
    var n = parseInt(String(PM.col(c)).slice(1), 16);
    return 'rgba(' + (n >> 16) + ',' + ((n >> 8) & 255) + ',' + (n & 255) + ',' + a + ')';
  };

  // ── Tema y alto (protocolo POPULI) ─────────────────────────────────────────
  var oyentes = [];
  PM.alCambiarTema = function (f) { oyentes.push(f); };
  var qs = new URLSearchParams(location.search);
  var temaIni = qs.get('tema') || qs.get('theme');
  if (temaIni === 'dark' || temaIni === 'light') root.setAttribute('data-theme', temaIni);
  else if (!root.getAttribute('data-theme')) root.setAttribute('data-theme', 'light');
  function ponerTema(t) {
    if ((t !== 'dark' && t !== 'light') || t === root.getAttribute('data-theme')) return;
    root.setAttribute('data-theme', t);
    oyentes.forEach(function (f) { try { f(); } catch (e) { console.error(e); } });
  }

  var enIframe = window.parent !== window, ultimoAlto = 0, cuadro = 0;
  // El alto es el del CONTENIDO: scrollHeight no sirve porque nunca baja del alto del iframe.
  PM.alto = function () { return Math.ceil(root.getBoundingClientRect().height); };
  function avisarAlto(forzar) {
    if (!enIframe) return;
    var h = PM.alto();
    if (!forzar && Math.abs(h - ultimoAlto) < 2) return;
    ultimoAlto = h;
    window.parent.postMessage({ populiEmbed: 'height', height: h }, '*');
  }
  PM.avisarAlto = function () { avisarAlto(true); };
  // Con temporizador y no con requestAnimationFrame: Chrome congela los cuadros de un iframe
  // que está fuera de la vista y el aviso llegaba recién al volver a verlo (la página saltaba).
  // getBoundingClientRect fuerza el cálculo aunque el iframe no se esté pintando.
  PM.programarAlto = function () {
    clearTimeout(cuadro);
    cuadro = setTimeout(function () { avisarAlto(false); }, 40);
  };
  window.addEventListener('message', function (e) {
    var d = e.data;
    if (!d || typeof d !== 'object') return;
    if (d.theme) ponerTema(d.theme);
    if (d.populiEmbed === 'ask-height') { root.classList.add('alto-auto'); avisarAlto(true); }
  });
  if (enIframe) {
    // La página puede no estar escuchando todavía: saludo con reintentos.
    [0, 150, 600, 1600, 4000].forEach(function (t) {
      setTimeout(function () { window.parent.postMessage({ populiEmbed: 'hello' }, '*'); avisarAlto(true); }, t);
    });
    if (window.ResizeObserver) new ResizeObserver(function () { PM.programarAlto(); }).observe(root);
    window.addEventListener('load', function () { avisarAlto(true); });
    if (document.fonts && document.fonts.ready) document.fonts.ready.then(function () { avisarAlto(true); });
  }

  // ── Números (es-BO) ────────────────────────────────────────────────────────
  var NF = {}, NB = ' ';                 // espacio duro: «ago 2026» y «2,0 pp» nunca se parten
  PM.num = function (x, dec) {
    if (x === null || x === undefined || x === '' || isNaN(x)) return '—';
    dec = dec == null ? 1 : dec;
    var f = NF[dec] || (NF[dec] = new Intl.NumberFormat('es-BO', { minimumFractionDigits: dec, maximumFractionDigits: dec }));
    var t = f.format(+x);                                 // Intl redondea simétrico: −2,5 → −3 como 2,5 → 3
    if (/^-[0.,]+$/.test(t)) t = t.slice(1);              // sin «−0,0»
    return t.replace('-', '−');
  };
  PM.signo = function (x, dec) {
    if (x === null || x === undefined || isNaN(x)) return '—';
    var s = PM.num(x, dec);
    return (+x > 0 && s.replace(/[0,.]/g, '') !== '') ? '+' + s : s;
  };
  PM.pct = function (x, dec, conSigno) {
    if (x === null || x === undefined || isNaN(x)) return '—';
    return (conSigno ? PM.signo(x, dec) : PM.num(x, dec)) + '%';
  };
  PM.pp = function (x, dec) { return PM.signo(x, dec == null ? 1 : dec) + NB + 'pp'; };
  // Magnitudes: sin decimales desde mil, uno por debajo. «4.148» · «549,9» · «65,2»
  PM.auto = function (x) { return PM.num(x, Math.abs(x) >= 1000 ? 0 : 1); };
  // Montos en millones: PM.mm(4148.3) → «$4.148 MM» · PM.mm(105417, 'Bs') → «105.417 MM Bs»
  PM.mm = function (x, moneda) {
    if (x === null || x === undefined || isNaN(x)) return '—';
    return moneda === 'Bs' ? PM.auto(x) + NB + 'MM' + NB + 'Bs' : '$' + PM.auto(x) + NB + 'MM';
  };
  // Rótulo de eje: los decimales que el corte necesita, ni uno más. «0» · «0,5» · «120.000»
  PM.tick = function (v) {
    var a = Math.abs(v), dec = Math.abs(a - Math.round(a)) < 1e-9 ? 0 : Math.abs(a * 10 - Math.round(a * 10)) < 1e-9 ? 1 : 2;
    return PM.num(v, dec);
  };

  // ── Fechas ─────────────────────────────────────────────────────────────────
  PM.MES = ['ene', 'feb', 'mar', 'abr', 'may', 'jun', 'jul', 'ago', 'sep', 'oct', 'nov', 'dic'];
  PM.MES_L = ['enero', 'febrero', 'marzo', 'abril', 'mayo', 'junio', 'julio', 'agosto', 'septiembre', 'octubre', 'noviembre', 'diciembre'];
  var MES_IDX = { ene: 0, feb: 1, mar: 2, abr: 3, may: 4, jun: 5, jul: 6, ago: 7, sep: 8, set: 8, oct: 9, nov: 10, dic: 11 };
  var cachePer = {};
  // Cualquier clave de período → {a, m (0-11), d, s, t, f}; f: 'd' diaria · 's' semanal BCB ·
  // 'm' mensual · 't' trimestral · 'a' anual. Acepta «2026-08», «2026-09-S4», «2026-10-04»,
  // «2026-T2», «2026» y rótulos viejos como «Ene 2024».
  PM.periodo = function (k) {
    if (k && typeof k === 'object') return k;
    k = String(k).trim();
    if (cachePer[k]) return cachePer[k];
    var r, p = null;
    if ((r = /^(\d{4})-(\d{2})-(\d{2})/.exec(k))) p = { a: +r[1], m: +r[2] - 1, d: +r[3], f: 'd' };
    else if ((r = /^(\d{4})-(\d{2})-S(\d)$/i.exec(k))) p = { a: +r[1], m: +r[2] - 1, s: +r[3], f: 's' };
    else if ((r = /^(\d{4})-(\d{2})$/.exec(k))) p = { a: +r[1], m: +r[2] - 1, f: 'm' };
    else if ((r = /^(\d{4})-T(\d)$/i.exec(k))) p = { a: +r[1], m: (+r[2] - 1) * 3, t: +r[2], f: 't' };
    else if ((r = /^(\d{4})$/.exec(k))) p = { a: +r[1], m: 0, f: 'a' };
    else if ((r = /^([A-Za-zÁÉÍÓÚáéíóú]{3})[a-zé]*\.?\s+(\d{4})$/.exec(k)) && MES_IDX[r[1].toLowerCase()] != null)
      p = { a: +r[2], m: MES_IDX[r[1].toLowerCase()], f: 'm' };
    return (cachePer[k] = p);
  };
  PM.fecha = function (k, largo) {
    var p = PM.periodo(k);
    if (!p) return String(k);
    var M = largo ? PM.MES_L : PM.MES;
    switch (p.f) {
      case 'd': return largo ? p.d + ' de ' + M[p.m] + ' de ' + p.a : p.d + NB + M[p.m] + NB + p.a;
      case 's': return largo ? p.s + '.ª semana de ' + M[p.m] + ' de ' + p.a : 'sem' + NB + p.s + ' · ' + M[p.m] + NB + p.a;
      case 't': return 'T' + p.t + NB + p.a;
      case 'a': return String(p.a);
      default: return largo ? M[p.m] + ' de ' + p.a : M[p.m] + NB + p.a;
    }
  };
  // Índice desde el que arranca un rango: 'todo' · '5a' (últimos 5 años) · '6m' · 'desde2010'.
  PM.desde = function (claves, rango) {
    if (!rango || rango === 'todo' || !claves.length) return 0;
    var u = PM.periodo(claves[claves.length - 1]), corte = null, r;
    if ((r = /^(\d+)a$/i.exec(rango))) corte = (u.a - +r[1]) * 12 + u.m + 1;
    else if ((r = /^(\d+)m$/i.exec(rango))) corte = u.a * 12 + u.m - +r[1] + 1;
    else if ((r = /^desde(\d{4})$/i.exec(rango))) corte = +r[1] * 12;
    if (corte === null) return 0;
    for (var i = 0; i < claves.length; i++) {
      var p = PM.periodo(claves[i]);
      if (p.a * 12 + p.m >= corte) return i;
    }
    return 0;
  };

  // ── Eje de tiempo ──────────────────────────────────────────────────────────
  function inicioAnio(p) { return p.f === 'a' || (p.m === 0 && (p.f === 'm' || p.f === 't' || (p.f === 's' && p.s === 1) || (p.f === 'd' && p.d <= 4))); }
  function inicioMes(p) { return p.f === 'm' || p.f === 't' || p.f === 'a' || (p.f === 's' && p.s === 1) || (p.f === 'd' && p.d <= 3); }
  function rotulos(P, esq) {               // → [[índice, texto], …] para un esquema
    var out = [];
    for (var i = 0; i < P.length; i++) {
      var p = P[i], q = i ? P[i - 1] : null, t = null;
      if (!p) continue;
      if (esq.tipo === 'anio') {
        if ((q ? p.a !== q.a : inicioAnio(p)) && p.a % esq.paso === 0) t = String(p.a);
      } else if (esq.tipo === 'mes') {
        if ((q ? (p.a !== q.a || p.m !== q.m) : inicioMes(p)) && p.m % esq.paso === 0) t = p.m === 0 ? String(p.a) : PM.MES[p.m];
      } else if (esq.tipo === 'dia') {
        var anc = function (x) { var b = 0; esq.anclas.forEach(function (a) { if (x.d >= a) b = a; }); return b; };
        var cambia = q ? (p.a !== q.a || p.m !== q.m || anc(p) !== anc(q)) : esq.anclas.indexOf(p.d) >= 0;
        if (cambia && anc(p)) t = (p.m === 0 && anc(p) === 1) ? String(p.a) : p.d + ' ' + PM.MES[p.m];
      }
      if (t) out.push([i, t]);
    }
    return out;
  }
  // Marcas menores (sin rótulo) que acompañan a cada escala de rótulos
  function menores(esq) {
    if (esq.tipo === 'anio') return esq.paso >= 10 ? [{ tipo: 'anio', paso: 1 }, { tipo: 'anio', paso: 5 }]
      : esq.paso > 1 ? [{ tipo: 'anio', paso: 1 }] : [{ tipo: 'mes', paso: 3 }];
    if (esq.tipo === 'mes') return esq.paso === 6 ? [{ tipo: 'mes', paso: 3 }] : esq.paso === 3 ? [{ tipo: 'mes', paso: 1 }] : [];
    if (esq.tipo === 'dia') return esq.anclas.length === 2 ? [{ tipo: 'dia', anclas: [1, 8, 15, 22] }] : [];
    return [];
  }
  // De los rótulos candidatos de un esquema, los que entran. Se recorre de derecha a izquierda (lo reciente
  // manda). Si un rótulo choca con el de su derecha: en un tramo normal, el esquema no entra; en un tramo
  // COMPRIMIDO (la serie trae datos anuales o trimestrales antes de los mensuales y cada año ocupa una sola
  // casilla) el eje deja de rotular ahí, sin bajar la densidad de todo el resto.
  function elegir(C, px, car, aire) {
    if (C.length < 2) return C.slice();
    var d = [];
    for (var j = 1; j < C.length; j++) d.push(C[j][0] - C[j - 1][0]);
    var med = d.slice().sort(function (a, b) { return a - b; })[Math.floor(d.length / 2)];
    var hace = function (a, b) { return (a[1].length + b[1].length) * car / 2 + aire; };
    var puestos = [C[C.length - 1]];
    for (var i = C.length - 2; i >= 0; i--) {
      var der = puestos[puestos.length - 1];
      if ((der[0] - C[i][0]) * px >= hace(C[i], der)) { puestos.push(C[i]); continue; }
      // tramo comprimido (el comienzo anual o trimestral de la serie): de ahí hacia atrás, sin rótulos;
      // seguir poniendo dejaría un paso irregular («2002, 2006, 2008…»)
      if (C[i + 1][0] - C[i][0] < 0.85 * med) break;
      return null;
    }
    return puestos.reverse();
  }
  // Rótulos y marcas del eje de tiempo para un ancho dado. `util` es el ancho REAL del trazado si ya se midió
  // (PM.montar lo mide tras el primer dibujo); si no, se estima: lienzo − rótulos del eje Y − márgenes.
  PM.cortesTiempo = function (claves, ancho, conHueco, util) {
    var P = claves.map(PM.periodo), n = P.length;
    var res = { txt: {}, marcas: {}, n: 0 };
    if (!n || !P[0]) return res;
    var fs = PM.pequeno() ? 10 : 10.5, car = fs * 0.6 + 0.15;   // JetBrains Mono: 0,6 em por carácter
    util = util || Math.max(140, (ancho || 600) - 64);
    res.util = util;
    var px = util / Math.max(1, conHueco ? n : n - 1);
    var aire = PM.pequeno() ? 12 : 14;                          // aire entre dos rótulos (además del texto)
    var f = P[n - 1].f, esquemas = [];
    if (f === 'd') esquemas.push({ tipo: 'dia', anclas: [1, 8, 15, 22] }, { tipo: 'dia', anclas: [1, 15] });
    // con más de cinco años y medio a la vista bastan los años: los meses entre año y año sólo ensucian
    var meses = (P[n - 1].a - P[0].a) * 12 + P[n - 1].m - P[0].m;
    if (f !== 'a' && f !== 't' && meses <= 66) esquemas.push({ tipo: 'mes', paso: 1 }, { tipo: 'mes', paso: 3 }, { tipo: 'mes', paso: 6 });
    [1, 2, 5, 10, 20, 50].forEach(function (k) { esquemas.push({ tipo: 'anio', paso: k }); });
    var L = null, esq = null;
    for (var e = 0; e < esquemas.length && !L; e++) {
      var C = rotulos(P, esquemas[e]);
      if (!C.length) continue;
      var r = elegir(C, px, car, aire);
      if (r && r.length) { L = r; esq = esquemas[e]; }
    }
    if (!L) { esq = { tipo: 'anio', paso: 50 }; L = rotulos(P, esq); }
    // Si ningún rótulo nombra el año (rango corto sin enero), el primero lo lleva.
    if (L.length && !L.some(function (x) { return /^\d{4}$/.test(x[1]); })) L[0][1] += ' ' + P[L[0][0]].a;
    L.forEach(function (x) { res.txt[x[0]] = x[1]; res.marcas[x[0]] = true; });
    // Marcas menores: la escala más fina cuyo paso típico deje al menos 9 px; donde se aprietan (tramo
    // comprimido) se ralean a 7 px en vez de quitarlas de todo el eje.
    var cand = menores(esq);
    for (var m = 0; m < cand.length; m++) {
      var M = rotulos(P, cand[m]);
      if (M.length < 2) continue;
      var dm = [];
      for (var q = 1; q < M.length; q++) dm.push((M[q][0] - M[q - 1][0]) * px);
      if (dm.sort(function (a, b) { return a - b; })[Math.floor(dm.length / 2)] < 9) continue;
      var ult = Infinity;
      for (var q2 = M.length - 1; q2 >= 0; q2--) {
        if ((ult - M[q2][0]) * px >= 7 || res.txt[M[q2][0]] != null) { res.marcas[M[q2][0]] = true; ult = M[q2][0]; }
      }
      break;
    }
    res.n = L.length;
    var ancho = function (t) { return t.length * car; };
    var ult = L[L.length - 1], pri = L[0];
    res.der = ult && ult[0] === n - 1 ? Math.ceil(Math.max(0, ancho(ult[1]) / 2 - (conHueco ? px / 2 : 0))) + 3 : 0;
    res.izq = pri && pri[0] === 0 ? Math.max(0, Math.ceil(ancho(pri[1]) / 2 - (conHueco ? px / 2 : 0)) - 28) : 0;
    return res;
  };
  PM.ejeTiempo = function (claves, el, extra) {
    var conHueco = !!(extra && extra.boundaryGap), medido = el && el._util && el._util.ancho === el.clientWidth ? el._util.util : null;
    var c = PM.cortesTiempo(claves, el ? el.clientWidth : 600, conHueco, medido);
    var dk = PM.dk(), fs = PM.pequeno() ? 10 : 10.5, linea = dk ? '#3A4549' : '#C9CDCE';
    var letra = { fontFamily: PM.mono(), fontSize: fs, lineHeight: fs + 4 };
    return mezclar({
      type: 'category', data: claves, boundaryGap: false,
      axisLine: { onZero: false, lineStyle: { color: linea } },
      // con barras (boundaryGap) la marca va al centro de la barra, donde está el rótulo
      axisTick: { show: true, length: 4, alignWithLabel: conHueco, interval: function (i) { return !!c.marcas[i]; }, lineStyle: { color: linea } },
      axisLabel: {
        interval: function (i) { return c.txt[i] != null; }, margin: 9,
        // el año ancla la lectura: más oscuro y más firme que los meses
        // Donde no hay rótulo, un carácter invisible con la misma letra (no '' ): ECharts reserva el alto de los
        // rótulos midiendo sólo 1 de cada n/40 categorías, y si todas las medidas salen vacías reserva 0 px y los
        // años quedan dibujados debajo del lienzo (pasaba en «Todo» de Reservas, Emisión, Agregados, Monetización).
        // Sólo se dibujan los índices que aprueba `interval`: el invisible nunca llega a la pantalla.
        formatter: function (v, i) {
          var t = c.txt[i], r;
          if (t == null) return '{a|\u200b}';
          if (/^\d{4}$/.test(t)) return '{a|' + t + '}';
          if ((r = /^(.*) (\d{4})$/.exec(t))) return '{m|' + r[1] + ' }{a|' + r[2] + '}';
          return '{m|' + t + '}';
        },
        rich: {
          a: mezclar({ fontWeight: 600, color: dk ? 'rgba(226,232,240,.9)' : 'rgba(0,18,25,.8)' }, letra),
          m: mezclar({ fontWeight: 500, color: PM.var('--pizarra') }, letra)
        }
      },
      splitLine: { show: false },
      _bordes: { der: c.der, izq: c.izq },     // lo lee PM.montar para ensanchar el grid (y lo borra)
      _util: c.util                            // el ancho de trazado que usó: PM.montar lo compara con el real
    }, extra);
  };
  PM.ejeY = function (opc) {
    opc = opc || {};
    var dk = PM.dk(), s = PM.pequeno();
    var fmt = typeof opc.fmt === 'function' ? opc.fmt
      : opc.fmt === 'pct' ? function (v) { return PM.tick(v) + '%'; } : PM.tick;
    // Un tope fijo (100 en una composición) que no es múltiplo del corte deja dos marcas pegadas
    // («90%» y «100%»): con tope fijo y base cero, el corte divide el tope en partes exactas.
    var ex = opc.extra || {}, corte = {};
    if (typeof ex.max === 'number' && (ex.min == null || ex.min === 0) && ex.interval == null) corte.interval = ex.max / (s ? 4 : 5);
    return mezclar(mezclar({
      type: 'value', name: opc.unidad || '', nameLocation: 'end', nameGap: 12,
      nameTextStyle: { align: 'right', color: PM.var('--pizarra'), fontFamily: PM.inter(), fontSize: 10, fontWeight: 600 },
      splitNumber: s ? 4 : 5, axisLine: { show: false }, axisTick: { show: false },
      splitLine: { lineStyle: { color: dk ? '#2A3A50' : '#E2DDD3', opacity: dk ? 0.6 : 0.75 } },
      axisLabel: { color: PM.var('--pizarra'), fontFamily: PM.mono(), fontSize: s ? 10 : 10.5, fontWeight: 500, margin: 8, formatter: fmt }
    }, corte), opc.extra);
  };
  PM.grid = function (extra) {
    var s = PM.pequeno();
    return mezclar({ left: s ? 10 : 8, right: s ? 14 : 10, top: 28, bottom: 2, containLabel: true }, extra);
  };
  // Línea del cero, más firme que la grilla (para series que cruzan el cero)
  PM.cero = function () {
    return { silent: true, symbol: 'none', animation: false, label: { show: false },
      lineStyle: { color: PM.dk() ? 'rgba(226,232,240,.5)' : 'rgba(0,18,25,.45)', width: 1, type: 'solid' }, data: [{ yAxis: 0 }] };
  };
  // Serie de línea con el punto que aparece bajo el cursor (anillo del color de la tarjeta)
  PM.linea = function (color, o) {
    o = o || {};
    var c = PM.col(color);
    return mezclar({
      type: 'line', symbol: 'circle', showSymbol: false, symbolSize: o.punto || 7,
      lineStyle: { width: o.ancho || 2, color: c, type: o.punteada ? 'dashed' : 'solid', cap: 'round', join: 'round' },
      itemStyle: { color: c, borderColor: PM.var('--card') || '#fff', borderWidth: 1.5 },
      emphasis: { focus: 'series' }
    }, o.extra);
  };
  // Serie de barras (apiladas o no). Sin borde entre segmentos (regla 7 de la marca).
  // ⛔ Con más de ~90 barras el antialiasing de los bordes fraccionarios deja costuras claras y un
  // borde del mismo color NO las tapa (ECharts encoge la barra para meterlo): usar `large:true,
  // largeThreshold:1`. El modo large no apila por signo: separar cada serie en parte positiva y
  // negativa (dos pilas) con barGap:'-100%' (ver pasivo_bcb y determinantes_base).
  PM.barra = function (color, o) {
    o = o || {};
    return mezclar({ type: 'bar', itemStyle: { color: PM.col(color) }, emphasis: { focus: 'series' } }, o.extra);
  };
  // Puntero del tooltip para barras: una franja tenue en lugar de la línea
  PM.sombraEje = function () {
    return { type: 'shadow', shadowStyle: { color: PM.dk() ? 'rgba(226,232,240,.06)' : 'rgba(0,18,25,.05)' } };
  };
  // Serie que se reinicia cada enero (variación acumulada en el año): sin esto, la línea traza una
  // caída vertical de diciembre a enero que no es un dato. Devuelve dos series del mismo nombre, una
  // con los años pares y otra con los impares (null corta la línea); la segunda lleva `_dup` para que
  // el tooltip y la descarga no la repitan.
  PM.porAnio = function (serie, claves) {
    var P = claves.map(PM.periodo), n = serie.data.length - 1;
    var par = function (i) { return P[i] && P[i].a % 2 === 0; };
    var a = mezclar({}, serie), b = mezclar({}, serie);
    a.data = serie.data.map(function (v, i) { return par(i) ? v : null; });
    b.data = serie.data.map(function (v, i) { return par(i) ? null : v; });
    b._dup = true;
    a.connectNulls = false; b.connectNulls = false;   // el null entre años es el corte: nunca unirlo
    if (serie.markPoint) { if (par(n)) delete b.markPoint; else delete a.markPoint; }
    if (serie.areaStyle) { a.areaStyle = serie.areaStyle; b.areaStyle = serie.areaStyle; }
    return [a, b];
  };
  // Último dato: punto con anillo
  // Halo translúcido del color de la serie y no anillo del color de la tarjeta: con ~100 puntos cada
  // tramo mide ~6 px y un anillo opaco tapaba el último (la línea parecía terminar un mes antes).
  PM.puntoFinal = function (color, n, v) {
    return { symbol: 'circle', symbolSize: 7, silent: true, animation: false, data: [{ coord: [n, v] }], label: { show: false },
      itemStyle: { color: PM.col(color), borderColor: PM.rgba(color, PM.dk() ? 0.32 : 0.24), borderWidth: 6 } };
  };

  // ── Tooltip: vidrio oscuro con filete del acento ───────────────────────────
  // En el teléfono va arriba del lienzo y sigue al dedo en horizontal: no tapa lo que se toca.
  function arriba(pt, params, dom, rect, size) {
    var w = size.contentSize[0], W = size.viewSize[0];
    return [Math.min(Math.max(pt[0] - w / 2, 4), Math.max(4, W - w - 4)), 4];
  }
  PM.tooltip = function (formatter, extra) {
    var s = PM.pequeno(), dk = PM.dk();
    var t = {
      trigger: 'axis', confine: true, className: 'tt', transitionDuration: 0.14, enterable: false,
      axisPointer: { type: 'line', lineStyle: { color: dk ? 'rgba(226,232,240,.32)' : 'rgba(0,18,25,.22)', width: 1 } },
      // en oscuro, un filo de 1 px separa el vidrio de la tarjeta (en claro lo separa la sombra)
      backgroundColor: 'rgba(0,18,25,.9)', borderWidth: dk ? 1 : 0, borderColor: 'rgba(255,255,255,.1)',
      padding: s ? [8, 10, 9] : [10, 13, 11],
      extraCssText: 'border-radius:' + (PM.var('--r-s') || '0px') + ';border-top:2px solid var(--acento);' +
        'box-shadow:0 14px 34px -10px rgba(0,18,25,.55),0 2px 8px rgba(0,18,25,.16);min-width:' + (s ? 150 : 176) + 'px;',
      textStyle: { fontFamily: 'Inter', fontSize: s ? 11 : 12, color: '#fff' },
      formatter: formatter
    };
    if (s) t.position = arriba;
    return mezclar(t, extra);
  };
  PM.ttTitulo = function (t, sub) {
    return '<div class="tt-t">' + t + '</div>' + (sub ? '<div class="tt-s">' + sub + '</div>' : '') + '<div class="tt-hr"></div>';
  };
  // forma: 'linea' · 'punteada' · 'area' (la clave imita la marca del gráfico)
  // color null: fila sin clave (todas las filas describen la misma marca: dispersiones, mapas)
  PM.ttFila = function (color, nombre, valor, forma) {
    if (!color) return '<div class="tt-f"><span class="tt-n">' + nombre + '</span><span class="tt-v" style="color:#fff">' + valor + '</span></div>';
    var c = PM.col(color);
    if (/^#[0-9a-f]{6}$/i.test(c) && lum(c) < 0.035) c = '#E2E8F0';   // el tooltip es oscuro en los dos temas: la tinta no se vería
    var k = forma === 'area' ? 'width:9px;height:9px;box-shadow:inset 0 0 0 1px rgba(255,255,255,.18);background:' + c
      : 'width:12px;height:0;border-top:2.5px ' + (forma === 'punteada' ? 'dashed' : 'solid') + ' ' + c;
    return '<div class="tt-f"><span class="tt-k" style="' + k + '"></span><span class="tt-n">' + nombre + '</span>' +
      '<span class="tt-v" style="color:' + PM.txTip(color) + '">' + valor + '</span></div>';
  };
  PM.ttTotal = function (nombre, valor) {
    return '<div class="tt-tot"><span class="tt-n">' + nombre + '</span><span class="tt-v">' + valor + '</span></div>';
  };
  PM.ttPie = function (html) { return '<div class="tt-pie">' + html + '</div>'; };

  // ── Fuentes del lienzo ─────────────────────────────────────────────────────
  // ECharts mide los rótulos con la fuente disponible AL DIBUJAR: si JetBrains Mono llega
  // después, el texto real es más ancho que el medido y el eje Y se corta («18.000» → «8.000»).
  // document.fonts.ready no alcanza: el navegador no pide una fuente hasta que el DOM la usa
  // (el lienzo no cuenta), así que se piden explícitamente. Resuelve true si hubo que esperar.
  var tarde = false, fuentesYa = !document.fonts || !document.fonts.load;
  PM.mono = function () { return tarde ? '"JetBrains Mono", monospace' : 'JetBrains Mono'; };
  PM.inter = function () { return tarde ? 'Inter, sans-serif' : 'Inter'; };
  PM.fuentes = (function () {
    if (!document.fonts || !document.fonts.load) return Promise.resolve(false);
    var pedidas = ['500 11px "JetBrains Mono"', '600 11px "JetBrains Mono"', '700 11px "JetBrains Mono"', '500 11px "Inter"', '600 11px "Inter"'];
    var faltaba = pedidas.some(function (f) { try { return !document.fonts.check(f); } catch (e) { return true; } });
    return Promise.all(pedidas.map(function (f) { return document.fonts.load(f).catch(function () { return []; }); }))
      .then(function () { fuentesYa = true; return faltaba; });
  })();

  // ── Montaje: el gráfico se redibuja con el tema y con el ANCHO (los cortes del eje dependen de él)
  // redibujar() (un botón) anima; el tema, el ancho y la llegada de fuentes redibujan quietos.
  var graficos = [];
  PM.montar = function (el, opciones) {
    var chart = echarts.init(el);
    var ancho = el.clientWidth, t = 0, primera = true, arrancado = false;
    var pintar = function (quieto) {
      if (!arrancado) return;                     // todavía esperando las fuentes
      // El eje de tiempo elige sus rótulos con el ancho del trazado; la primera vez lo estima. Si el trazado
      // real (que depende de los rótulos del eje Y) difiere, se vuelve a armar con el real: una vuelta más.
      for (var vuelta = 0; vuelta < 2; vuelta++) {
        var o = opciones(), ejes = [].concat(o.xAxis || []), x = ejes[0], usado = null;
        ejes.forEach(function (a) { if (a && a._util != null) { if (usado == null) usado = a._util; delete a._util; } });
        if (x && x._bordes) {
          if (o.grid && !Array.isArray(o.grid)) {
            if (typeof o.grid.right === 'number') o.grid.right = Math.max(o.grid.right, x._bordes.der);
            if (typeof o.grid.left === 'number') o.grid.left = Math.max(o.grid.left, x._bordes.izq);
          }
          delete x._bordes;
        }
        if (quieto) o.animation = false;
        else if (o.animationDuration == null) { o.animationDuration = primera ? 800 : 480; o.animationEasing = 'cubicOut'; }
        chart.setOption(o, true);
        if (usado == null || vuelta) break;
        var eje = chart.getModel().getComponent('xAxis', 0), ext = eje && eje.axis && eje.axis.getExtent();
        var real = ext ? Math.abs(ext[1] - ext[0]) : 0;
        if (!real || Math.abs(real - usado) <= 4) break;
        el._util = { ancho: el.clientWidth, util: real };
      }
      primera = false;
      PM.programarAlto();
    };
    PM.alCambiarTema(function () { pintar(true); });
    var alCambiarTamano = function () {
      clearTimeout(t);
      t = setTimeout(function () {
        chart.resize();
        if (Math.abs(el.clientWidth - ancho) > 1) { ancho = el.clientWidth; pintar(true); }
      }, 80);
    };
    if (window.ResizeObserver) new ResizeObserver(alCambiarTamano).observe(el);
    else window.addEventListener('resize', alCambiarTamano);
    var arrancar = function () { if (arrancado) return; arrancado = true; pintar(false); };
    // El primer dibujo espera dos cosas: las fuentes (tope 1,5 s) y que el lienzo entre en pantalla,
    // así el trazo se ve dibujarse. opciones() corre igual en el acto: hay gráficos que calculan ahí
    // sus claves y llaman al panel enseguida (IPP/IPM quedaba con el panel vacío).
    var conFuentes = fuentesYa, reales = fuentesYa, sinFuentes = false, visto = !window.IntersectionObserver;
    var intentar = function () { if (conFuentes && visto && !arrancado) { sinFuentes = !reales; arrancar(); } };
    opciones();
    if (!visto) {
      var vigia = new IntersectionObserver(function (es) {
        if (!es.some(function (x) { return x.isIntersecting; })) return;
        vigia.disconnect(); visto = true; intentar();
      }, { threshold: 0.12 });
      vigia.observe(el);
      // red de seguridad: un lienzo que nunca «se ve» (impresión, pestaña oculta) igual se dibuja
      setTimeout(function () { visto = true; conFuentes = true; intentar(); }, 12000);
      window.addEventListener('beforeprint', function () { visto = true; conFuentes = true; intentar(); });
    }
    if (!conFuentes) {
      var tope = setTimeout(function () { conFuentes = true; intentar(); }, 1500);
      PM.fuentes.then(function () {
        clearTimeout(tope); reales = true; conFuentes = true;
        if (arrancado && sinFuentes) { tarde = true; pintar(true); }    // dibujó con la fuente de reemplazo: otra cadena, otra medida
        else intentar();
      });
    } else intentar();
    graficos.push({ chart: chart, el: el, pintar: pintar });
    ponerDescargas();
    return { chart: chart, redibujar: function () { pintar(false); } };
  };

  // ── Datos ──────────────────────────────────────────────────────────────────
  PM.cargar = function (nombres) {
    var v = new Date().toISOString().slice(0, 10);
    return Promise.all(nombres.map(function (n) {
      return fetch('../data/' + n + '?v=' + v).then(function (r) {
        if (!r.ok) throw new Error(n + ': ' + r.status);
        return r.json();
      });
    }));
  };
  PM.error = function (el, txt) {
    if (el) el.innerHTML = '<div class="loading"><span style="color:#C71E1D">' + (txt || 'No se pudieron cargar los datos') + '</span></div>';
  };

  // ── Botonera: el activo es un indicador que se desliza de un tramo a otro ──
  PM.botonera = function (el, opciones, activo, alCambiar) {
    el.innerHTML = '<span class="hz-thumb" aria-hidden="true"></span>' + opciones.map(function (o) {
      return '<button type="button" class="hz-btn' + (o[0] === activo ? ' active' : '') + '" data-v="' + o[0] + '" aria-pressed="' + (o[0] === activo) + '">' + o[1] + '</button>';
    }).join('');
    var thumb = el.querySelector('.hz-thumb');
    var mover = function (quieto) {
      var b = el.querySelector('.hz-btn.active');
      if (!b || !b.offsetWidth) return;
      if (quieto) el.classList.add('quieto');
      thumb.style.width = b.offsetWidth + 'px';
      thumb.style.transform = 'translateX(' + b.offsetLeft + 'px)';
      el.classList.add('listo');
      if (quieto) { void thumb.offsetWidth; el.classList.remove('quieto'); }
    };
    el.querySelectorAll('.hz-btn').forEach(function (b) {
      b.addEventListener('click', function () {
        if (b.classList.contains('active')) return;
        el.querySelectorAll('.hz-btn').forEach(function (x) { x.classList.toggle('active', x === b); x.setAttribute('aria-pressed', x === b); });
        mover(false);
        alCambiar(b.dataset.v);
      });
    });
    if (window.ResizeObserver) new ResizeObserver(function () { mover(true); }).observe(el);
    setTimeout(function () { mover(true); }, 0);
    PM.fuentes.then(function () { mover(true); });
  };
  // Pastillas de serie (leyenda que filtra) · series: [{k, nombre, color, forma:'linea'|'punteada'|'area'}]
  PM.pastillas = function (el, series, activas, alCambiar) {
    el.innerHTML = '';
    series.forEach(function (s) {
      var b = document.createElement('button');
      b.type = 'button'; b.className = 'pill'; b.dataset.k = s.k;
      b.innerHTML = '<span class="lp' + (s.forma === 'punteada' ? ' dashed' : s.forma === 'area' ? ' area' : '') + '"></span>' + s.nombre;
      b.addEventListener('click', function () {
        if (activas.has(s.k)) { if (activas.size === 1) return; activas.delete(s.k); } else activas.add(s.k);
        pintar(); alCambiar();
      });
      el.appendChild(b);
    });
    function pintar() {
      var dk = PM.dk();
      el.querySelectorAll('.pill').forEach(function (b) {
        var s = series.filter(function (x) { return x.k === b.dataset.k; })[0];
        b.style.setProperty('--pc', PM.col(s.color));
        b.style.setProperty('--pf', PM.rgba(s.color, dk ? 0.16 : 0.09));
        b.style.setProperty('--pb', PM.rgba(s.color, dk ? 0.6 : 0.45));
        b.setAttribute('aria-pressed', activas.has(s.k));
      });
    }
    pintar();
    PM.alCambiarTema(pintar);
  };
  // Minilínea: los últimos valores de la serie, sin ejes, con un relleno tenue y el último punto.
  // viewBox fijo y preserveAspectRatio:none (se estira con la tarjeta); non-scaling-stroke mantiene el
  // grosor y el punto final es un trazo de largo cero con remate redondo: no se ovala al estirarse.
  PM.mini = function (serie, color) {
    var v = (serie || []).map(function (x) { return x == null || isNaN(x) ? null : +x; });
    var ok = v.filter(function (x) { return x != null; });
    if (ok.length < 3) return '';
    var lo = Math.min.apply(null, ok), hi = Math.max.apply(null, ok), r = hi - lo || 1, n = v.length - 1;
    // un dato faltante corta el trazo (como en el gráfico); el relleno sólo si la serie no tiene huecos
    var W = 100, H = 30, pts = [], d = '', a = '', ultimo = null, corte = true, huecos = false;
    v.forEach(function (x, i) {
      if (x == null) { corte = true; if (pts.length) huecos = true; return; }
      var X = +(i / n * W).toFixed(2), Y = +(H - 3 - (x - lo) / r * (H - 6)).toFixed(2);
      d += (corte ? 'M' : 'L') + X + ' ' + Y; corte = false;
      pts.push([X, Y]); ultimo = [X, Y];
    });
    a = huecos ? '' : d + 'L' + pts[pts.length - 1][0] + ' ' + H + 'L' + pts[0][0] + ' ' + H + 'Z';
    var c = PM.col(color);
    return '<svg class="kpi-sp" viewBox="0 0 ' + W + ' ' + H + '" preserveAspectRatio="none" aria-hidden="true">' +
      (a ? '<path class="sp-a" d="' + a + '" fill="' + c + '" fill-opacity="' + (PM.dk() ? 0.16 : 0.1) + '"/>' : '') +
      '<path d="' + d + '" stroke="' + c + '" stroke-width="1.5"/>' +
      '<path d="M' + ultimo[0] + ' ' + ultimo[1] + 'Z" stroke="' + c + '" stroke-width="4.5"/></svg>';
  };
  // Cifra: {color, rotulo, valor, delta, tono:'bueno'|'malo'|'neutro', serie:[…] (opcional)}
  PM.kpi = function (o) {
    return '<div class="kpi" style="--kc:' + PM.col(o.color) + '"><div class="kpi-lbl">' + o.rotulo + '</div>' +
      '<div class="kpi-val" style="color:' + PM.tx(o.color) + '">' + o.valor + '</div>' +
      (o.delta ? '<div class="kpi-d ' + (o.tono || 'neutro') + '">' + o.delta + '</div>' : '') +
      (o.serie ? PM.mini(o.serie, o.color) : '') + '</div>';
  };
  PM.ultimos = function (arr, n) { return arr.slice(Math.max(0, arr.length - (n || 24))); };
  PM.flecha = function (d, umbral) { umbral = umbral || 0; return d > umbral ? '▲' : d < -umbral ? '▼' : '—'; };
  PM.pb = function (color, rotulo, valor, desc) {
    return '<div class="pb"><div class="pb-lbl" style="color:' + PM.tx(color) + ';background:' + PM.rgba(color, PM.dk() ? 0.16 : 0.08) +
      ';border-left-color:' + PM.col(color) + '">' + rotulo + '</div>' +
      (valor != null ? '<div class="pb-val" style="color:' + PM.tx(color) + '">' + valor + '</div>' : '') +
      (desc ? '<p class="pb-desc">' + desc + '</p>' : '') + '</div>';
  };
  PM.ctx = function (html) { return '<div class="ctx"><p>' + html + '</p></div>'; };
  PM.dt = function (color, txt) {
    return '<span class="dt" style="color:' + PM.tx(color) + ';background:' + PM.rgba(color, PM.dk() ? 0.16 : 0.08) + '">' + txt + '</span>';
  };
  var FLECHA = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><polyline points="6 9 12 15 18 9"/></svg>';
  // lista: [[pregunta, respuesta-html], …]
  PM.preguntas = function (el, lista) {
    el.innerHTML = lista.map(function (p, i) {
      var id = (el.id || 'edu') + '-' + i;
      return '<div class="edu"><button type="button" class="edu-t" aria-expanded="false" aria-controls="' + id + '">' + p[0] + FLECHA + '</button>' +
        '<div class="edu-b" id="' + id + '"><div><div class="edu-body">' + p[1] + '</div></div></div></div>';
    }).join('');
    el.querySelectorAll('.edu-t').forEach(function (b) {
      b.addEventListener('click', function () {
        var e = b.parentNode, abierta = e.classList.toggle('open');
        b.setAttribute('aria-expanded', abierta);
        PM.programarAlto();
        setTimeout(PM.programarAlto, 340);          // después de la transición de 0,3 s
      });
    });
  };

  function vigilarPaneles() {
    document.querySelectorAll('.panel-body').forEach(function (pb) {
      if (pb.dataset.vigilado) return;
      pb.dataset.vigilado = '1';
      var revisar = function () {
        var sobra = pb.scrollHeight - pb.clientHeight > 4, alFinal = pb.scrollTop + pb.clientHeight >= pb.scrollHeight - 4;
        pb.classList.toggle('desborda', sobra && !alFinal);
      };
      pb.addEventListener('scroll', revisar, { passive: true });
      new MutationObserver(revisar).observe(pb, { childList: true, subtree: true, characterData: true });
      if (window.ResizeObserver) new ResizeObserver(revisar).observe(pb);
      revisar();
    });
  }
  PM.vigilarPaneles = vigilarPaneles;

  // ── Cifras que cuentan hasta su valor la primera vez que se ven ────────────────────────────
  var NUM = /([+−-]?)(\d{1,3}(?:\.\d{3})+|\d+)(?:,(\d+))?/;
  function contar(el) {
    if (el.children.length || el.dataset.contado) return;
    var t = el.textContent, m = NUM.exec(t);
    el.dataset.contado = '1';
    if (!m) return;
    var dec = m[3] ? m[3].length : 0, v = parseFloat(m[2].replace(/\./g, '') + '.' + (m[3] || '0'));
    if (m[1] === '−' || m[1] === '-') v = -v;
    var antes = t.slice(0, m.index), despues = t.slice(m.index + m[0].length), mas = m[1] === '+';
    var f = new Intl.NumberFormat('es-BO', { minimumFractionDigits: dec, maximumFractionDigits: dec }), t0 = null, dur = 900;
    var paso = function (ts) {
      if (t0 === null) t0 = ts;
      var k = Math.min(1, (ts - t0) / dur), e = 1 - Math.pow(1 - k, 3), x = v * e;
      var s = f.format(Math.abs(x));
      el.textContent = antes + (x < 0 ? '−' : mas ? '+' : '') + s + despues;
      if (k < 1 && el.isConnected) requestAnimationFrame(paso); else if (el.isConnected) el.textContent = t;
    };
    requestAnimationFrame(paso);
  }
  function cifrasVivas() {
    var g = document.querySelector('.kpi-grid');
    if (!g || g.dataset.vigilada || !window.IntersectionObserver) return;
    if (window.matchMedia && matchMedia('(prefers-reduced-motion: reduce)').matches) return;
    g.dataset.vigilada = '1';
    var visto = false, hecho = false;
    var correr = function () {
      if (hecho || !visto) return;
      var vals = g.querySelectorAll('.kpi-val');
      if (!vals.length) return;
      hecho = true; vals.forEach(contar);
    };
    new MutationObserver(correr).observe(g, { childList: true });
    var io = new IntersectionObserver(function (es) {
      if (!es.some(function (x) { return x.isIntersecting; })) return;
      io.disconnect(); visto = true; correr();
    }, { threshold: 0.35 });
    io.observe(g);
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', cifrasVivas);
  else setTimeout(cifrasVivas, 0);

  // ── Descargas: imagen (título, leyenda, gráfico, fuente) y datos en CSV ────────────────────
  var ICONO = '<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M8 1.5v9M4.5 7 8 10.5 11.5 7M2 12.5v2h12v-2" fill="none" stroke="currentColor" stroke-width="1.6"/></svg>';
  // sólo espacios comunes: \s también se come el espacio duro de «sem 4» y «ago 2026»
  function texto(sel) { var e = document.querySelector(sel); return e ? e.textContent.replace(/[ \t\n\r]+/g, ' ').trim() : ''; }
  function slug(t) { return (t || 'grafico').toLowerCase().normalize('NFD').replace(/[\u0300-\u036f]/g, '').replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, ''); }
  function bajar(nombre, blob) {
    var a = document.createElement('a'); a.href = URL.createObjectURL(blob); a.download = nombre;
    document.body.appendChild(a); a.click(); setTimeout(function () { URL.revokeObjectURL(a.href); a.remove(); }, 1500);
  }
  function colorSerie(s) {
    var c = (s.itemStyle && s.itemStyle.color) || (s.lineStyle && s.lineStyle.color) || s.color;
    return typeof c === 'string' ? c : '#8A9699';
  }
  function seriesVisibles(opt) {
    var vistas = {}, out = [], ocultas = {};
    document.querySelectorAll('.pill').forEach(function (b) {
      if (b.offsetParent === null || b.style.display === 'none') ocultas[b.textContent.trim()] = 1;
    });
    (opt.series || []).forEach(function (s) {
      if (!s.name || s.name.charAt(0) === '_' || s._dup || vistas[s.name] || ocultas[s.name]) return;
      var algo = (s.data || []).some(function (v) {
        var n = v == null ? null : typeof v === 'object' ? (Array.isArray(v) ? v[v.length - 1] : v.value) : v;
        return n != null && n !== '-' && Math.abs(+n) > 0;
      });
      if (!algo) return;
      vistas[s.name] = 1; out.push(s);
    });
    // modo Foco: tres o más series del mismo color son contexto → una sola entrada
    var porColor = {};
    out.forEach(function (s) { var c = String(colorSerie(s)).toUpperCase(); (porColor[c] = porColor[c] || []).push(s); });
    var final = [], puesto = {};
    out.forEach(function (s) {
      var c = String(colorSerie(s)).toUpperCase(), grupo = porColor[c];
      if (grupo.length < 3) { final.push(s); return; }
      if (puesto[c]) return;
      puesto[c] = 1; final.push({ name: PM.leyendaResto || 'Otros', itemStyle: { color: colorSerie(s) } });
    });
    return final;
  }
  function envolver(ctx, t, ancho) {
    var palabras = t.split(' '), lineas = [], l = '';
    palabras.forEach(function (w) { var p = l ? l + ' ' + w : w; if (ctx.measureText(p).width > ancho && l) { lineas.push(l); l = w; } else l = p; });
    if (l) lineas.push(l);
    return lineas;
  }
  // Firma OFICIAL, la misma de las láminas del Banco de Gráficos (populi_style._wordmark_img):
  // «P» en Playfair regular roja + «opuli» en Playfair itálica tinta, a 40/64 del tamaño de la P y
  // sobre la misma línea base (opuli arranca 5/64 antes del final de la P); encima, la regla roja del
  // pie, que va del borde izquierdo de la firma al margen derecho. Abajo a la derecha.
  var PLAYFAIR_400 = 'https://fonts.googleapis.com/css2?family=Playfair+Display:ital,wght@0,400;1,400&display=swap';
  function fuentesFirma() {
    if (!document.querySelector('link[data-firma]')) {
      var l = document.createElement('link'); l.rel = 'stylesheet'; l.href = PLAYFAIR_400; l.setAttribute('data-firma', '1');
      document.head.appendChild(l);
    }
    if (!document.fonts || !document.fonts.load) return Promise.resolve();
    var cargar = function () {
      return Promise.all(['400 64px "Playfair Display"', 'italic 400 40px "Playfair Display"', '700 24px "Playfair Display"', '500 13px Inter', '600 12px Inter']
        .map(function (f) { return document.fonts.load(f).catch(function () { return []; }); }));
    };
    // la hoja de estilos recién agregada tarda en declarar las fuentes: se reintenta y hay tope
    return Promise.race([
      new Promise(function (ok) { setTimeout(function () { cargar().then(function () { setTimeout(function () { cargar().then(ok); }, 250); }); }, 60); }),
      new Promise(function (ok) { setTimeout(ok, 2500); })
    ]);
  }
  function firma(c, xDer, yBase, h, dk) {
    var s = h / 64, tP = '400 ' + (64 * s) + 'px "Playfair Display", Georgia, serif', tR = 'italic 400 ' + (40 * s) + 'px "Playfair Display", Georgia, serif';
    c.textAlign = 'left'; c.textBaseline = 'alphabetic';
    c.font = tP; var wP = c.measureText('P').width;
    c.font = tR; var wR = c.measureText('opuli').width;
    var ancho = wP + wR + 8 * s, x = xDer - ancho, rojo = dk ? '#E8706B' : '#C71E1D';
    c.font = tP; c.fillStyle = rojo; c.fillText('P', x, yBase);
    c.font = tR; c.fillStyle = dk ? '#F5EFE0' : '#001219'; c.fillText('opuli', x + wP - 5 * s, yBase);
    var arriba = yBase - 64 * s, grueso = Math.max(2, 2.4 * s * 64 / 52);
    c.fillStyle = rojo; c.fillRect(x, arriba - 7 * h / 52 - grueso, xDer - x, grueso);   // 7 px sobre la firma de 52 px, como el Banco
    return ancho;
  }
  // Imagen de descarga de TAMAÑO FIJO, la misma desde el teléfono que desde el PC (en el teléfono el lienzo es
  // angosto y la imagen salía alta y apretada): 2160 px de ancho, 800 lógicos a 2,7× (el ancho de las láminas
  // del Banco a doble resolución), y el alto de una proporción fija: 5:4 por defecto. La página puede pedir otra
  // (PM.imagen = { proporcion: 1 }) o el aspecto de su lienzo (PM.imagen = { aspecto: 2.2 }: el mapa). Si la
  // leyenda es tan larga que el trazado quedaría más ancho que 2:1, pasa a la proporción siguiente más alta
  // (5:4 → 1:1 → 4:5). El gráfico se vuelve a dibujar fuera de pantalla en ese tamaño, con formato de PC.
  var IMG_ANCHO = 800, IMG_PR = 2.7, PROPORCIONES = [16 / 9, 4 / 3, 5 / 4, 1, 4 / 5];
  // El gráfico se dibuja fuera de pantalla en el tamaño de la imagen y se captura cuando TERMINA de dibujarse
  // (evento «finished»): las etiquetas al final de la línea y el reacomodo de rótulos se ubican un cuadro después.
  // Mientras tanto, en su lugar queda una foto del gráfico tal como se ve, ya decodificada: no parpadea ni salta.
  function lienzoFijo(g, ancho, alto, pr, fondo) {
    var el = g.el, ch = g.chart, r = el.getBoundingClientRect(), foto = new Image();
    foto.src = ch.getDataURL({ type: 'png', pixelRatio: window.devicePixelRatio || 1, backgroundColor: fondo });
    foto.alt = '';
    foto.style.cssText = 'display:block;width:' + r.width + 'px;height:' + r.height + 'px';
    var decodificada = foto.decode ? foto.decode().catch(function () {}) : Promise.resolve();
    return decodificada.then(function () { return new Promise(function (ok) {
      var estilo = el.getAttribute('style'), listo = false, url = null;
      var terminar = function () {
        if (listo) return;
        listo = true; ch.off('finished', alTerminar);
        try { url = ch.getDataURL({ type: 'png', pixelRatio: pr, backgroundColor: fondo, excludeComponents: ['toolbox'] }); }
        finally {
          exportando = false;
          if (estilo == null) el.removeAttribute('style'); else el.setAttribute('style', estilo);
          if (foto.parentNode) foto.parentNode.removeChild(foto);
          ch.resize({ width: 'auto', height: 'auto' });   // sin 'auto', ECharts se queda con el tamaño explícito
          ch.clear(); g.pintar(true);
        }
        ok(url);
      };
      // fuera del despacho del evento: ECharts ignora resize y setOption mientras despacha «finished»
      var alTerminar = function () { setTimeout(terminar, 0); };
      el.parentNode.insertBefore(foto, el);
      el.style.position = 'fixed'; el.style.left = '-40000px'; el.style.top = '0';
      el.style.width = ancho + 'px'; el.style.height = alto + 'px';
      exportando = true;
      try {
        ch.resize({ width: ancho, height: alto, animation: { duration: 0 } });
        ch.on('finished', alTerminar);
        // de cero: al reutilizar la vista de una línea con otro tamaño, ECharts no reubica su etiqueta final
        ch.clear(); g.pintar(true);
      } catch (e) { terminar(); return; }
      setTimeout(terminar, 900);   // red de seguridad: si «finished» no llega, se captura igual
    }); });
  }
  PM.descargarImagen = function () {
    var g = graficos[0]; if (!g) return Promise.resolve();
    var ch = g.chart, dk = PM.dk(), pr = IMG_PR, card = PM.var('--card') || '#fff';
    var tinta = dk ? '#E2E8F0' : '#001219', gris = dk ? '#8A9699' : '#5C6B70', acento = PM.var('--acento') || '#C71E1D';
    return fuentesFirma().then(function () { return new Promise(function (ok) {
      var lado = 20 * pr, W = IMG_ANCHO * pr, m = 28 * pr, cv = document.createElement('canvas'), c = cv.getContext('2d');
      var titulo = texto('.sec-title'), sub = texto('.sec-sub'), fuente = texto('.source-txt');
      // la página puede declarar su leyenda (dispersiones, mapas, radares: sin series con nombre)
      var opt = ch.getOption(), ley = PM.leyendaImagen ? PM.leyendaImagen() : seriesVisibles(opt), hFirma = Math.round(W * 0.048);   // la firma del Banco mide 52 px en láminas de 1080
      c.font = '700 ' + 24 * pr + 'px "Playfair Display", Georgia, serif';
      var lTit = envolver(c, titulo, W - 2 * m - 14 * pr);
      c.font = '500 ' + 13 * pr + 'px Inter, sans-serif';
      var lSub = envolver(c, sub, W - 2 * m);
      // la fuente cede a la firma el ancho de la derecha, como en las láminas del Banco
      c.font = '500 ' + 11 * pr + 'px Inter, sans-serif';
      var anchoFirma = hFirma * 1.62, lFue = envolver(c, fuente, W - 2 * m - anchoFirma - 18 * pr);
      // leyenda en filas: cada ítem = clave + nombre; salta de fila antes del margen derecho
      c.font = '600 ' + 12 * pr + 'px Inter, sans-serif';
      var filas = [[]], xf = m;
      ley.forEach(function (s) {
        var w = 18 * pr + c.measureText(s.name).width + 18 * pr;
        if (xf + w - 18 * pr > W - m && filas[filas.length - 1].length) { filas.push([]); xf = m; }
        filas[filas.length - 1].push({ s: s, x: xf }); xf += w;
      });
      var hTit = lTit.length * 30 * pr, hSub = lSub.length * 18 * pr, hLey = ley.length ? filas.length * 22 * pr + 4 * pr : 0;
      var hPie = Math.max(lFue.length * 15 * pr, hFirma + 14 * pr) + 20 * pr;
      // tamaño fijo: el trazado se lleva lo que deja la proporción (en píxeles lógicos enteros)
      var arriba = m + hTit + hSub + 10 * pr + hLey, abajo = 14 * pr + hPie, anchoL = IMG_ANCHO - 40, altoL, H;
      var pide = PM.imagen || {};
      if (pide.aspecto) {
        altoL = Math.round(anchoL / pide.aspecto); H = Math.round(arriba + altoL * pr + abajo);
      } else {
        var p0 = pide.proporcion || 5 / 4, lista = PROPORCIONES.filter(function (p) { return p < p0 - 1e-6; });
        lista.unshift(p0);
        for (var i = 0; i < lista.length; i++) {
          H = Math.round(W / lista[i]); altoL = Math.floor((H - arriba - abajo) / pr);
          if (altoL >= anchoL / 2) break;
        }
      }
      lienzoFijo(g, anchoL, altoL, pr, card).then(function (url) {
      var img = new Image();
      img.onload = function () {
        cv.width = W; cv.height = H;
        c.fillStyle = card; c.fillRect(0, 0, W, H);
        var y = m;
        c.fillStyle = acento; c.fillRect(m, y + 2 * pr, 4 * pr, (lTit.length * 30 - 6) * pr);
        c.fillStyle = tinta; c.font = '700 ' + 24 * pr + 'px "Playfair Display", Georgia, serif'; c.textBaseline = 'top'; c.textAlign = 'left';
        lTit.forEach(function (l) { c.fillText(l, m + 14 * pr, y); y += 30 * pr; });
        c.fillStyle = gris; c.font = '500 ' + 13 * pr + 'px Inter, sans-serif';
        lSub.forEach(function (l) { c.fillText(l, m, y); y += 18 * pr; });
        y += 10 * pr;
        if (ley.length) {
          c.font = '600 ' + 12 * pr + 'px Inter, sans-serif';
          filas.forEach(function (fila, k) {
            fila.forEach(function (it) {
              var yy = y + k * 22 * pr;
              var col = colorSerie(it.s), f = it.s.forma;
              if (f === 'linea' || f === 'punteada') {
                c.strokeStyle = col; c.lineWidth = 2 * pr; c.setLineDash(f === 'punteada' ? [4 * pr, 3 * pr] : []);
                c.beginPath(); c.moveTo(it.x, yy + 9 * pr); c.lineTo(it.x + 12 * pr, yy + 9 * pr); c.stroke(); c.setLineDash([]);
              } else if (f === 'punto') {
                c.fillStyle = col; c.beginPath(); c.arc(it.x + 6 * pr, yy + 9 * pr, 5 * pr, 0, 2 * Math.PI); c.fill();
              } else { c.fillStyle = col; c.fillRect(it.x, yy + 4 * pr, 12 * pr, 10 * pr); }
              c.fillStyle = tinta; c.fillText(it.s.name, it.x + 18 * pr, yy + 2 * pr);
            });
          });
          y += hLey;
        }
        c.drawImage(img, lado, y, anchoL * pr, altoL * pr);
        // pie: fuente a la izquierda, firma oficial abajo a la derecha
        var base = H - m * 0.75;
        c.fillStyle = gris; c.font = '500 ' + 11 * pr + 'px Inter, sans-serif'; c.textBaseline = 'alphabetic';
        var yf = base - (lFue.length - 1) * 15 * pr;
        lFue.forEach(function (l) { c.fillText(l, m, yf); yf += 15 * pr; });
        firma(c, W - m, base, hFirma, dk);
        cv.toBlob(function (b) { bajar(slug(titulo) + '-' + new Date().toISOString().slice(0, 10) + '.png', b); ok(); }, 'image/png');
      };
      img.src = url;
      });
    }); });
  };
  // la línea de fuente de cada página ya empieza con «Fuente:»: no repetirlo en el CSV
  function conFuente(t) { return /^\s*fuente/i.test(t) ? t.trim() : 'Fuente: ' + t; }
  PM.descargarDatos = function () {
    var g = graficos[0]; if (!g) return;
    if (PM.tablaDatos) {   // la página declara su tabla (dispersiones, mapas: el eje X no es de categorías)
      var t = PM.tablaDatos(), celda = function (v) {
        if (v == null || (typeof v === 'number' && isNaN(v))) return '';
        if (typeof v === 'number') return String(Math.round(v * 1e6) / 1e6).replace('.', ',');
        v = String(v); return /[;"\n]/.test(v) ? '"' + v.replace(/"/g, '""') + '"' : v;
      };
      var ft = [t.cols.map(celda).join(';')].concat(t.filas.map(function (r) { return r.map(celda).join(';'); }));
      ft.push('', conFuente(texto('.source-txt')));
      bajar(slug(texto('.sec-title')) + '-' + new Date().toISOString().slice(0, 10) + '.csv', new Blob(['\ufeff' + ft.join('\r\n')], { type: 'text/csv;charset=utf-8' }));
      return;
    }
    var opt = g.chart.getOption(), x = (opt.xAxis && opt.xAxis[0] && opt.xAxis[0].data) || [];
    var cols = {}, orden = [];
    (opt.series || []).forEach(function (s) {
      if (!s.name || s.name.charAt(0) === '_') return;
      if (!cols[s.name]) { cols[s.name] = []; orden.push(s.name); }
      (s.data || []).forEach(function (v, i) {
        var n = v == null ? null : typeof v === 'object' ? (Array.isArray(v) ? v[v.length - 1] : v.value) : v;
        if (n == null || n === '-' || isNaN(n)) return;
        cols[s.name][i] = cols[s.name][i] == null ? +n : cols[s.name][i] + +n;   // partes de una misma serie (por año o por signo) se juntan
      });
    });
    var fmt = function (n) { return n == null ? '' : String(Math.round(n * 1e6) / 1e6).replace('.', ','); };
    var filas = ['Período;' + orden.join(';')];
    x.forEach(function (k, i) { filas.push(k + ';' + orden.map(function (n) { return fmt(cols[n][i]); }).join(';')); });
    var titulo = texto('.sec-title');
    filas.push('', conFuente(texto('.source-txt')));
    bajar(slug(titulo) + '-' + new Date().toISOString().slice(0, 10) + '.csv', new Blob(['\ufeff' + filas.join('\r\n')], { type: 'text/csv;charset=utf-8' }));
  };
  // Marca del pie: el logo compacto OFICIAL de la barra superior de populi.org.bo
  // (astro-frontend/src/components/Logo.astro, modo compact: mismo viewBox, posiciones y tamaños).
  // Reemplaza las copias sueltas que algunos gráficos traían en su pie.
  var MARCA = '<svg class="marca" viewBox="0 0 310 130" xmlns="http://www.w3.org/2000/svg" role="img" aria-label="Populi">' +
    '<text class="marca-p" x="6" y="112" font-family="\'Playfair Display\', Georgia, serif" font-size="145" font-weight="700">P</text>' +
    '<text class="marca-r" x="92" y="87" font-family="\'Playfair Display\', Georgia, serif" font-size="55" font-weight="400" font-style="italic" letter-spacing="1">opuli</text></svg>';
  function ponerMarca(src) {
    src.querySelectorAll('svg').forEach(function (v) { if (v.getAttribute('viewBox') === '0 0 310 130' && !v.classList.contains('marca')) v.remove(); });
    if (!src.querySelector('.marca')) src.insertAdjacentHTML('beforeend', MARCA);
  }
  function ponerDescargas() {
    var src = document.querySelector('.source');
    if (src) ponerMarca(src);
    if (!src || src.querySelector('.dls')) return;
    var caja = document.createElement('span'); caja.className = 'dls';
    caja.innerHTML = '<button type="button" class="dl" data-dl="img" title="Descargar el gráfico como imagen (PNG)">' + ICONO + '<span><span class="dl-t">Descargar </span>imagen</span></button>' +
      '<button type="button" class="dl" data-dl="csv" title="Descargar los datos del gráfico (CSV)">' + ICONO + '<span><span class="dl-t">Descargar </span>datos</span></button>';
    var txt = src.querySelector('.source-txt');
    if (txt && txt.nextSibling) src.insertBefore(caja, txt.nextSibling); else src.appendChild(caja);
    caja.addEventListener('click', function (e) {
      var b = e.target.closest('.dl'); if (!b) return;
      if (b.dataset.dl === 'csv') { PM.descargarDatos(); return; }
      b.setAttribute('aria-busy', 'true');
      PM.descargarImagen().then(function () { b.removeAttribute('aria-busy'); });
    });
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', vigilarPaneles);
  else setTimeout(vigilarPaneles, 0);
  window.addEventListener('load', vigilarPaneles);

  window.PM = PM;
})();
