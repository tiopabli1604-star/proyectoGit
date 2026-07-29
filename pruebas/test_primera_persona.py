# -*- coding: utf-8 -*-
"""Instrucciones del guion para juegos en primera persona.

Lo que mas importa comprobar aqui:

  · que 'girar' mueve EXACTAMENTE lo pedido, aunque lo reparta en varios envios
    (se verifica con un receptor de raw input de verdad, que es lo que ve el
    juego);
  · que al parar el guion se suelta todo lo que quedara pulsado. Si no, un F12
    durante un 'mantener w 30' deja la W enganchada y el personaje se va andando.
"""
import os
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import main as G

R = []


def check(nombre, ok, extra=""):
    R.append(ok)
    extra = str(extra) if extra else ""
    print(f"  {'OK  ' if ok else 'FALLO'} {nombre}{(' — ' + extra) if extra else ''}")


# ------------------------------------------------------------ girar
def escuchar(fn, espera=0.4):
    recibidos = []
    lis = G.RawMouseListener(lambda dx, dy: recibidos.append((dx, dy)))
    if not lis.start():
        raise RuntimeError(f"no arranco el raw input: {lis.error}")
    try:
        time.sleep(0.2)
        fn()
        time.sleep(espera)
    finally:
        lis.stop()
    return recibidos


def p_girar_suma_exacta():
    print("--- 'girar' mueve exactamente lo pedido, repartido ---")
    for dx, dy in ((200, 0), (0, -150), (-333, 77), (7, 3), (1, 0),
                   (1000, -1000)):
        got = escuchar(lambda: G.turn_camera(dx, dy, gap=0.001), espera=0.35)
        sx = sum(d[0] for d in got)
        sy = sum(d[1] for d in got)
        check(f"girar {dx} {dy}", (sx, sy) == (dx, dy),
              f"suma ({sx}, {sy}) en {len(got)} envio(s)")


def p_girar_reparte():
    print("--- reparte en trozos pequenos, no de un salto ---")
    got = escuchar(lambda: G.turn_camera(300, 0, paso_max=15, gap=0.001))
    check("hace varios envios", len(got) >= 15, f"{len(got)} envios")
    mayor = max(abs(d[0]) for d in got) if got else 0
    check("y ninguno pasa del limite", mayor <= 16, f"el mayor fue {mayor}")
    check("sumando exacto", sum(d[0] for d in got) == 300,
          str(sum(d[0] for d in got)))


def p_girar_cero():
    print("--- girar 0 0 no manda nada ---")
    got = escuchar(lambda: G.turn_camera(0, 0), espera=0.25)
    check("sin envios", not got, str(got))


def p_girar_se_puede_cortar():
    print("--- un giro largo se corta al parar ---")
    parar = threading.Event()

    def espera(seg):
        return not parar.wait(seg)

    def girar():
        threading.Timer(0.15, parar.set).start()
        G.turn_camera(50000, 0, paso_max=5, gap=0.004, espera=espera)
    t0 = time.perf_counter()
    got = escuchar(girar, espera=0.3)
    tardo = time.perf_counter() - t0
    completo = sum(d[0] for d in got)
    check("no ha hecho el giro entero", completo < 50000, f"{completo} de 50000")
    check("y ha tardado poco", tardo < 2.0, f"{tardo:.2f} s")


# ------------------------------------------------------------ guion
class RatonEspia:
    def __init__(self):
        self.acciones = []
        self.position = (0, 0)

    def press(self, b):
        self.acciones.append(("press", str(b)))

    def release(self, b):
        self.acciones.append(("release", str(b)))


class TecladoEspia:
    def __init__(self):
        self.acciones = []

    def press(self, k):
        self.acciones.append(("press", str(k)))

    def release(self, k):
        self.acciones.append(("release", str(k)))

    def type(self, s):
        self.acciones.append(("type", s))


OBJETIVOS = {"cosa": {"mode": "unico", "sat_min": 40, "val_min": 90,
                      "min_area": 80, "max_area": 40000, "max_side": 80,
                      "frames": 1, "on_gui": False,
                      "roi_left": 0.0, "roi_right": 1.0,
                      "roi_top": 0.0, "roi_bottom": 1.0}}


def guion(texto):
    lineas = []
    s = G.Script(lineas.append, OBJETIVOS)
    s.interval = 0.02
    s.confirmaciones = 1
    s.sound = False
    s.keyboard = TecladoEspia()
    s.mouse = RatonEspia()
    errores = s.load(texto)
    return s, errores, lineas


def correr(s, limite=8.0):
    s.start()
    t0 = time.perf_counter()
    while s.running and time.perf_counter() - t0 < limite:
        time.sleep(0.01)
    colgado = s.running
    s.stop()
    s._thread.join(timeout=2.0)
    return colgado


def p_analiza_lo_nuevo():
    print("--- el analisis acepta las instrucciones nuevas ---")
    bueno = ("girar 200 0\n"
             "girar -100 -50\n"
             "mantener w 2\n"
             "mantener shift\n"
             "soltar shift\n"
             "mantener_clic 3\n"
             "mantener_clic derecho\n"
             "soltar_clic derecho\n"
             "pulsar espacio\n"
             "repetir 3\n")
    pasos, errs = G.Script.parse(bueno, OBJETIVOS)
    check("sin errores", not errs, str(errs))
    check("10 pasos", len(pasos) == 10, str(len(pasos)))
    d = G.Script.describe(pasos)
    check("explica el giro", "200 a la derecha" in d[0], d[0])
    check("explica el giro negativo",
          "100 a la izquierda" in d[1] and "50 arriba" in d[1], d[1])
    check("explica el mantener con segundos", "2 s" in d[2], d[2])
    check("y el mantener sin segundos", "hasta un 'soltar'" in d[3], d[3])
    check("explica las vueltas", "hasta 3 veces" in d[9], d[9])


def p_errores_nuevos():
    print("--- y caza los errores de las nuevas ---")
    casos = [
        ("girar", "necesita dos números"),
        ("girar 200", "necesita dos números"),
        ("girar mucho poco", "no son dos números"),
        ("mantener", "necesita una tecla"),
        ("mantener teclamala 2", "no conozco la tecla"),
        ("mantener w mucho", "no es un número de segundos"),
        ("soltar w 3", "solo lleva la tecla"),
        ("mantener_clic derecho mucho", "solo van los segundos"),
        ("mantener_clic 2 raro", "no entiendo"),
    ]
    for texto, esperado in casos:
        _, errs = G.Script.parse(texto, OBJETIVOS)[0], G.Script.parse(
            texto, OBJETIVOS)[1]
        check(repr(texto)[:34], any(esperado in e for e in errs),
              (errs[0] if errs else "no dio error")[:66])


def p_bucle_con_mantener_frena():
    print("--- un 'mantener' con segundos vale como freno del bucle ---")
    _, errs = G.Script.parse("mantener w 2\nrepetir", OBJETIVOS)[0], \
        G.Script.parse("mantener w 2\nrepetir", OBJETIVOS)[1]
    check("con segundos se acepta", not errs, str(errs))
    _, errs2 = G.Script.parse("mantener w\nrepetir", OBJETIVOS)[0], \
        G.Script.parse("mantener w\nrepetir", OBJETIVOS)[1]
    check("sin segundos NO frena nada, se rechaza",
          any("sin freno" in e for e in errs2), str(errs2))
    _, errs3 = G.Script.parse("girar 100 0\nrepetir", OBJETIVOS)[0], \
        G.Script.parse("girar 100 0\nrepetir", OBJETIVOS)[1]
    check("'girar' solo tampoco frena", any("sin freno" in e for e in errs3),
          str(errs3))
    _, errs4 = G.Script.parse("girar 100 0\nrepetir 5", OBJETIVOS)[0], \
        G.Script.parse("girar 100 0\nrepetir 5", OBJETIVOS)[1]
    check("pero con 'repetir 5' si, que acaba solo", not errs4, str(errs4))


def p_mantener_y_soltar():
    print("--- mantener y soltar de verdad ---")
    s, errs, log = guion("mantener w 0.2\nparar")
    check("analiza", not errs, str(errs))
    correr(s)
    ops = s.keyboard.acciones
    check("pulsa y suelta la w",
          len(ops) == 2 and ops[0][0] == "press" and ops[1][0] == "release",
          str(ops))
    check("y no queda nada pendiente", not s._teclas, str(s._teclas))


def p_suelta_al_parar():
    print("--- al parar, suelta lo que quedara pulsado (lo importante) ---")
    s, errs, log = guion("mantener w\nmantener_clic\nesperar 30\nparar")
    check("analiza", not errs, str(errs))
    s.start()
    t0 = time.perf_counter()
    while not any("esperando" in l for l in log) and time.perf_counter() - t0 < 3:
        time.sleep(0.01)
    check("la w esta pulsada mientras corre", "w" in s._teclas, str(s._teclas))
    check("y el clic tambien", "left" in s._botones, str(s._botones))
    s.stop()
    s._thread.join(timeout=3.0)
    check("el hilo muere", not s._thread.is_alive())
    check("la w se suelta", not s._teclas, str(s._teclas))
    check("el clic se suelta", not s._botones, str(s._botones))
    ks = s.keyboard.acciones
    check("hay un release de la tecla",
          any(a[0] == "release" for a in ks), str(ks))
    ms = s.mouse.acciones
    check("y un release del boton",
          any(a[0] == "release" for a in ms), str(ms))
    check("y lo dice en el registro",
          any("Suelto lo que quedaba" in l for l in log),
          str([l for l in log if "Suelto" in l]))


def p_suelta_aunque_reviente():
    print("--- tambien suelta si el guion revienta a media ejecucion ---")

    class RatonRoto(RatonEspia):
        def press(self, b):
            raise RuntimeError("raton averiado a proposito")

    s, errs, log = guion("mantener w\nmantener_clic\nparar")
    check("analiza", not errs, str(errs))
    s.mouse = RatonRoto()
    correr(s, limite=4.0)
    check("el guion informa del error",
          any("cortado por un error" in l for l in log),
          str([l for l in log if "error" in l]))
    check("pero la w NO queda pulsada", not s._teclas, str(s._teclas))
    check("y se ve el release en el teclado",
          any(a[0] == "release" for a in s.keyboard.acciones),
          str(s.keyboard.acciones))
    check("y lo dice en el registro",
          any("Suelto lo que quedaba" in l for l in log),
          str([l for l in log if "Suelto" in l]))


def p_repetir_veces():
    print("--- 'repetir N' da N vueltas y para solo ---")
    s, errs, log = guion("mantener w 0.05\nrepetir 3")
    check("analiza", not errs, str(errs))
    colgado = correr(s, limite=6.0)
    check("acaba solo, no se cuelga", not colgado)
    presses = [a for a in s.keyboard.acciones if a[0] == "press"]
    # la primera pasada mas 3 vueltas = 4 pulsaciones
    check("4 pasadas (la primera y 3 vueltas)", len(presses) == 4,
          f"{len(presses)} pulsaciones")
    check("lo dice al acabar",
          any("hechas las 3 vueltas" in l for l in log),
          str([l for l in log if "vuelta" in l]))


def p_repetir_sin_numero_sigue_infinito():
    print("--- 'repetir' sin numero sigue siendo infinito ---")
    s, errs, log = guion("mantener w 0.05\nrepetir")
    check("analiza", not errs, str(errs))
    s.start()
    time.sleep(0.8)
    sigue = s.running
    s.stop()
    s._thread.join(timeout=2.0)
    check("seguia en marcha tras 0.8 s", sigue)
    presses = [a for a in s.keyboard.acciones if a[0] == "press"]
    check("ha dado varias vueltas", len(presses) > 3, f"{len(presses)}")


def p_girar_dentro_del_guion():
    print("--- 'girar' dentro del guion llega al raw input ---")
    s, errs, log = guion("girar 120 -60\nparar")
    check("analiza", not errs, str(errs))
    got = escuchar(lambda: correr(s), espera=0.4)
    sx = sum(d[0] for d in got)
    sy = sum(d[1] for d in got)
    check("gira exactamente lo pedido", (sx, sy) == (120, -60),
          f"({sx}, {sy})")
    check("y lo apunta en el registro",
          any("girada la cámara" in l for l in log), str(log))


if __name__ == "__main__":
    for fn in (p_girar_suma_exacta, p_girar_reparte, p_girar_cero,
               p_girar_se_puede_cortar, p_analiza_lo_nuevo, p_errores_nuevos,
               p_bucle_con_mantener_frena, p_mantener_y_soltar,
               p_suelta_al_parar, p_suelta_aunque_reviente, p_repetir_veces,
               p_repetir_sin_numero_sigue_infinito, p_girar_dentro_del_guion):
        try:
            fn()
        except Exception:
            import traceback
            traceback.print_exc()
            R.append(False)
        print()
    fallos = R.count(False)
    print(f"{len(R)} comprobaciones, "
          + ("TODO OK" if not fallos else f"{fallos} FALLO(S)"))
    sys.exit(1 if fallos else 0)
