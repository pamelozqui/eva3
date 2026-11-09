# config.py · Parámetros del controlador virtual (Raspbian)
import os
AQUI = os.path.dirname(os.path.abspath(__file__))

# ---------- AWS IoT Core ----------
MQTT_HOST = "a1b9h3ouaufjru-ats.iot.us-east-1.amazonaws.com"   # tu endpoint de AWS IoT Core
MQTT_PORT = 8883                                               # MQTT sobre TLS
MQTT_CLIENT_ID = "atrapa-nodo"                                 # debe coincidir con la política IoT (client/atrapa-nodo)

# Los 3 archivos que creó crear_infraestructura.sh (se copian a la carpeta certs/)
CA = os.path.join(AQUI, "certs", "AmazonRootCA1.pem")
CERT = os.path.join(AQUI, "certs", "nodo.crt")
LLAVE = os.path.join(AQUI, "certs", "nodo.key")

# Secreto compartido con la API (mismo valor que HMAC_SECRET del .env). Firma lecturas y valida órdenes.
HMAC_SECRET = "758ddf01538c07a7c3adbc434e6a80079198d87a1d590eaa"

# ---------- Tópicos (iguales a servidor.py y a las reglas IoT) ----------
TOPIC_DATOS = "atrapanieblas/meneses-quintoman/captador/datos"
TOPIC_CMD = "atrapanieblas/meneses-quintoman/riego/cmd"
TOPIC_ESTADO = "atrapanieblas/meneses-quintoman/riego/estado"

# ---------- Regla de riego (la de la evaluación 1) ----------
UMBRAL_HUMEDAD = 90        # humedad >= 90 %
UMBRAL_ESTANQUE = 15       # estanque >= 15 %
                           # y es de noche (luz = 0)

# ---------- Tiempos ----------
CICLO_S = 2.5
PUBLICAR_CADA_S = 10
MANUAL_MAX_S = 120         # una orden manual de la app vale 2 minutos; luego vuelve el modo automático
VENTANA_ORDEN_S = 120      # una orden firmada solo es válida 2 minutos (anti-repetición)
FALLA_COM_S = 60           # aviso de contingencia tras este tiempo sin conexión
MAX_COLA = 60              # lecturas guardadas mientras no hay conexión

# ---------- Relé real (opcional, solo si corres esto en una Raspberry física) ----------
GPIO_RELE = 17
