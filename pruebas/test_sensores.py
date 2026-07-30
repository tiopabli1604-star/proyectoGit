# -*- coding: utf-8 -*-
"""Sensores nuevos: cambio en una zona y sonido por los altavoces.

El de sonido se prueba de verdad: se reproduce un tono y se comprueba que el
vigilante lo oye por loopback. Si el equipo no tiene loopback, esas
comprobaciones se marcan como omitidas en vez de darlas por buenas.
"""
import os
import sys
import threading
import time

import cv2
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import main as G

W, H = 800, 600
MON = {"left": 0, "top": 0, "width": W, "height": H}

R = []
OMITIDAS = []


def check(nombre, ok, extra=""):
    R.append(ok)
    extra = str(extra) if extra else ""
    print(f"  {'OK  ' if ok else 'FALLO'} {nombre}{(' — ' + extra) if extra else ''}")


def omitir(nombre, motivo):
    OMITIDAS.append(nombre)
    print(f"  OMIT {nombre} — {motivo}")


def fondo(v=40):
    img = np.zeros((H, W, 3), np.uint8)
    img[:] = (v, v, v)
    return img


def con_mancha(cx, cy, lado=60, color=(220, 220, 220)):
    img = fondo()
    cv2.rectangle(img, (cx - lado // 2, cy - lado // 2),
                  (cx + lado // 2, cy + lado // 2), color, -1)
    return img


def instalar(secuencia):
    est = {"i": 0}

    def grab():
        img = secuencia[min(est["i"], len(secuencia) - 1)]
        est["i"] += 1
        return img, MON
    G.Finder.grab_screen = staticmethod(grab)
    return est


# ------------------------------------------------------- ZoneChange
def p_cambio_mide():
    print("--- ZoneChange mide el cambio de la zona ---")
    f = G.Finder()
    instalar([fondo(40), fondo(40), fondo(200)])
    z = G.ZoneChange(f, minimo_muestras=1)
    d0, c0, _u = z.paso()
    check("el primer fotograma no da valor", c0 is None and d0 is False)
    d1, c1, _u = z.paso()
    check("dos iguales dan cero", c1 == 0.0 and not d1, f"{c1}")
    d2, c2, _u = z.paso()
    check("uno distinto da mucho y dispara", c2 > 100 and d2, f"{c2:.1f}")


def p_calentamiento():
    """Sin fondo medido no se puede juzgar: en una zona animada el primer dato
    dispararia al instante contra el suelo absoluto."""
    print("--- no dispara antes de tener fondo medido ---")
    f = G.Finder()
    # alterna dos imagenes: cambio constante y alto desde el primer momento
    sec = []
    for i in range(12):
        sec.append(fondo(40) if i % 2 == 0 else fondo(90))
    instalar(sec)
    z = G.ZoneChange(f)          # minimo_muestras por defecto = 4
    primeros = [z.paso()[0] for _ in range(4)]
    check("los primeros no disparan aunque haya cambio", not any(primeros),
          str(primeros))

    # ya con fondo medido, un cambio MUCHO mayor si debe disparar
    instalar([fondo(40)] * 6 + [fondo(240)])
    z2 = G.ZoneChange(f)
    for _ in range(6):
        z2.paso()
    d, c, u = z2.paso()
    check("y despues del calentamiento si juzga", d,
          f"cambio {c:.1f} vs umbral {u:.2f}")


def p_umbral_adaptativo():
    """El umbral tiene que subir si la zona ya tiene algo animado de fondo, o
    cualquier animacion del juego contaria como 'ha pasado algo'."""
    print("--- el umbral se adapta al movimiento de fondo ---")
    f = G.Finder()
    rng = np.random.RandomState(4)
    # 25 fotogramas con un parpadeo constante de fondo
    sec = []
    for _ in range(25):
        img = fondo(60)
        img[:] = np.clip(img.astype(np.int16)
                         + rng.randint(-25, 26, img.shape), 0, 255).astype(np.uint8)
        sec.append(np.ascontiguousarray(img))
    instalar(sec)
    z = G.ZoneChange(f)
    disparos = 0
    for _ in range(len(sec)):
        d, _c, _u = z.paso()
        disparos += 1 if d else 0
    check("el umbral acaba por encima del suelo", z.umbral() > z.umbral_min,
          f"umbral {z.umbral():.2f} vs suelo {z.umbral_min}")
    check("y no dispara con el fondo", disparos == 0,
          f"{disparos} disparos en {len(sec)} fotogramas")


def p_cambio_respeta_la_zona():
    print("--- solo mira dentro de la zona del objetivo ---")
    f = G.Finder()
    # zona: la mitad izquierda
    f.roi_left, f.roi_right, f.roi_top, f.roi_bottom = 0.0, 0.5, 0.0, 1.0
    # la mancha aparece en la mitad DERECHA: no deberia notarse
    instalar([fondo(), fondo(), con_mancha(650, 300)])
    z = G.ZoneChange(f)
    z.paso()
    z.paso()
    d, c, _u = z.paso()
    check("un cambio fuera de la zona no cuenta", not d and c < 1.0, f"{c:.2f}")

    # y ahora dentro (con fotogramas quietos antes, para el calentamiento)
    f2 = G.Finder()
    f2.roi_left, f2.roi_right, f2.roi_top, f2.roi_bottom = 0.0, 0.5, 0.0, 1.0
    instalar([fondo()] * 6 + [con_mancha(150, 300)])
    z2 = G.ZoneChange(f2)
    for _ in range(6):
        z2.paso()
    d2, c2, u2 = z2.paso()
    check("y dentro si", d2, f"cambio {c2:.2f} vs umbral {u2:.2f}")


def p_reset_cambio():
    print("--- reset olvida el fotograma anterior ---")
    f = G.Finder()
    instalar([fondo()])
    z = G.ZoneChange(f)
    z.paso()
    check("tras un paso hay referencia", z.prev is not None)
    z.reset()
    check("y tras reset no", z.prev is None and not z.fondo)
    check("el siguiente paso vuelve a dar None", z.paso()[1] is None)


# ------------------------------------------------------- en el guion
class TecladoEspia:
    def __init__(self):
        self.acciones = []

    def press(self, k):
        self.acciones.append(("press", str(k)))

    def release(self, k):
        self.acciones.append(("release", str(k)))

    def type(self, s):
        self.acciones.append(("type", s))


OBJETIVOS = {"flotador": {"mode": "unico", "sat_min": 40, "val_min": 90,
                          "min_area": 80, "max_area": 40000, "max_side": 80,
                          "frames": 1, "on_gui": False,
                          "roi_left": 0.0, "roi_right": 1.0,
                          "roi_top": 0.0, "roi_bottom": 1.0}}


def guion(texto):
    lineas = []
    s = G.Script(lineas.append, OBJETIVOS)
    s.sound = False
    s.keyboard = TecladoEspia()
    s.cambio_intervalo = 0.01
    errores = s.load(texto)
    return s, errores, lineas


def correr(s, limite=12.0):
    s.start()
    t0 = time.perf_counter()
    while s.running and time.perf_counter() - t0 < limite:
        time.sleep(0.01)
    colgado = s.running
    s.stop()
    s._thread.join(timeout=2.0)
    return colgado


def p_analisis():
    print("--- analisis de las instrucciones nuevas ---")
    pasos, errs = G.Script.parse(
        "esperar_cambio flotador 5 si_falla repetir\n"
        "esperar_sonido 3 si_falla parar\n"
        "pitar\nparar", OBJETIVOS)
    check("acepta las dos", not errs, str(errs))
    if pasos:
        d = G.Script.describe(pasos)
        check("explica el cambio", "algo cambie en la zona" in d[0], d[0])
        check("explica el sonido", "suene algo" in d[1], d[1])
    casos = [
        ("esperar_cambio", "necesita el objetivo"),
        ("esperar_cambio fantasma 5", "no hay un objetivo llamado"),
        ("esperar_cambio flotador", "necesita un límite de segundos"),
        ("esperar_cambio flotador mucho", "van los segundos"),
        ("esperar_sonido", "necesita un límite de segundos"),
        ("esperar_sonido 5 si_falla loquesea", "tras 'si_falla' pon"),
        ("esperar_sonido 5 si_falla ir 99", "no existe"),
    ]
    for texto, esperado in casos:
        errs2 = G.Script.parse(texto, OBJETIVOS)[1]
        check(repr(texto)[:40], any(esperado in e for e in errs2),
              (errs2[0] if errs2 else "no dio error")[:60])


def p_cambio_en_el_guion():
    print("--- el guion reacciona a un cambio en la zona ---")
    # cinco fotogramas quietos y luego algo aparece
    instalar([fondo()] * 5 + [con_mancha(400, 300)] * 10)
    s, errs, log = guion("esperar_cambio flotador 5\npitar\nparar")
    check("analiza", not errs, str(errs))
    colgado = correr(s)
    check("no se cuelga", not colgado)
    check("lo detecta", any("algo ha cambiado" in l for l in log),
          str([l for l in log if "cambiado" in l][:1]))
    check("y sigue al paso 2", any(l.startswith("2. pitido") for l in log),
          str(log))


def p_cambio_no_pasa_nada():
    print("--- si nada cambia, agota el tiempo y aplica la politica ---")
    instalar([fondo()])
    s, errs, log = guion("esperar_cambio flotador 0.4 si_falla ir 3\n"
                         "parar\n"
                         "pitar\n"
                         "parar")
    check("analiza", not errs, str(errs))
    correr(s)
    check("dice que nada cambio", any("nada cambió" in l for l in log),
          str([l for l in log if "nada cambi" in l][:1]))
    check("y salta al 3", any(l.startswith("3. pitido") for l in log),
          str(log))


def p_cambio_se_corta():
    print("--- se puede parar a mitad de la espera ---")
    instalar([fondo()])
    s, errs, log = guion("esperar_cambio flotador 60\nparar")
    s.start()
    time.sleep(0.3)
    check("esta en marcha", s._thread.is_alive())
    s.stop()
    s._thread.join(timeout=3.0)
    check("el hilo muere sin esperar los 60 s", not s._thread.is_alive())


# ------------------------------------------------------- sonido
SR = 44100


def tono(freq=440.0, dur=1.5, vol=0.3):
    t = np.arange(int(dur * SR)) / SR
    onda = (vol * np.sin(2 * np.pi * freq * t)).astype("float32")
    return np.column_stack([onda, onda])


def p_sonido_disponible():
    print("--- la libreria de audio esta ---")
    check("SoundWatch.disponible()", G.SoundWatch.disponible() is True,
          "sin soundcard no hay instrucciones de sonido")


def p_sonido_oye():
    print("--- el vigilante de sonido oye un tono de verdad ---")
    if not G.SoundWatch.disponible():
        omitir("oir un tono", "no hay libreria de audio")
        return
    sw = G.SoundWatch()
    if not sw.start():
        omitir("oir un tono", f"no se abrio el loopback: {sw.error}")
        return
    try:
        time.sleep(0.8)                     # que mida el fondo (silencio)
        fondo_medido = sw.nivel
        sw.limpiar()
        check("en silencio no hay golpe", not sw.hubo_golpe(),
              f"nivel de fondo {fondo_medido:.5f}")

        import soundcard as sc
        alt = sc.default_speaker()

        def reproducir():
            with alt.player(samplerate=SR, channels=2, blocksize=512) as sp:
                sp.play(tono())
        hilo = threading.Thread(target=reproducir, daemon=True)
        hilo.start()
        t0 = time.perf_counter()
        while not sw.hubo_golpe() and time.perf_counter() - t0 < 4.0:
            time.sleep(0.02)
        oyo = sw.hubo_golpe()
        tardo = time.perf_counter() - t0
        hilo.join(timeout=4)
        check("oye el tono", oyo,
              f"nivel {sw.nivel:.5f} sobre fondo {sw.fondo:.5f} en {tardo:.2f} s")
        check("y reacciona rapido", not oyo or tardo < 1.5, f"{tardo:.2f} s")
    finally:
        sw.stop()
    check("para limpio", not sw.activo)


def p_sonido_en_el_guion():
    print("--- 'esperar_sonido' dentro del guion ---")
    if not G.SoundWatch.disponible():
        omitir("esperar_sonido", "no hay libreria de audio")
        return
    prueba = G.SoundWatch()
    if not prueba.start():
        omitir("esperar_sonido", f"no se abrio el loopback: {prueba.error}")
        return
    prueba.stop()

    instalar([fondo()])
    s, errs, log = guion("esperar_sonido 4\npitar\nparar")
    check("analiza", not errs, str(errs))
    s.start()
    time.sleep(1.2)                          # deja que abra el audio y mida
    import soundcard as sc
    alt = sc.default_speaker()

    def reproducir():
        with alt.player(samplerate=SR, channels=2, blocksize=512) as sp:
            sp.play(tono(dur=1.0))
    threading.Thread(target=reproducir, daemon=True).start()
    t0 = time.perf_counter()
    while s.running and time.perf_counter() - t0 < 8:
        time.sleep(0.02)
    s.stop()
    s._thread.join(timeout=3.0)
    check("lo oye", any("he oído algo" in l for l in log),
          str([l for l in log if "oído" in l or "oí" in l][:1]))
    check("y sigue al paso 2", any(l.startswith("2. pitido") for l in log),
          str(log))


def p_sonido_sin_nada_que_oir():
    print("--- si no suena nada, agota el tiempo ---")
    if not G.SoundWatch.disponible():
        omitir("silencio", "no hay libreria de audio")
        return
    prueba = G.SoundWatch()
    if not prueba.start():
        omitir("silencio", f"no se abrio el loopback: {prueba.error}")
        return
    prueba.stop()
    instalar([fondo()])
    s, errs, log = guion("esperar_sonido 1.5 si_falla ir 3\n"
                         "parar\npitar\nparar")
    check("analiza", not errs, str(errs))
    correr(s, limite=12.0)
    check("dice que no oyo nada", any("no oí nada" in l for l in log),
          str([l for l in log if "no o" in l][:1]))
    check("y salta al 3", any(l.startswith("3. pitido") for l in log), str(log))


if __name__ == "__main__":
    for fn in (p_cambio_mide, p_calentamiento, p_umbral_adaptativo,
               p_cambio_respeta_la_zona, p_reset_cambio,
               p_analisis, p_cambio_en_el_guion, p_cambio_no_pasa_nada,
               p_cambio_se_corta, p_sonido_disponible, p_sonido_oye,
               p_sonido_en_el_guion, p_sonido_sin_nada_que_oir):
        try:
            fn()
        except Exception:
            import traceback
            traceback.print_exc()
            R.append(False)
        print()
    fallos = R.count(False)
    resumen = f"{len(R)} comprobaciones"
    if OMITIDAS:
        resumen += f", {len(OMITIDAS)} bloque(s) omitido(s): " + ", ".join(OMITIDAS)
    print(resumen + ", " + ("TODO OK" if not fallos else f"{fallos} FALLO(S)"))
    sys.exit(1 if fallos else 0)
