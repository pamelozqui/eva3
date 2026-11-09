#!/usr/bin/env python3
"""
==========================================================================================
ATRAPANIEBLAS INTELIGENTE · UNIDAD 3 · CONTROLADOR ÚNICO VIRTUAL (Raspbian)
Hace el papel del ESP32: sensores simulados + decisión de riego + relé, conectado a AWS IoT Core.
==========================================================================================
 SEGURIDAD: MQTT sobre TLS (8883) validando la CA de Amazon y con certificado X.509 propio; lecturas firmadas
            con HMAC-SHA256; órdenes aceptadas solo si la firma es válida, son recientes y no se repiten.
 REGLA (evaluación 1): relé ON si humedad >= 90 %, estanque >= 15 % y es de noche.
 CONTINUIDAD: reconexión automática con espera creciente (2 -> 60 s), cola de lecturas sin conexión,
              el riego sigue en modo automático local aunque se caiga la red.

 Comandos (escríbelos en la terminal mientras corre):
   h 95      humedad (0-100)          n 50     nivel del estanque (0-100)
   l 0|1     luz: 0 = noche, 1 = día  red      corta / restablece la red (prueba de contingencia)
   estado    muestra los valores      q        salir
==========================================================================================
"""
import hmac
import hashlib
import json
import os
import random
import ssl
import sys
import threading
import time
from collections import deque

import paho.mqtt.client as mqtt

import config as cfg

# ---------- estado ----------
sim = {"humedad": 92.0, "nivel": 60, "luz": 0}       # sensores simulados (luz 0 = noche)
rele_on = False
manual_hasta = None            # instante (monotonic) hasta el que vale una orden manual
ultima_orden = (0, 0)          # (t, n) de la última orden aceptada
seq = 0
cola = deque(maxlen=cfg.MAX_COLA)
conectado = False
caida_simulada = False
sin_conexion_desde = time.monotonic()
aviso_contingencia = False
client = None
lock = threading.RLock()
salir = False

try:                                                   # relé real si hay GPIO (opcional)
    from gpiozero import OutputDevice
    _rele_gpio = OutputDevice(cfg.GPIO_RELE, active_high=False, initial_value=False)
except Exception:
    _rele_gpio = None


# ==================================================
# SEGURIDAD: firmas y verificación de órdenes
# ==================================================

def firmar(texto):
    return hmac.new(cfg.HMAC_SECRET.encode(), texto.encode(), hashlib.sha256).hexdigest()


def firma_lectura(s, t, h10, nivel, luz):
    # Mismo formato que servidor.py (humedad en décimas, entero)
    return firmar("%d|%d|%d|%d|%d" % (s, t, h10, nivel, 1 if luz else 0))


def verificar_orden(texto, ahora, ult):
    """Devuelve (ok, motivo, accion, t, n)."""
    try:
        d = json.loads(texto)
        accion, t, n, firma = str(d["accion"]), int(d["t"]), int(d["n"]), str(d["firma"])
    except Exception:
        return (False, "mensaje inválido", None, 0, 0)
    if not hmac.compare_digest(firma, firmar("%s|%d|%d" % (accion, t, n))):
        return (False, "firma HMAC inválida, orden descartada", None, 0, 0)
    if abs(ahora - t) > cfg.VENTANA_ORDEN_S:
        return (False, "orden vencida, descartada", None, 0, 0)
    if (t, n) <= ult:
        return (False, "orden repetida, descartada", None, 0, 0)
    if accion not in ("ON", "OFF"):
        return (False, "acción desconocida", None, 0, 0)
    return (True, "ok", accion, t, n)


# ==================================================
# DECISIÓN Y ACTUADOR
# ==================================================

def debe_regar(humedad, nivel, es_noche):
    return humedad >= cfg.UMBRAL_HUMEDAD and nivel >= cfg.UMBRAL_ESTANQUE and es_noche


def manual_activo():
    return manual_hasta is not None and time.monotonic() < manual_hasta


def aplicar_rele(on):
    global rele_on
    rele_on = on
    if _rele_gpio:
        _rele_gpio.on() if on else _rele_gpio.off()
    print("[RELÉ]", "ACTIVADO (bomba encendida)" if on else "DESACTIVADO (bomba apagada)")


# ==================================================
# MQTT (TLS)
# ==================================================

def publicar(topic, payload):
    """True si se entregó al cliente MQTT estando conectado."""
    if not conectado or client is None:
        return False
    info = client.publish(topic, payload, qos=1)
    return info.rc == mqtt.MQTT_ERR_SUCCESS


def publicar_estado():
    payload = json.dumps({"riego": "ON" if rele_on else "OFF", "modo": "manual" if manual_activo() else "auto"})
    if publicar(cfg.TOPIC_ESTADO, payload):
        print("[MQTT -> ESTADO]", payload)


def json_lectura(l):
    t, s, h10, nivel, luz = l
    return '{"t":%d,"seq":%d,"humedad":%.1f,"nivel":%d,"luz":%s,"firma":"%s"}' % (
        t, s, h10 / 10, nivel, "true" if luz else "false", firma_lectura(s, t, h10, nivel, luz))


def enviar_lectura(l):
    payload = json_lectura(l)
    ok = publicar(cfg.TOPIC_DATOS, payload)
    if ok:
        print("[MQTT -> DATOS]", payload)
    return ok


def vaciar_cola():
    enviadas = 0
    with lock:
        while cola and conectado and enviadas < 5:
            if not enviar_lectura(cola[0]):
                break
            cola.popleft()
            enviadas += 1
    if enviadas:
        print("[COLA] Sincronizadas %d lecturas, pendientes %d" % (enviadas, len(cola)))


def al_conectar(c, ud, flags, rc, props=None):
    global conectado, sin_conexion_desde, aviso_contingencia
    if rc == 0:
        conectado, aviso_contingencia = True, False
        c.subscribe(cfg.TOPIC_CMD, qos=1)
        print("[MQTT] Conectado (TLS) a AWS IoT Core y suscrito a", cfg.TOPIC_CMD)
        publicar_estado()
    else:
        print("[ERROR MQTT] AWS rechazó la conexión:", rc)


def al_desconectar(c, ud, flags, rc, props=None):
    global conectado, sin_conexion_desde
    if conectado:
        sin_conexion_desde = time.monotonic()
        print("[MQTT] Conexión perdida. Reconectando automáticamente (2 -> 60 s)...")
    conectado = False


def al_fallar(c, ud, *a):
    print("[ERROR MQTT] No se pudo conectar. Revisa: endpoint, certificados, hora del sistema e Internet. Reintentando...")


def al_recibir(c, ud, msg):
    """Orden de la app (vía AWS IoT Core). Solo se acepta si pasa las 3 verificaciones de seguridad."""
    global manual_hasta, ultima_orden
    texto = msg.payload.decode(errors="replace")
    print("[MQTT <- ORDEN]", texto)
    ok, motivo, accion, t, n = verificar_orden(texto, time.time(), ultima_orden)
    if not ok:
        print("[SEGURIDAD]", motivo)
        return
    ultima_orden = (t, n)
    manual_hasta = time.monotonic() + cfg.MANUAL_MAX_S
    print("[OK] Orden válida:", accion, "(manual por %d s)" % cfg.MANUAL_MAX_S)
    aplicar_rele(accion == "ON")
    publicar_estado()


def revisar_certificados():
    for ruta in (cfg.CA, cfg.CERT, cfg.LLAVE):
        if not os.path.isfile(ruta):
            sys.exit("Falta el archivo %s (cópialo a la carpeta certs/)" % ruta)
        if "PEGAR_AQUI" in open(ruta).read():
            sys.exit("El archivo %s no tiene el contenido real" % ruta)


def iniciar_mqtt():
    """Crea el cliente, configura TLS y conecta en segundo plano (la reconexión es automática)."""
    global client
    c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=cfg.MQTT_CLIENT_ID)
    c.tls_set(ca_certs=cfg.CA, certfile=cfg.CERT, keyfile=cfg.LLAVE,
              cert_reqs=ssl.CERT_REQUIRED, tls_version=ssl.PROTOCOL_TLS_CLIENT)   # valida el servidor y se identifica
    c.reconnect_delay_set(min_delay=2, max_delay=60)                              # espera creciente
    c.on_connect, c.on_disconnect, c.on_message, c.on_connect_fail = al_conectar, al_desconectar, al_recibir, al_fallar
    c.connect_async(cfg.MQTT_HOST, cfg.MQTT_PORT, keepalive=30)
    c.loop_start()
    client = c


def detener_mqtt():
    global client, conectado
    if client is not None:
        try:
            client.disconnect()
            client.loop_stop()
        except Exception:
            pass
    client, conectado = None, False


# ==================================================
# CICLO
# ==================================================

def ciclo(n_ciclo):
    global manual_hasta, seq, aviso_contingencia
    h, nivel, luz = sim["humedad"], sim["nivel"], sim["luz"]
    es_noche = luz == 0
    print("[SENSORES] Humedad: %.1f %% | Estanque: %d %% | Noche: %s" % (h, nivel, es_noche))

    if manual_hasta is not None and not manual_activo():
        manual_hasta = None
        print("[MODO] La orden manual venció: vuelve el modo automático")
    if not manual_activo():
        quiere = debe_regar(h, nivel, es_noche)
        if quiere != rele_on:
            print("[RIEGO]", "Se cumplen las condiciones" if quiere else "Condiciones no cumplidas")
            aplicar_rele(quiere)
            publicar_estado()

    if n_ciclo % max(1, round(cfg.PUBLICAR_CADA_S / cfg.CICLO_S)) == 0:
        seq += 1
        l = (int(time.time()), seq, int(round(h * 10)), nivel, luz == 1)
        if not (conectado and enviar_lectura(l)):
            cola.append(l)
            print("[COLA] Sin conexión: lectura guardada (%d/%d)" % (len(cola), cfg.MAX_COLA))

    if (not conectado and not aviso_contingencia
            and time.monotonic() - sin_conexion_desde > cfg.FALLA_COM_S):
        aviso_contingencia = True
        print("[CONTINGENCIA] Sin comunicación con AWS: el riego sigue en modo AUTOMÁTICO local;")
        print("               las lecturas se guardan en cola y se enviarán al reconectar")
    print("--------------------------------------------------")


def jitter():
    """Pequeña variación para que las lecturas se vean reales (no cruza el umbral de 90 %)."""
    sim["humedad"] = round(min(100, max(0, sim["humedad"] + random.choice((-0.2, -0.1, 0, 0.1, 0.2)))), 1)


# ==================================================
# COMANDOS DE TECLADO (para las pruebas y el video)
# ==================================================

def comando(linea):
    global caida_simulada, salir, sin_conexion_desde
    p = linea.strip().lower().split()
    if not p:
        return
    try:
        if p[0] == "h":
            sim["humedad"] = max(0.0, min(100.0, float(p[1])))
        elif p[0] == "n":
            sim["nivel"] = max(0, min(100, int(p[1])))
        elif p[0] == "l":
            sim["luz"] = 1 if int(p[1]) else 0
        elif p[0] == "red":
            caida_simulada = not caida_simulada
            if caida_simulada:
                print("[PRUEBA] Caída de red SIMULADA: se corta la conexión MQTT")
                sin_conexion_desde = time.monotonic()
                detener_mqtt()
            else:
                print("[PRUEBA] Red RESTABLECIDA: reconectando...")
                iniciar_mqtt()
        elif p[0] == "estado":
            print("[ESTADO]", sim, "relé:", "ON" if rele_on else "OFF", "| conectado:", conectado, "| cola:", len(cola))
        elif p[0] == "q":
            salir = True
        else:
            print("Comando desconocido. Usa: h <0-100> | n <0-100> | l <0|1> | red | estado | q")
    except (IndexError, ValueError):
        print("Uso: h <0-100> | n <0-100> | l <0|1> | red | estado | q")


def leer_teclado():
    for linea in sys.stdin:
        comando(linea)
        if salir:
            break


def main():
    revisar_certificados()
    print("=" * 50)
    print("  ATRAPANIEBLAS · CONTROLADOR VIRTUAL (Raspbian)")
    print("  Sensores simulados + relé · AWS IoT Core TLS")
    print("=" * 50)
    print("Comandos: h <0-100> | n <0-100> | l <0|1> | red | estado | q")
    iniciar_mqtt()
    threading.Thread(target=leer_teclado, daemon=True).start()
    n = 0
    try:
        while not salir:
            n += 1
            jitter()
            ciclo(n)
            vaciar_cola()
            time.sleep(cfg.CICLO_S)
    except KeyboardInterrupt:
        pass
    detener_mqtt()
    print("Adiós")


if __name__ == "__main__":
    main()
