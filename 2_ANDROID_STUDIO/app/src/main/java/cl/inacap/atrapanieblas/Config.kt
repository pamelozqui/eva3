package cl.inacap.atrapanieblas

/** Parámetros del sistema. La IP del servidor API se ingresa en el login (emulador: 10.0.2.2). */
object Config {
    const val API_PORT = 5000                 // API HTTPS (Flask) -> AWS IoT Core / DynamoDB

    const val HUMEDAD_MIN = 90.0              // regla de la evaluación 1: hay camanchaca
    const val NIVEL_MIN = 15                  // estanque con agua suficiente

    const val POLL_MS = 5_000L                // cada cuánto se consultan los datos mientras la app está visible
    const val BACKOFF_MAX_MS = 60_000L        // reconexión automática: espera creciente 10, 20, 40, 60 s
    const val ORDEN_VIGENCIA_MS = 10 * 60_000L // una orden pendiente más antigua que esto se descarta (seguridad)
    const val REENVIO_AUTO_MS = 15_000L       // modo automático: no repetir una orden antes de este tiempo

    /** Misma regla del ESP32: humedad >= 90 %, estanque >= 15 % y es de noche (sin luz). */
    fun debeRegar(humedad: Double, nivel: Int, hayLuz: Boolean) =
        humedad >= HUMEDAD_MIN && nivel >= NIVEL_MIN && !hayLuz
}
