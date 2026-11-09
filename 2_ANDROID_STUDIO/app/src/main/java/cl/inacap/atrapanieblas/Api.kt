package cl.inacap.atrapanieblas

import android.os.Handler
import android.os.Looper
import org.json.JSONObject
import java.io.IOException
import java.net.URL
import javax.net.ssl.HttpsURLConnection

data class Lectura(val ts: Long, val humedad: Double, val nivel: Int, val luz: Boolean, val integra: Boolean)
data class Estado(val lectura: Lectura?, val nodoEnLinea: Boolean, val riego: String?, val modo: String?)

/** Error devuelto por el servidor (HTTP 4xx/5xx). Un fallo de red es una IOException. */
class ApiError(val codigo: Int, mensaje: String) : Exception(mensaje)

/** Cliente de la API HTTPS (Flask). El servidor lee DynamoDB y publica órdenes en AWS IoT Core. */
object Api {
    var host = ""
    private var token = ""

    private val main = Handler(Looper.getMainLooper())

    /** Ejecuta [trabajo] en un hilo y entrega el resultado en el hilo de UI. */
    fun <T> ejecutar(trabajo: () -> T, ok: (T) -> Unit, error: (Exception) -> Unit) {
        Thread {
            try {
                val r = trabajo()
                main.post { ok(r) }
            } catch (e: Exception) {
                main.post { error(e) }
            }
        }.start()
    }

    fun esFalloDeRed(e: Exception) = e is IOException || (e is ApiError && e.codigo >= 500)

    private fun llamar(metodo: String, ruta: String, cuerpo: JSONObject? = null): JSONObject {
        val c = URL("https://$host:${Config.API_PORT}$ruta").openConnection() as HttpsURLConnection
        try {
            c.requestMethod = metodo
            c.connectTimeout = 6000
            c.readTimeout = 8000
            c.setRequestProperty("Content-Type", "application/json")
            if (token.isNotEmpty()) c.setRequestProperty("Authorization", "Bearer $token")
            if (cuerpo != null) {
                c.doOutput = true
                c.outputStream.use { it.write(cuerpo.toString().toByteArray()) }
            }
            val code = c.responseCode
            val txt = (if (code < 400) c.inputStream else c.errorStream)?.bufferedReader()?.use { it.readText() } ?: "{}"
            val json = try { JSONObject(txt) } catch (e: Exception) { JSONObject() }
            if (code >= 400) throw ApiError(code, json.optString("error", "Error $code"))
            return json
        } finally {
            c.disconnect()
        }
    }

    fun registrar(u: String, p: String) {
        llamar("POST", "/api/registro", JSONObject().put("usuario", u).put("password", p))
    }

    fun login(u: String, p: String) {
        token = llamar("POST", "/api/login", JSONObject().put("usuario", u).put("password", p)).getString("token")
    }

    private fun lectura(o: JSONObject) =
        Lectura(o.getLong("ts"), o.getDouble("humedad"), o.getInt("nivel"), o.optBoolean("luz", false), o.optBoolean("integra", false))

    /** Última lectura del sensor + estado del riego (pantalla principal). */
    fun estado(): Estado {
        val j = llamar("GET", "/api/estado")
        return Estado(
            lectura = j.optJSONObject("lectura")?.let { lectura(it) },
            nodoEnLinea = j.optBoolean("nodo_en_linea", false),
            riego = if (j.isNull("riego")) null else j.optString("riego"),
            modo = if (j.isNull("modo")) null else j.optString("modo"))
    }

    fun historial(): Pair<List<Lectura>, Double> {
        val j = llamar("GET", "/api/lecturas?limit=100")
        val arr = j.getJSONArray("lecturas")
        return (0 until arr.length()).map { lectura(arr.getJSONObject(it)) } to j.optDouble("promedio_humedad", 0.0)
    }

    /** Orden ON/OFF: el servidor la firma (HMAC) y la publica en AWS IoT Core hacia el ESP32. */
    fun ordenRiego(accion: String) {
        llamar("POST", "/api/riego", JSONObject().put("accion", accion))
    }

    /** Publica una lectura simulada (demo cuando Wokwi no está corriendo). */
    fun simular(humedad: Int, nivel: Int, luz: Boolean) {
        llamar("POST", "/api/simular", JSONObject().put("humedad", humedad).put("nivel", nivel).put("luz", luz))
    }

    fun cerrarSesion() { token = "" }
}
