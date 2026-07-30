# -*- coding: utf-8 -*-
"""Alineacion de la camara con el ancla de la vista.

La prueba central monta un mundo falso: una imagen grande de la que se ve una
ventana, y esa ventana se desplaza cuando se "mueve el raton". Con eso se puede
comprobar que la alineacion converge de verdad, que aprende sola la sensibilidad
y el signo, y que se rinde cuando no puede.
"""
import os
import sys

import cv2
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import main as G

W, H = 960, 540
MON = {"left": 0, "top": 0, "width": W, "height": H}

R = []


def check(nombre, ok, extra=""):
    R.append(ok)
    extra = str(extra) if extra else ""
    print(f"  {'OK  ' if ok else 'FALLO'} {nombre}{(' — ' + extra) if extra else ''}")


class MundoFalso:
    """Un 'juego': una imagen grande de la que se ve una ventana movil.

    sens = pixeles que se desplaza la vista por unidad de raton. El signo se
    puede invertir para comprobar que la alineacion lo aprende y no lo supone.
    """

    def __init__(self, sens=0.5, signo_x=-1, signo_y=-1, semilla=3):
        rng = np.random.RandomState(semilla)
        self.mundo = rng.randint(0, 255, (H * 3, W * 3, 3)).astype(np.uint8)
        self.mundo = cv2.GaussianBlur(self.mundo, (7, 7), 0)
        for _ in range(260):                      # formas reconocibles
            x, y = rng.randint(0, W * 3 - 90), rng.randint(0, H * 3 - 90)
            c = tuple(int(v) for v in rng.randint(0, 255, 3))
            if rng.rand() < 0.5:
                cv2.rectangle(self.mundo, (x, y), (x + rng.randint(30, 90),
                                                   y + rng.randint(30, 90)), c, -1)
            else:
                cv2.circle(self.mundo, (x, y), rng.randint(15, 45), c, -1)
        self.cx, self.cy = W, H                   # esquina de la ventana visible
        self.sens = sens
        self.signo_x = signo_x
        self.signo_y = signo_y
        self.movimientos = 0

    def instalar(self):
        def grab():
            # en horizontal el mundo da la vuelta, como girar sobre uno mismo;
            # en vertical se topa, como el limite de mirar arriba y abajo
            mh, mw = self.mundo.shape[:2]
            x = int(self.cx) % mw
            y = int(np.clip(self.cy, 0, mh - H))
            if x + W <= mw:
                vista = self.mundo[y:y + H, x:x + W]
            else:
                vista = np.concatenate(
                    [self.mundo[y:y + H, x:], self.mundo[y:y + H, :x + W - mw]],
                    axis=1)
            return np.ascontiguousarray(vista), MON
        G.Finder.grab_screen = staticmethod(grab)

    def _limitar(self):
        # el juego CLAVA el angulo en el limite, no acumula por dentro: si estas
        # mirando al suelo del todo y sigues bajando, no se guarda un "mas
        # abajo" imaginario, asi que subir un poco mueve la vista al instante
        tope = self.mundo.shape[0] - H
        self.cy = float(np.clip(self.cy, 0, tope))

    def mover(self, dx, dy):
        """Como turn_camera: mover el raton desplaza la vista."""
        self.movimientos += 1
        self.cx -= self.signo_x * dx * self.sens
        self.cy -= self.signo_y * dy * self.sens
        self._limitar()

    def desviar(self, px, py):
        self.cx += px
        self.cy += py
        self._limitar()


def p_ancla_ida_y_vuelta():
    print("--- guardar y recuperar el ancla en texto ---")
    m = MundoFalso()
    m.instalar()
    ancla, pos = G.capturar_ancla()
    check("recorta el centro", ancla.shape[0] == int(H * G.ANCLA_FRAC)
          and ancla.shape[1] == int(W * G.ANCLA_FRAC),
          f"{ancla.shape[1]}x{ancla.shape[0]}")
    check("y dice donde estaba", pos == ((W - ancla.shape[1]) // 2,
                                        (H - ancla.shape[0]) // 2), str(pos))
    txt = G.ancla_a_texto(ancla)
    check("se serializa", isinstance(txt, str) and len(txt) > 100,
          f"{len(txt) if txt else 0} caracteres")
    vuelta = G.texto_a_ancla(txt)
    check("y vuelve identica", np.array_equal(vuelta, ancla),
          f"{vuelta.shape} vs {ancla.shape}")
    try:
        G.texto_a_ancla("esto no es una imagen")
        check("un ancla corrupta avisa", False, "no lanzo error")
    except Exception as exc:
        check("un ancla corrupta avisa", True, type(exc).__name__)


def p_localizar():
    print("--- localizar el ancla en la vista ---")
    m = MundoFalso()
    m.instalar()
    ancla, pos = G.capturar_ancla()
    loc, score = G.localizar_ancla(ancla)
    check("sin moverse, la encuentra donde estaba",
          loc == pos and score > 0.95, f"{loc} vs {pos}, parecido {score:.3f}")
    m.desviar(37, -21)
    loc2, score2 = G.localizar_ancla(ancla)
    check("desviada, la encuentra desplazada justo eso",
          loc2 == (pos[0] - 37, pos[1] + 21) and score2 > 0.95,
          f"{loc2}, esperado {(pos[0] - 37, pos[1] + 21)}, "
          f"parecido {score2:.3f}")


def p_alinea():
    print("--- alinea desde varios desvios ---")
    for px, py in ((60, 0), (0, -45), (-120, 80), (200, -150), (7, 3)):
        m = MundoFalso()
        m.instalar()
        ancla, pos = G.capturar_ancla()
        m.desviar(px, py)
        ok, detalle = G.alinear_camara(ancla, pos, mover=m.mover, espera=0.0)
        loc, _s = G.localizar_ancla(ancla)
        err = (pos[0] - loc[0], pos[1] - loc[1]) if loc else None
        check(f"desvio ({px}, {py})",
              ok and err is not None and abs(err[0]) <= 4 and abs(err[1]) <= 4,
              f"{detalle} | queda {err}")


def p_aprende_el_signo():
    print("--- aprende el signo, no lo supone ---")
    for sx, sy in ((-1, -1), (1, 1), (-1, 1), (1, -1)):
        m = MundoFalso(signo_x=sx, signo_y=sy)
        m.instalar()
        ancla, pos = G.capturar_ancla()
        m.desviar(90, -70)
        ok, detalle = G.alinear_camara(ancla, pos, mover=m.mover, espera=0.0)
        loc, _s = G.localizar_ancla(ancla)
        err = (pos[0] - loc[0], pos[1] - loc[1]) if loc else None
        check(f"signos ({sx:+d}, {sy:+d})", ok, f"{detalle} | queda {err}")


def p_aprende_la_sensibilidad():
    print("--- y la sensibilidad, sea la que sea ---")
    for sens in (0.2, 0.5, 1.0, 2.5):
        m = MundoFalso(sens=sens)
        m.instalar()
        ancla, pos = G.capturar_ancla()
        m.desviar(110, -60)
        ok, detalle = G.alinear_camara(ancla, pos, mover=m.mover, espera=0.0)
        check(f"sensibilidad {sens}", ok, detalle)


def p_pocas_correcciones():
    print("--- converge en pocos movimientos ---")
    m = MundoFalso()
    m.instalar()
    ancla, pos = G.capturar_ancla()
    m.desviar(150, -110)
    antes = m.movimientos
    ok, detalle = G.alinear_camara(ancla, pos, mover=m.mover, espera=0.0)
    usados = m.movimientos - antes
    check("alinea", ok, detalle)
    # 2 de calibracion + las correcciones
    check("con pocos giros en total", usados <= 6, f"{usados} giros")


def p_busca_girando():
    """Lo que pidio el usuario: que no haya que colocar la camara a mano."""
    print("--- la busca girando si esta mirando a otro lado ---")
    for nombre, (px, py) in (("media vuelta", (2400, 0)),
                             ("de espaldas", (4300, 0)),
                             ("mirando al suelo", (900, 700)),
                             ("mirando arriba", (1500, -700))):
        m = MundoFalso()
        m.instalar()
        ancla, pos = G.capturar_ancla()
        m.desviar(px, py)
        # de entrada no deberia reconocerla
        _l, s0 = G.localizar_ancla(ancla)
        ok, detalle = G.alinear_camara(ancla, pos, mover=m.mover, espera=0.0,
                                       log=lambda _m: None)
        loc, _s = G.localizar_ancla(ancla)
        err = (pos[0] - loc[0], pos[1] - loc[1]) if loc else None
        check(f"{nombre} (parecido inicial {s0:.2f})",
              ok and err is not None and abs(err[0]) <= 4 and abs(err[1]) <= 4,
              f"{detalle[:70]} | queda {err}")


def p_sin_buscar_se_niega():
    print("--- con buscar=False se niega, como antes ---")
    m = MundoFalso()
    m.instalar()
    ancla, pos = G.capturar_ancla()
    m.desviar(2400, 0)
    ok, detalle = G.alinear_camara(ancla, pos, mover=m.mover, espera=0.0,
                                   buscar=False)
    check("no alinea", not ok)
    check("y dice que no reconoce la vista", "no reconozco la vista" in detalle,
          detalle[:70])
    check("sin haber movido nada", m.movimientos == 0,
          f"{m.movimientos} movimientos")


def p_no_esta_en_ningun_sitio():
    print("--- si el ancla no esta en ningun sitio, lo dice tras buscar ---")
    m = MundoFalso()
    m.instalar()
    ancla, _pos = G.capturar_ancla()
    # otro mundo completamente distinto: por mucho que gire no la vera
    m2 = MundoFalso(semilla=99)
    m2.instalar()
    ok, detalle = G.alinear_camara(ancla, _pos, mover=m2.mover, espera=0.0,
                                   log=lambda _m: None)
    check("no alinea", not ok)
    check("dice que ha girado y no la ha visto",
          "no he reconocido la vista" in detalle
          and "alturas" in detalle, detalle[:90])
    check("y da el mejor parecido que vio, para poder juzgar",
          "lo más parecido fue" in detalle, detalle[-70:])
    check("y ha girado de verdad buscandola", m2.movimientos > 20,
          f"{m2.movimientos} movimientos")


def p_camara_que_no_gira():
    print("--- si la camara no responde, lo dice en vez de insistir ---")
    m = MundoFalso()
    m.instalar()
    ancla, pos = G.capturar_ancla()
    m.desviar(50, 0)
    ok, detalle = G.alinear_camara(ancla, pos, mover=lambda dx, dy: None,
                                   espera=0.0)
    check("no alinea", not ok)
    check("y sospecha del raton capturado",
          "no se ha movido" in detalle and "ratón capturado" in detalle,
          detalle[:100])


def p_camara_lenta_y_rapida():
    print("--- busca igual con sensibilidad muy baja o muy alta ---")
    for sens in (0.15, 2.0):
        m = MundoFalso(sens=sens)
        m.instalar()
        ancla, pos = G.capturar_ancla()
        m.desviar(2200, 0)
        ok, detalle = G.alinear_camara(ancla, pos, mover=m.mover, espera=0.0,
                                       log=lambda _m: None)
        loc, _s = G.localizar_ancla(ancla)
        err = (pos[0] - loc[0], pos[1] - loc[1]) if loc else None
        check(f"sensibilidad {sens}", ok and err and abs(err[0]) <= 4,
              f"{detalle[:60]} | queda {err}")


def p_formato_del_archivo():
    print("--- el ancla viaja dentro del .macro.json ---")
    import json
    m = MundoFalso()
    m.instalar()
    ancla, pos = G.capturar_ancla()
    ruta = os.path.join(G.APP_DIR, "_prueba_ancla.macro.json")
    try:
        datos = {"version": 3, "relative": True,
                 "events": [{"t": 0.0, "e": "mr", "dx": 5, "dy": 5}],
                 "ancla": G.ancla_a_texto(ancla), "ancla_pos": list(pos)}
        with open(ruta, "w", encoding="utf-8") as f:
            json.dump(datos, f)
        with open(ruta, encoding="utf-8-sig") as f:
            leido = json.load(f)
        check("version 3", leido["version"] == 3)
        vuelta = G.texto_a_ancla(leido["ancla"])
        check("el ancla sobrevive al archivo", np.array_equal(vuelta, ancla))
        check("y su posicion tambien", tuple(leido["ancla_pos"]) == pos,
              str(leido["ancla_pos"]))
        kb = os.path.getsize(ruta) / 1024
        check("sin engordar el archivo de forma absurda", kb < 400,
              f"{kb:.0f} KB")
    finally:
        if os.path.exists(ruta):
            os.remove(ruta)


if __name__ == "__main__":
    for fn in (p_ancla_ida_y_vuelta, p_localizar, p_alinea, p_aprende_el_signo,
               p_aprende_la_sensibilidad, p_pocas_correcciones,
               p_busca_girando, p_sin_buscar_se_niega, p_no_esta_en_ningun_sitio,
               p_camara_que_no_gira, p_camara_lenta_y_rapida,
               p_formato_del_archivo):
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
