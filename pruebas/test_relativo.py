# -*- coding: utf-8 -*-
"""Movimiento relativo para juegos en primera persona.

La prueba central es de ida y vuelta de verdad: se graba con el receptor de raw
input mientras se inyectan deltas conocidos, y luego se reproduce la macro
grabada mientras otro receptor escucha. Lo que sale tiene que ser identico a lo
que entro, delta por delta y en el mismo orden. Eso es lo que garantiza que en
el juego la camara acabe mirando donde miraba.
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import main as G

R = []


def check(nombre, ok, extra=""):
    R.append(ok)
    extra = str(extra) if extra else ""
    print(f"  {'OK  ' if ok else 'FALLO'} {nombre}{(' — ' + extra) if extra else ''}")


def escuchar(fn, espera=0.35):
    """Ejecuta fn() con un receptor de raw input puesto. Devuelve los deltas."""
    recibidos = []
    lis = G.RawMouseListener(lambda dx, dy: recibidos.append((dx, dy)))
    if not lis.start():
        raise RuntimeError(f"no arranco el raw input: {lis.error}")
    try:
        time.sleep(0.25)
        fn()
        time.sleep(espera)
    finally:
        lis.stop()
    return recibidos


SECUENCIA = [(12, 0), (0, -8), (-5, 4), (33, 21), (1, -1), (-40, 0), (0, 60)]


def p_inyeccion_exacta():
    print("--- lo inyectado llega al raw input sin tocarse ---")
    def inyectar():
        for dx, dy in SECUENCIA:
            G.move_relative(dx, dy)
            time.sleep(0.04)
    got = escuchar(inyectar)
    check("llegan todos y en orden", got == SECUENCIA, f"{got}")
    # el recorrido total en valor absoluto: si Windows aplicara la aceleracion
    # del puntero a lo que ve el raw input, este numero saldria inflado. (La
    # suma con signo no serviria: en esta secuencia da 1 de pura casualidad.)
    rec_got = sum(abs(d[0]) + abs(d[1]) for d in got)
    rec_esp = sum(abs(d[0]) + abs(d[1]) for d in SECUENCIA)
    check("la aceleracion del puntero no los toca", rec_got == rec_esp,
          f"recorrido {rec_got} px vs {rec_esp} esperados")


def p_grabar():
    print("--- el grabador en relativo guarda eventos 'mr' ---")
    rec = G.Recorder()
    rec.relative = True
    rec.start()
    check("arranco el raw input", rec.raw_error is None, str(rec.raw_error))
    time.sleep(0.25)
    for dx, dy in SECUENCIA:
        G.move_relative(dx, dy)
        time.sleep(0.04)
    time.sleep(0.35)
    ev = rec.stop()
    mr = [e for e in ev if e["e"] == "mr"]
    mm = [e for e in ev if e["e"] == "mm"]
    check("no guarda posiciones absolutas", not mm, f"{len(mm)} eventos 'mm'")
    check("guarda un 'mr' por movimiento", len(mr) == len(SECUENCIA),
          f"{len(mr)} de {len(SECUENCIA)}")
    check("con los deltas exactos",
          [(e["dx"], e["dy"]) for e in mr] == SECUENCIA,
          str([(e["dx"], e["dy"]) for e in mr]))
    check("y con tiempos crecientes",
          all(mr[i]["t"] <= mr[i + 1]["t"] for i in range(len(mr) - 1)))
    return ev


def p_ida_y_vuelta():
    print("--- ida y vuelta: grabar, reproducir y comparar ---")
    rec = G.Recorder()
    rec.relative = True
    rec.start()
    if rec.raw_error:
        check("raw input disponible", False, str(rec.raw_error))
        return
    time.sleep(0.25)
    for dx, dy in SECUENCIA:
        G.move_relative(dx, dy)
        time.sleep(0.05)
    time.sleep(0.35)
    grabado = rec.stop()
    originales = [(e["dx"], e["dy"]) for e in grabado if e["e"] == "mr"]

    p = G.Player()
    reproducido = escuchar(lambda: p.play_sync(grabado), espera=0.5)
    check("se reproduce lo mismo, delta por delta",
          reproducido == originales,
          f"grabado {originales} / reproducido {reproducido}")
    check("y el desplazamiento total cuadra",
          (sum(d[0] for d in reproducido), sum(d[1] for d in reproducido))
          == (sum(d[0] for d in originales), sum(d[1] for d in originales)))


def p_no_mueve_el_cursor_en_los_clics():
    print("--- en una macro relativa un clic no recoloca el cursor ---")
    m = G.MouseController()
    m.position = (500, 500)
    time.sleep(0.15)
    eventos = [
        {"t": 0.0, "e": "mr", "dx": 5, "dy": 5},
        {"t": 0.05, "e": "mc", "x": 1500, "y": 900, "b": "left", "d": True},
        {"t": 0.10, "e": "mc", "x": 1500, "y": 900, "b": "left", "d": False},
    ]
    p = G.Player()
    p.play_sync(eventos)
    time.sleep(0.2)
    pos = m.position
    check("el cursor NO ha saltado a (1500, 900)",
          abs(pos[0] - 1500) > 100 or abs(pos[1] - 900) > 100, str(pos))

    # y en una macro absoluta si tiene que recolocar
    m.position = (500, 500)
    time.sleep(0.15)
    abs_ev = [
        {"t": 0.0, "e": "mm", "x": 700, "y": 600},
        {"t": 0.05, "e": "mc", "x": 700, "y": 600, "b": "left", "d": True},
        {"t": 0.10, "e": "mc", "x": 700, "y": 600, "b": "left", "d": False},
    ]
    p2 = G.Player()
    p2.play_sync(abs_ev)
    time.sleep(0.2)
    pos2 = m.position
    check("pero en una absoluta si va a su sitio",
          abs(pos2[0] - 700) < 6 and abs(pos2[1] - 600) < 6, str(pos2))


def p_deteccion_de_modo():
    print("--- se detecta el modo por el contenido de la macro ---")
    check("con 'mr' es relativa",
          G.Player.es_relativa([{"e": "mr", "dx": 1, "dy": 1, "t": 0}]) is True)
    check("solo con 'mm' no lo es",
          G.Player.es_relativa([{"e": "mm", "x": 1, "y": 1, "t": 0}]) is False)
    check("una macro vieja de solo teclas tampoco",
          G.Player.es_relativa([{"e": "kd", "k": "k:shift", "t": 0}]) is False)
    check("vacia tampoco", G.Player.es_relativa([]) is False)
    check("mezclada cuenta como relativa",
          G.Player.es_relativa([{"e": "mm", "x": 1, "y": 1, "t": 0},
                                {"e": "mr", "dx": 1, "dy": 1, "t": 0.1}]) is True)


def p_absoluto_sigue_igual():
    print("--- el modo absoluto no se ha roto ---")
    rec = G.Recorder()
    rec.relative = False
    rec.start()
    check("no arranca el raw input si no hace falta", rec._raw is None)
    m = G.MouseController()
    time.sleep(0.15)
    for x in range(400, 460, 12):
        m.position = (x, 400)
        time.sleep(0.05)
    time.sleep(0.2)
    ev = rec.stop()
    mm = [e for e in ev if e["e"] == "mm"]
    mr = [e for e in ev if e["e"] == "mr"]
    check("graba posiciones absolutas", len(mm) > 0, f"{len(mm)} eventos 'mm'")
    check("y ningun 'mr'", not mr, f"{len(mr)}")


def p_archivo():
    print("--- el archivo guarda y recupera el modo ---")
    ruta = os.path.join(G.APP_DIR, "_prueba_rel.macro.json")
    eventos = [{"t": 0.0, "e": "mr", "dx": 3, "dy": -4}]
    try:
        with open(ruta, "w", encoding="utf-8") as f:
            json.dump({"version": 2, "relative": True, "events": eventos}, f)
        with open(ruta, encoding="utf-8-sig") as f:
            data = json.load(f)
        check("version 2", data["version"] == 2)
        check("marca relative", data["relative"] is True)
        check("y al cargarlo se deduce igual",
              G.Player.es_relativa(data["events"]) is True)
    finally:
        if os.path.exists(ruta):
            os.remove(ruta)


def p_velocidad():
    print("--- reproducir al doble de velocidad no cambia el desplazamiento ---")
    PASO = 0.08
    duracion = (len(SECUENCIA) - 1) * PASO
    grabado = [{"t": i * PASO, "e": "mr", "dx": d[0], "dy": d[1]}
               for i, d in enumerate(SECUENCIA)]

    def medir(speed):
        """Cronometra SOLO la reproduccion, no las esperas de la prueba."""
        marca = {}

        def reproducir():
            p = G.Player()
            t0 = time.perf_counter()
            p.play_sync(grabado, speed=speed)
            marca["t"] = time.perf_counter() - t0
        got = escuchar(reproducir, espera=0.3)
        return got, marca["t"]

    g1, t1 = medir(1.0)
    g2, t2 = medir(2.0)
    check("a velocidad normal llegan todos", g1 == SECUENCIA, str(g1))
    check("al doble tambien llegan todos, sin perder ninguno",
          g2 == SECUENCIA, str(g2))
    check("a velocidad normal tarda lo grabado",
          abs(t1 - duracion) < 0.12, f"{t1:.3f} s de {duracion:.3f} s grabados")
    check("al doble tarda la mitad",
          abs(t2 - duracion / 2) < 0.12,
          f"{t2:.3f} s, esperado ~{duracion / 2:.3f} s")
    check("y es de verdad mas rapido", t2 < t1 * 0.75,
          f"{t2:.3f} s vs {t1:.3f} s")


if __name__ == "__main__":
    for fn in (p_inyeccion_exacta, p_grabar, p_ida_y_vuelta,
               p_no_mueve_el_cursor_en_los_clics, p_deteccion_de_modo,
               p_absoluto_sigue_igual, p_archivo, p_velocidad):
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
