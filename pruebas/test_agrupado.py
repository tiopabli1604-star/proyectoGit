# -*- coding: utf-8 -*-
"""El agrupado del movimiento: mismo recorrido, muchos menos eventos."""
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


def p_agrupa():
    print("--- 600 informes seguidos se agrupan sin perder recorrido ---")
    rec = G.Recorder()
    rec.relative = True
    rec.reforzar = False
    rec.start()
    if rec.raw_error:
        print("   (sin raw input, omitido)")
        return
    time.sleep(0.25)
    enviados = 0
    for _ in range(600):                     # como un raton de 1000 Hz
        G.move_relative(2, 1)
        enviados += 1
    time.sleep(0.5)
    ev = rec.stop()
    mr = [e for e in ev if e["e"] == "mr"]
    tot_x = sum(e["dx"] for e in mr)
    tot_y = sum(e["dy"] for e in mr)
    print(f"   inyectados {enviados} informes ({enviados*2}, {enviados*1})")
    print(f"   guardados  {len(mr)} eventos ({tot_x}, {tot_y})")
    check("el recorrido total se conserva entero",
          (tot_x, tot_y) == (enviados * 2, enviados * 1),
          f"({tot_x}, {tot_y}) vs ({enviados*2}, {enviados})")
    check("y se guardan muchos menos eventos", len(mr) < enviados / 2,
          f"{len(mr)} de {enviados}")
    if len(mr) > 1:
        huecos = [mr[i+1]["t"] - mr[i]["t"] for i in range(len(mr)-1)]
        check("separados al menos el intervalo minimo",
              min(huecos) >= G.Recorder.MOVE_MIN_INTERVAL * 0.9,
              f"minimo {min(huecos)*1000:.1f} ms")


def p_no_pierde_la_cola():
    print("--- lo que quede sin emitir al parar no se pierde ---")
    rec = G.Recorder()
    rec.relative = True
    rec.reforzar = False
    rec.start()
    if rec.raw_error:
        print("   (sin raw input, omitido)")
        return
    time.sleep(0.25)
    G.move_relative(50, 0)      # uno solo, justo antes de parar
    time.sleep(0.05)
    ev = rec.stop()
    mr = [e for e in ev if e["e"] == "mr"]
    check("el ultimo movimiento esta", sum(e["dx"] for e in mr) == 50,
          f"{sum(e['dx'] for e in mr)} en {len(mr)} eventos")


if __name__ == "__main__":
    for fn in (p_agrupa, p_no_pierde_la_cola):
        try:
            fn()
        except Exception:
            import traceback
            traceback.print_exc()
            R.append(False)
        print()
    print(f"{len(R)} comprobaciones, "
          + ("TODO OK" if not R.count(False) else f"{R.count(False)} FALLO(S)"))
    sys.exit(1 if R.count(False) else 0)
