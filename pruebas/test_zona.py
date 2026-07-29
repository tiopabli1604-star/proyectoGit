# -*- coding: utf-8 -*-
"""La zona marcada con F2 nunca debe recortar lo que el usuario marco.

Llama al App.mark_zone de verdad con un objeto minimo, sin levantar Tk.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import main as G

R = []


def check(nombre, ok, extra=""):
    R.append(ok)
    print(f"  {'OK  ' if ok else 'FALLO'} {nombre}{(' — ' + extra) if extra else ''}")


class Var:
    def __init__(self, v="0"):
        self.v = v

    def get(self):
        return self.v

    def set(self, v):
        self.v = str(v)


class Stub:
    """Lo minimo que mark_zone toca."""

    def __init__(self):
        self._zone_p1 = None
        self.finder = G.Finder()
        self.var_roi_left = Var("0")
        self.var_roi_right = Var("100")
        self.var_roi_top = Var("0")
        self.var_roi_bottom = Var("100")
        self.lineas = []

    def log(self, m):
        self.lineas.append(m)

    def _apply_settings(self):
        f = self.finder
        f.roi_left = float(self.var_roi_left.get()) / 100.0
        f.roi_right = float(self.var_roi_right.get()) / 100.0
        f.roi_top = float(self.var_roi_top.get()) / 100.0
        f.roi_bottom = float(self.var_roi_bottom.get()) / 100.0

    marcar = G.App.mark_zone
    reset = G.App.reset_zone


def con_pantalla(w, h, fn):
    """Finge un monitor de w x h y un raton que se puede colocar."""
    pos = {"p": (0, 0)}
    mon = {"left": 0, "top": 0, "width": w, "height": h}

    class FakeSct:
        monitors = [mon, mon]

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    orig_mss, orig_mouse = G.mss.mss, G.MouseController
    G.mss.mss = lambda: FakeSct()
    G.MouseController = lambda: type("M", (), {"position": pos["p"]})()
    try:
        return fn(pos, mon)
    finally:
        G.mss.mss, G.MouseController = orig_mss, orig_mouse


def marcar(app, pos, p1, p2):
    pos["p"] = p1
    app.marcar()
    pos["p"] = p2
    app.marcar()


def p_contiene():
    print("--- la zona guardada contiene siempre lo marcado ---")

    def cuerpo(pos, mon):
        w, h = mon["width"], mon["height"]
        casos = [
            ((800, 340), (1125, 560), "rejilla del cofre 1920x1080"),
            ((787, 310), (1133, 748), "ventana entera del captcha"),
            ((1000, 500), (1040, 540), "una sola casilla, 40x40"),
            ((1800, 1000), (1919, 1079), "pegado al borde inferior derecho"),
            ((3, 5), (60, 70), "pegado a la esquina 0,0"),
        ]
        for p1, p2, nombre in casos:
            app = Stub()
            marcar(app, pos, p1, p2)
            f = app.finder
            L = f.roi_left * w
            Rr = f.roi_right * w
            T = f.roi_top * h
            B = f.roi_bottom * h
            x0, x1 = min(p1[0], p2[0]), max(p1[0], p2[0])
            y0, y1 = min(p1[1], p2[1]), max(p1[1], p2[1])
            # que no se haya rechazado por pequeña y caido a pantalla completa,
            # porque entonces "contiene lo marcado" no demostraria nada
            acepto = (f.roi_left, f.roi_right, f.roi_top,
                      f.roi_bottom) != (0.0, 1.0, 0.0, 1.0)
            dentro = L <= x0 and Rr >= x1 and T <= y0 and B >= y1
            cabe = 0.0 <= f.roi_left and f.roi_right <= 1.0 \
                and 0.0 <= f.roi_top and f.roi_bottom <= 1.0
            check(nombre, acepto and dentro and cabe,
                  f"marcado x {x0}..{x1} y {y0}..{y1} -> guardado "
                  f"x {L:.0f}..{Rr:.0f} y {T:.0f}..{B:.0f}"
                  + ("" if acepto else "  [RECHAZADA: cayo a pantalla completa]")
                  + ("" if cabe else "  [SE SALE DE LA PANTALLA]"))
    con_pantalla(1920, 1080, cuerpo)


def p_orden_esquinas():
    print("--- da igual el orden de las dos esquinas ---")

    def cuerpo(pos, mon):
        ref = None
        for p1, p2 in (((800, 340), (1125, 560)), ((1125, 560), (800, 340)),
                       ((1125, 340), (800, 560)), ((800, 560), (1125, 340))):
            app = Stub()
            marcar(app, pos, p1, p2)
            f = app.finder
            act = (f.roi_left, f.roi_right, f.roi_top, f.roi_bottom)
            if ref is None:
                ref = act
            check(f"{p1} -> {p2}", act == ref, f"{act}")
    con_pantalla(1920, 1080, cuerpo)


def p_demasiado_pequena():
    print("--- una zona diminuta se rechaza y no toca los ajustes ---")

    def cuerpo(pos, mon):
        app = Stub()
        marcar(app, pos, (900, 500), (905, 503))
        f = app.finder
        ok = (f.roi_left, f.roi_right, f.roi_top, f.roi_bottom) == (0.0, 1.0,
                                                                    0.0, 1.0)
        check("sigue en pantalla completa", ok,
              f"{(f.roi_left, f.roi_right, f.roi_top, f.roi_bottom)}")
        check("lo dice en el registro",
              any("demasiado peque" in l for l in app.lineas),
              app.lineas[-1] if app.lineas else "")
        check("no se queda a medias esperando la 2a esquina",
              app._zone_p1 is None)
    con_pantalla(1920, 1080, cuerpo)


def p_primera_pulsacion():
    print("--- la primera pulsacion no cambia nada todavia ---")

    def cuerpo(pos, mon):
        app = Stub()
        pos["p"] = (800, 340)
        app.marcar()
        f = app.finder
        ok = (f.roi_left, f.roi_right) == (0.0, 1.0) and app._zone_p1 == (800, 340)
        check("guarda la esquina y espera", ok, f"esquina={app._zone_p1}")
    con_pantalla(1920, 1080, cuerpo)


def p_reset():
    print("--- 'Toda la pantalla' deshace la zona ---")

    def cuerpo(pos, mon):
        app = Stub()
        marcar(app, pos, (800, 340), (1125, 560))
        app.reset()
        f = app.finder
        check("vuelve a 0..100 en los dos ejes",
              (f.roi_left, f.roi_right, f.roi_top, f.roi_bottom) == (0.0, 1.0,
                                                                     0.0, 1.0))
        pos["p"] = (900, 500)
        app.marcar()
        check("y olvida una esquina a medio marcar",
              app._zone_p1 == (900, 500))
    con_pantalla(1920, 1080, cuerpo)


def p_otras_resoluciones():
    print("--- otras resoluciones ---")
    for w, h in ((2560, 1440), (1366, 768), (3840, 2160)):
        def cuerpo(pos, mon, w=w, h=h):
            app = Stub()
            p1 = (int(w * 0.417), int(h * 0.315))
            p2 = (int(w * 0.586), int(h * 0.519))
            marcar(app, pos, p1, p2)
            f = app.finder
            ok = (f.roi_left * w <= p1[0] and f.roi_right * w >= p2[0]
                  and f.roi_top * h <= p1[1] and f.roi_bottom * h >= p2[1])
            check(f"{w}x{h}", ok,
                  f"marcado {p1}..{p2} -> {f.roi_left * w:.0f}.."
                  f"{f.roi_right * w:.0f} / {f.roi_top * h:.0f}.."
                  f"{f.roi_bottom * h:.0f}")
        con_pantalla(w, h, cuerpo)


if __name__ == "__main__":
    for fn in (p_primera_pulsacion, p_contiene, p_orden_esquinas,
               p_demasiado_pequena, p_reset, p_otras_resoluciones):
        fn()
        print()
    fallos = R.count(False)
    print(f"{len(R)} comprobaciones, "
          + ("TODO OK" if not fallos else f"{fallos} FALLO(S)"))
    sys.exit(1 if fallos else 0)
