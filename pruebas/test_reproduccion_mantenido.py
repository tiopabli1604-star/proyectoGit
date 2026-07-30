# -*- coding: utf-8 -*-
"""El caso del usuario: reproduciendo una macro larga, salta el captcha, el juego
suelta la W y el clic, y el resto de la reproduccion no hace nada.

La macro solo pulsa la tecla UNA vez, al principio: no vuelve a hacerlo hasta que
la suelta. Si el juego se olvida en el minuto 3, los 17 que quedan son inutiles.
Por eso hace falta un refuerzo en el reproductor, no solo en el guion.
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


class Juego:
    """Simula lo que ve el juego: que teclas y botones tiene por pulsados.

    olvidar() es lo que hace de verdad Minecraft al abrir el cofre: se olvida de
    lo que estaba pulsado. Como fisicamente sigue bajado, no llega ninguna
    pulsacion nueva... salvo que alguien la mande.
    """

    def __init__(self):
        self.teclas = set()
        self.botones = set()
        self.historial = []
        self.olvidos = 0

    # --- teclado
    def press(self, k):
        self.teclas.add(str(k))
        self.historial.append(("kd", str(k)))

    def release(self, k):
        self.teclas.discard(str(k))
        self.historial.append(("ku", str(k)))

    def type(self, s):
        pass

    def olvidar(self):
        self.teclas.clear()
        self.botones.clear()
        self.olvidos += 1
        self.historial.append(("olvido", ""))


class Raton:
    def __init__(self, juego):
        self.juego = juego
        self.position = (0, 0)

    def press(self, b):
        self.juego.botones.add(str(b))
        self.juego.historial.append(("md", str(b)))

    def release(self, b):
        self.juego.botones.discard(str(b))
        self.juego.historial.append(("mu", str(b)))

    def scroll(self, dx, dy):
        pass


def macro_larga(dur=6.0):
    """Mantiene la W y el clic izquierdo todo el rato, como la del usuario."""
    ev = [{"t": 0.0, "e": "kd", "k": "c:w"},
          {"t": 0.05, "e": "mc", "x": 0, "y": 0, "b": "left", "d": True}]
    # algo de movimiento a lo largo del tiempo, para que dure
    t = 0.2
    while t < dur:
        ev.append({"t": t, "e": "mr", "dx": 1, "dy": 0})
        t += 0.2
    ev.append({"t": dur, "e": "mc", "x": 0, "y": 0, "b": "left", "d": False})
    ev.append({"t": dur + 0.05, "e": "ku", "k": "c:w"})
    return ev


def preparar(keepalive=0.3, reforzar=True):
    juego = Juego()
    p = G.Player()
    p.keyboard = juego
    p.mouse = Raton(juego)
    p.reforzar = reforzar
    p.keepalive = keepalive
    return p, juego


def p_sin_refuerzo_se_pierde():
    """Primero, demostrar el problema: sin refuerzo, se queda sin hacer nada."""
    print("--- sin refuerzo: tras el olvido, la W no vuelve (el problema) ---")
    p, juego = preparar(reforzar=False)
    ev = macro_larga(3.0)
    hilo = threading.Thread(target=lambda: p.play_sync(ev), daemon=True)
    hilo.start()
    time.sleep(0.5)
    check("la W esta pulsada al principio", "w" in juego.teclas,
          str(juego.teclas))
    check("y el clic tambien", "Button.left" in juego.botones,
          str(juego.botones))
    juego.olvidar()                        # salta el captcha
    time.sleep(1.5)
    check("tras el olvido la W NO vuelve", "w" not in juego.teclas,
          str(juego.teclas))
    check("ni el clic", "Button.left" not in juego.botones, str(juego.botones))
    p.stop()
    hilo.join(timeout=3)


def p_con_refuerzo_vuelve():
    print("--- con refuerzo: vuelven solos ---")
    p, juego = preparar(keepalive=0.3)
    ev = macro_larga(4.0)
    hilo = threading.Thread(target=lambda: p.play_sync(ev), daemon=True)
    hilo.start()
    time.sleep(0.5)
    check("estan pulsados", "w" in juego.teclas
          and "Button.left" in juego.botones,
          f"{juego.teclas} {juego.botones}")
    juego.olvidar()
    t0 = time.perf_counter()
    while (("w" not in juego.teclas or "Button.left" not in juego.botones)
           and time.perf_counter() - t0 < 3):
        time.sleep(0.02)
    tardo = time.perf_counter() - t0
    check("la W vuelve sola", "w" in juego.teclas, f"en {tardo:.2f} s")
    check("y el clic tambien", "Button.left" in juego.botones,
          f"en {tardo:.2f} s")
    check("en menos de dos refuerzos", tardo < 0.7,
          f"{tardo:.2f} s con refuerzo cada 0.3 s")
    p.stop()
    hilo.join(timeout=3)


def p_aguanta_varios_olvidos():
    print("--- aguanta varios captchas seguidos ---")
    p, juego = preparar(keepalive=0.25)
    ev = macro_larga(6.0)
    hilo = threading.Thread(target=lambda: p.play_sync(ev), daemon=True)
    hilo.start()
    time.sleep(0.4)
    recuperaciones = 0
    for _ in range(4):
        juego.olvidar()
        t0 = time.perf_counter()
        while "w" not in juego.teclas and time.perf_counter() - t0 < 2:
            time.sleep(0.02)
        if "w" in juego.teclas:
            recuperaciones += 1
        time.sleep(0.3)
    p.stop()
    hilo.join(timeout=3)
    check("se recupera de los 4 olvidos", recuperaciones == 4,
          f"{recuperaciones} de 4")
    check("y el juego los conto todos", juego.olvidos == 4, str(juego.olvidos))


def p_no_suelta_durante_el_mantenido():
    print("--- el refuerzo no suelta: no reiniciaria el picado ---")
    p, juego = preparar(keepalive=0.2)
    ev = macro_larga(2.0)
    hilo = threading.Thread(target=lambda: p.play_sync(ev), daemon=True)
    hilo.start()
    time.sleep(1.2)
    # entre el md inicial y el final no debe haber ningun mu
    hist = juego.historial
    primer_md = next((i for i, h in enumerate(hist) if h[0] == "md"), None)
    mu_intermedios = [h for h in hist[primer_md:] if h[0] == "mu"]
    p.stop()
    hilo.join(timeout=3)
    check("ni una suelta del raton mientras se mantiene", not mu_intermedios,
          str(mu_intermedios))


def p_respeta_lo_soltado_por_la_macro():
    print("--- si la macro suelta la tecla, el refuerzo no la resucita ---")
    p, juego = preparar(keepalive=0.25)
    ev = [{"t": 0.0, "e": "kd", "k": "c:w"},
          {"t": 0.4, "e": "ku", "k": "c:w"},
          {"t": 1.6, "e": "mr", "dx": 1, "dy": 0}]
    p.play_sync(ev)
    time.sleep(0.4)
    check("acaba sin la W pulsada", "w" not in juego.teclas, str(juego.teclas))
    # tras el ku no debe haber ningun kd de la w
    hist = juego.historial
    ult_ku = max((i for i, h in enumerate(hist)
                  if h == ("ku", "w")), default=-1)
    despues = [h for h in hist[ult_ku + 1:] if h == ("kd", "w")]
    check("y no la vuelve a pulsar despues de soltarla", not despues,
          str(hist[-5:]))


def p_al_acabar_lo_suelta_todo():
    print("--- al acabar o al cortar, lo suelta todo ---")
    p, juego = preparar(keepalive=0.25)
    ev = macro_larga(30.0)             # larga, para cortarla a mitad
    hilo = threading.Thread(target=lambda: p.play_sync(ev), daemon=True)
    hilo.start()
    time.sleep(0.8)
    check("mantiene cosas", juego.teclas or juego.botones,
          f"{juego.teclas} {juego.botones}")
    p.stop()
    hilo.join(timeout=4)
    check("tras cortar, nada pulsado",
          not juego.teclas and not juego.botones,
          f"{juego.teclas} {juego.botones}")
    check("y el reproductor lo sabe",
          not p._sostenidas and not p._sostenidos_btn,
          f"{p._sostenidas} {p._sostenidos_btn}")
    check("el hilo del refuerzo ha muerto",
          p._ka_thread is None or not p._ka_thread.is_alive())


def p_desactivable():
    print("--- se puede desactivar ---")
    p, juego = preparar(reforzar=False)
    ev = macro_larga(1.5)
    p.play_sync(ev)
    check("no hubo ningun refuerzo", p.refuerzos == 0, str(p.refuerzos))
    p2, juego2 = preparar(keepalive=0.25, reforzar=True)
    p2.play_sync(macro_larga(1.5))
    check("y activado si los hay", p2.refuerzos > 0, str(p2.refuerzos))


if __name__ == "__main__":
    for fn in (p_sin_refuerzo_se_pierde, p_con_refuerzo_vuelve,
               p_aguanta_varios_olvidos, p_no_suelta_durante_el_mantenido,
               p_respeta_lo_soltado_por_la_macro, p_al_acabar_lo_suelta_todo,
               p_desactivable):
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
