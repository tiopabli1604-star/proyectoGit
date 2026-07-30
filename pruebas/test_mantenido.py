# -*- coding: utf-8 -*-
"""Que el clic mantenido y la W sobrevivan al captcha, si o si.

El usuario insistio en que esto tiene que funcionar sin depender de que el guion
tenga un 'reafirmar' en el sitio justo. Hay tres mecanismos y aqui se comprueban
los tres por separado y juntos:

  1. el paso 'clic' aparta el boton mantenido y lo recupera (si no, el propio
     clic lo dejaria suelto);
  2. tras un 'desaparecer' con exito se reafirma todo, porque que el objetivo
     desaparezca suele ser que se ha cerrado la interfaz;
  3. un refuerzo en segundo plano vuelve a pulsar las teclas cada pocos
     segundos, que es la red de seguridad para cualquier caso no previsto.
"""
import os
import sys
import time

import cv2
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import main as G

W, H = 800, 600
MON = {"left": 0, "top": 0, "width": W, "height": H}

R = []


def check(nombre, ok, extra=""):
    R.append(ok)
    extra = str(extra) if extra else ""
    print(f"  {'OK  ' if ok else 'FALLO'} {nombre}{(' — ' + extra) if extra else ''}")


class TecladoEspia:
    """Ademas de apuntar, lleva la cuenta de si la tecla esta 'abajo'."""

    def __init__(self):
        self.acciones = []
        self.abajo = set()
        self.pulsaciones = {}

    def press(self, k):
        self.acciones.append(("press", str(k)))
        self.abajo.add(str(k))
        self.pulsaciones[str(k)] = self.pulsaciones.get(str(k), 0) + 1

    def release(self, k):
        self.acciones.append(("release", str(k)))
        self.abajo.discard(str(k))

    def type(self, s):
        self.acciones.append(("type", s))

    def olvidar(self):
        """Como el juego al abrir una interfaz: se olvida de lo pulsado."""
        self.abajo.clear()


class RatonEspia:
    def __init__(self):
        self.acciones = []
        self.abajo = set()
        self.position = (0, 0)

    def press(self, b):
        self.acciones.append(("press", str(b)))
        self.abajo.add(str(b))

    def release(self, b):
        self.acciones.append(("release", str(b)))
        self.abajo.discard(str(b))


def vacia():
    img = np.zeros((H, W, 3), np.uint8)
    img[:] = (30, 30, 30)
    cv2.rectangle(img, (200, 150), (600, 450), (200, 200, 200), -1)
    return img


def con_objeto(cx=400, cy=300):
    img = vacia()
    bgr = cv2.cvtColor(np.uint8([[[45, 200, 220]]]), cv2.COLOR_HSV2BGR)[0, 0]
    cv2.rectangle(img, (cx - 13, cy - 13), (cx + 13, cy + 13), bgr.tolist(), -1)
    return img


def instalar(secuencia):
    est = {"i": 0}

    def grab():
        img = secuencia[min(est["i"], len(secuencia) - 1)]
        est["i"] += 1
        return img, MON
    G.Finder.grab_screen = staticmethod(grab)
    return est


OBJ = {"cristal": {"mode": "unico", "sat_min": 40, "val_min": 90,
                   "min_area": 80, "max_area": 40000, "max_side": 80,
                   "frames": 1, "on_gui": False, "roi_left": 0.0,
                   "roi_right": 1.0, "roi_top": 0.0, "roi_bottom": 1.0}}


def guion(texto, keepalive=2.0):
    lineas = []
    s = G.Script(lineas.append, OBJ)
    s.interval = 0.02
    s.confirmaciones = 1
    s.sound = False
    s.keepalive = keepalive
    s.keyboard = TecladoEspia()
    s.mouse = RatonEspia()
    return s, s.load(texto), lineas


def correr(s, limite=12.0):
    s.start()
    t0 = time.perf_counter()
    while s.running and time.perf_counter() - t0 < limite:
        time.sleep(0.01)
    colgado = s.running
    s.stop()
    s._thread.join(timeout=3.0)
    return colgado


# ------------------------------------------------ 1. el clic
def p_clic_recupera_el_boton():
    print("--- 1. el clic no deja suelto el boton mantenido ---")
    instalar([con_objeto()])
    orig = G.click_at
    G.click_at = lambda m, x, y, **kw: m.acciones.append(("click_at", "left"))
    try:
        # 'esperar 3' para tener tiempo de mirar el estado MIENTRAS corre: al
        # acabar, el guion suelta todo (y eso esta bien), asi que comprobarlo
        # despues no diria nada
        s, errs, log = guion("mantener_clic\nbuscar cristal\nclic\nesperar 3\n"
                             "parar", keepalive=0)
        check("analiza", not errs, str(errs))
        s.start()
        t0 = time.perf_counter()
        visto_abajo = False
        while s.running and time.perf_counter() - t0 < 8:
            time.sleep(0.01)
            if any(a[0] == "click_at" for a in s.mouse.acciones):
                time.sleep(0.15)
                visto_abajo = "Button.left" in s.mouse.abajo
                break
        s.stop()
        s._thread.join(timeout=3.0)
        check("el boton sigue pulsado despues del clic", visto_abajo,
              str(s.mouse.acciones))
        ops = [a[0] for a in s.mouse.acciones]
        check("y la secuencia es la correcta",
              ops == ["press", "release", "click_at", "release", "press",
                      "release"], str(ops))
    finally:
        G.click_at = orig


def p_clic_recupera_la_tecla():
    print("--- 1b. el clic tambien reafirma la tecla mantenida ---")
    instalar([con_objeto()])
    orig = G.click_at
    G.click_at = lambda m, x, y, **kw: m.acciones.append(("click_at", "left"))
    try:
        s, errs, log = guion("mantener w\nbuscar cristal\nclic\nesperar 0.1\n"
                             "parar", keepalive=0)
        correr(s)
        check("lo dice en el registro",
              any("recupero lo mantenido" in l and "w" in l for l in log),
              str([l for l in log if "recupero" in l]))
        ks = [a[0] for a in s.keyboard.acciones]
        check("hubo release y press de la w tras el clic",
              ks.count("press") >= 2 and "release" in ks, str(ks))
    finally:
        G.click_at = orig


# ------------------------------------------------ 2. tras desaparecer
def p_desaparecer_reafirma():
    print("--- 2. al cerrarse la interfaz se reafirma solo ---")
    instalar([con_objeto()] * 2 + [vacia()] * 20)
    orig = G.click_at
    G.click_at = lambda m, x, y, **kw: m.acciones.append(("click_at", "left"))
    try:
        s, errs, log = guion("mantener w\nmantener_clic\nbuscar cristal 3\n"
                             "clic\ndesaparecer cristal 3\nesperar 0.1\nparar",
                             keepalive=0)
        check("analiza", not errs, str(errs))
        correr(s)
        check("dice que ha desaparecido",
              any("ha desaparecido" in l for l in log), str(log[:6]))
        check("y que recupera lo mantenido justo ahi",
              any("recupero lo mantenido" in l for l in log),
              str([l for l in log if "recupero" in l]))
        check("SIN que el guion lleve un 'reafirmar'",
              "reafirmar" not in "\n".join(log).lower()
              or all("reafirmado" not in l for l in log))
    finally:
        G.click_at = orig


# ------------------------------------------------ 3. el refuerzo
def p_refuerzo_repulsa():
    print("--- 3. el refuerzo vuelve a pulsar aunque nadie se lo pida ---")
    instalar([vacia()])
    s, errs, log = guion("mantener w\nesperar 3\nparar", keepalive=0.3)
    check("analiza", not errs, str(errs))
    s.start()
    time.sleep(1.4)
    n = s.keyboard.pulsaciones.get("w", 0)
    s.stop()
    s._thread.join(timeout=3.0)
    check("ha pulsado la w varias veces en 1.4 s", n >= 4, f"{n} pulsaciones")
    check("y lo avisa una sola vez",
          sum(1 for l in log if "refuerzo activo" in l) == 1,
          str([l for l in log if "refuerzo" in l]))


def p_refuerzo_recupera_tras_olvido():
    """La prueba que de verdad importa: el juego se olvida de la tecla a media
    ejecucion, sin que el guion haga nada, y tiene que volver sola."""
    print("--- 3b. si el juego se olvida de la W, vuelve sola ---")
    instalar([vacia()])
    s, errs, log = guion("mantener w\nesperar 4\nparar", keepalive=0.3)
    s.start()
    time.sleep(0.6)
    check("la w esta abajo", "w" in s.keyboard.abajo, str(s.keyboard.abajo))
    s.keyboard.olvidar()                 # como al abrirse el cofre
    check("y ahora el juego la ha olvidado", "w" not in s.keyboard.abajo)
    t0 = time.perf_counter()
    while "w" not in s.keyboard.abajo and time.perf_counter() - t0 < 3:
        time.sleep(0.02)
    tardo = time.perf_counter() - t0
    vuelta = "w" in s.keyboard.abajo
    s.stop()
    s._thread.join(timeout=3.0)
    check("vuelve sola, sin 'reafirmar' en el guion", vuelta,
          f"tardo {tardo:.2f} s")
    check("y en menos de un refuerzo y medio", vuelta and tardo < 0.6,
          f"{tardo:.2f} s con refuerzo cada 0.3 s")


def p_refuerzo_no_suelta_el_raton():
    """El raton NO se refuerza soltando: eso reiniciaria lo que estes picando."""
    print("--- 3c. el refuerzo no suelta el clic (romperia el picado) ---")
    instalar([vacia()])
    s, errs, log = guion("mantener_clic\nesperar 2\nparar", keepalive=0.25)
    s.start()
    time.sleep(1.2)
    sueltas = [a for a in s.mouse.acciones if a[0] == "release"]
    s.stop()
    s._thread.join(timeout=3.0)
    check("ni una suelta del raton durante el mantenido", not sueltas,
          str(s.mouse.acciones))


def p_refuerzo_no_resucita_lo_soltado():
    print("--- 3d. tras un 'soltar', el refuerzo no la vuelve a pulsar ---")
    instalar([vacia()])
    s, errs, log = guion("mantener w\nesperar 0.5\nsoltar w\nesperar 1.5\nparar",
                         keepalive=0.3)
    correr(s)
    ops = s.keyboard.acciones
    # tras el ultimo release no debe haber ningun press
    ult_release = max((i for i, a in enumerate(ops) if a[0] == "release"),
                      default=-1)
    despues = [a for a in ops[ult_release + 1:] if a[0] == "press"]
    check("no la resucita", not despues, str(ops[-6:]))
    check("y acaba sin nada pulsado", not s.keyboard.abajo and not s._teclas,
          f"{s.keyboard.abajo} / {s._teclas}")


def p_refuerzo_para_al_parar():
    print("--- 3e. el refuerzo muere con el guion ---")
    instalar([vacia()])
    s, errs, log = guion("mantener w\nesperar 30\nparar", keepalive=0.3)
    s.start()
    time.sleep(0.7)
    check("el hilo del refuerzo esta vivo", s._ka_thread is not None
          and s._ka_thread.is_alive())
    s.stop()
    s._thread.join(timeout=3.0)
    time.sleep(0.5)
    check("y muere al parar", s._ka_thread is None
          or not s._ka_thread.is_alive())
    check("dejando la w soltada", not s.keyboard.abajo, str(s.keyboard.abajo))


# ------------------------------------------------ todo junto
def p_el_caso_real():
    print("--- el caso real completo, con el juego olvidando a mitad ---")
    # el cristal aparece, se clica, desaparece; y el juego se olvida al abrirse
    instalar([con_objeto()] * 2 + [vacia()] * 40)
    orig = G.click_at
    olvido = {"hecho": False}

    def falso_click(m, x, y, **kw):
        m.acciones.append(("click_at", "left"))
    G.click_at = falso_click
    try:
        s, errs, log = guion("mantener w\n"
                             "mantener_clic\n"
                             "buscar cristal 3\n"
                             "clic\n"
                             "desaparecer cristal 3\n"
                             "esperar 3\n"
                             "parar", keepalive=0.3)
        check("analiza", not errs, str(errs))
        s.start()
        # en cuanto vea el cristal, simular que el juego olvida las teclas, y
        # despues vigilar SI VUELVE mientras el guion sigue corriendo
        t0 = time.perf_counter()
        vuelta = False
        while s.running and time.perf_counter() - t0 < 10:
            time.sleep(0.01)
            if not olvido["hecho"] and any("visto" in l for l in log):
                s.keyboard.olvidar()
                olvido["hecho"] = True
                continue
            if olvido["hecho"] and "w" in s.keyboard.abajo:
                vuelta = True
                break
        s.stop()
        s._thread.join(timeout=3.0)
        check("se simulo el olvido del juego", olvido["hecho"])
        check("la W vuelve sola pese al olvido, y sin 'reafirmar'", vuelta,
              f"tardo {time.perf_counter() - t0:.2f} s")
        check("y el clic tambien siguio mantenido",
              "Button.left" in [a[1] for a in s.mouse.acciones if a[0] == "press"],
              str(s.mouse.acciones[:4]))
        check("al final lo suelta todo", not s._teclas and not s._botones,
              f"{s._teclas} / {s._botones}")
    finally:
        G.click_at = orig


if __name__ == "__main__":
    for fn in (p_clic_recupera_el_boton, p_clic_recupera_la_tecla,
               p_desaparecer_reafirma, p_refuerzo_repulsa,
               p_refuerzo_recupera_tras_olvido, p_refuerzo_no_suelta_el_raton,
               p_refuerzo_no_resucita_lo_soltado, p_refuerzo_para_al_parar,
               p_el_caso_real):
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
