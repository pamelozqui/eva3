package cl.inacap.atrapanieblas

import android.content.Intent
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.widget.SeekBar
import androidx.appcompat.app.AppCompatActivity
import cl.inacap.atrapanieblas.databinding.ActivityMainBinding

class MainActivity : AppCompatActivity() {
    private lateinit var b: ActivityMainBinding
    private lateinit var db: LocalDb
    private val handler = Handler(Looper.getMainLooper())

    private var activo = false          // la consulta periódica solo corre con la app visible (ahorro de batería)
    private var fallos = 0              // fallos seguidos -> espera creciente (reconexión automática)
    private var riegoActual: String? = null
    private var ultimaOrdenAuto = ""
    private var ultimaOrdenAutoTs = 0L

    private val tick = Runnable { consultar() }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        b = ActivityMainBinding.inflate(layoutInflater)
        setContentView(b.root)
        db = LocalDb(this)
        b.tvUsuario.text = " ${intent.getStringExtra("usuario") ?: ""}"

        b.btnOn.setOnClickListener { enviarOrden("ON") }
        b.btnOff.setOnClickListener { enviarOrden("OFF") }

        b.sbHum.setOnSeekBarChangeListener(listener { b.tvSimHum.text = "Humedad: $it %" })
        b.sbNivel.setOnSeekBarChangeListener(listener { b.tvSimNivel.text = "Nivel: $it %" })
        b.btnPublicar.setOnClickListener {
            Api.ejecutar({ Api.simular(b.sbHum.progress, b.sbNivel.progress, b.cbLuz.isChecked) },
                ok = { estado("🟢", "Lectura simulada publicada en AWS IoT Core") },
                error = { estado("🔴", "No se pudo publicar: ${it.message}") })
        }

        b.btnHistorial.setOnClickListener { startActivity(Intent(this, HistorialActivity::class.java)) }
        b.btnSalir.setOnClickListener {
            Api.cerrarSesion(); startActivity(Intent(this, LoginActivity::class.java)); finish()
        }

        mostrarCache()
    }

    override fun onStart() { super.onStart(); activo = true; consultar() }
    override fun onStop() { super.onStop(); activo = false; handler.removeCallbacks(tick) }

    // ---------- consulta periódica con reconexión automática ----------
    private fun consultar() {
        handler.removeCallbacks(tick)
        Api.ejecutar({ Api.estado() },
            ok = { e ->
                if (!activo) return@ejecutar
                fallos = 0
                mostrar(e)
                enviarPendiente()
                programar(Config.POLL_MS)
            },
            error = { ex ->
                if (!activo) return@ejecutar
                if (ex is ApiError && ex.codigo == 401) { cerrarPorSesion(); return@ejecutar }
                fallos++
                val espera = minOf(Config.POLL_MS shl minOf(fallos, 4), Config.BACKOFF_MAX_MS)   // 10, 20, 40, 60 s
                contingencia(espera)
                programar(espera)
            })
    }

    private fun programar(ms: Long) { handler.removeCallbacks(tick); handler.postDelayed(tick, ms) }

    private fun cerrarPorSesion() {
        Api.cerrarSesion()
        startActivity(Intent(this, LoginActivity::class.java)); finish()
    }

    // ---------- pantalla ----------
    private fun mostrar(e: Estado) {
        val l = e.lectura
        if (l != null) {
            b.tvHumedad.text = "Humedad: ${l.humedad} %"
            b.tvNivel.text = "Nivel del estanque: ${l.nivel} %"
            b.pbNivel.progress = l.nivel
            db.guardarLecturas(listOf(l))
        }
        riegoActual = e.riego
        b.tvRiego.text = "Riego: " + when (e.riego?.uppercase()) {
            "ON" -> "💦 ACTIVO"; "OFF" -> "⏹ DETENIDO"; else -> "--"
        } + if (e.modo == "manual") "  (orden manual temporal)" else "  (modo automático del nodo)"

        when {
            l != null && !l.integra -> estado("🟠", "Lectura con firma inválida: se ignora")
            !e.nodoEnLinea -> estado("🟠", "Servidor conectado · el nodo IoT no envía datos recientes")
            else -> estado("🟢", "Conectado · nodo en línea")
        }
        if (l != null && l.integra && e.nodoEnLinea && b.swAuto.isChecked) decidirRiego(l)
    }

    private fun estado(icono: String, texto: String) { b.tvConexion.text = "$icono $texto" }

    /** Plan de contingencia: sin servidor se muestra el último dato guardado y las órdenes quedan en cola. */
    private fun contingencia(espera: Long) {
        val pend = if (db.ordenPendiente() != null) " · 1 orden en cola" else ""
        estado("🔴", "Sin conexión con el servidor · reintento en ${espera / 1000} s · mostrando último dato guardado$pend")
        mostrarCache()
    }

    private fun mostrarCache() {
        val l = db.lecturasGuardadas(1).firstOrNull() ?: return
        b.tvHumedad.text = "Humedad: ${l.humedad} %  (guardada)"
        b.tvNivel.text = "Nivel del estanque: ${l.nivel} %  (guardado)"
        b.pbNivel.progress = l.nivel
    }

    // ---------- órdenes (con cola si no hay conexión) ----------
    private fun enviarOrden(accion: String) {
        Api.ejecutar({ Api.ordenRiego(accion) },
            ok = { estado("🟢", "Orden $accion enviada al nodo") },
            error = { ex ->
                if (Api.esFalloDeRed(ex)) {
                    db.encolarOrden(accion)
                    estado("🟠", "Sin conexión: orden $accion guardada, se enviará al reconectar")
                } else estado("🔴", "Orden rechazada: ${ex.message}")
            })
    }

    /** Al volver la conexión, envía la orden que quedó pendiente (si no es demasiado antigua). */
    private fun enviarPendiente() {
        val o = db.ordenPendiente() ?: return
        if (System.currentTimeMillis() - o.ts > Config.ORDEN_VIGENCIA_MS) {
            db.borrarOrden(o.id); estado("🟠", "Orden pendiente vencida: se descartó"); return
        }
        Api.ejecutar({ Api.ordenRiego(o.accion) },
            ok = { db.borrarOrden(o.id); estado("🟢", "Orden pendiente ${o.accion} enviada") },
            error = { /* se reintenta en la próxima consulta */ })
    }

    /** Misma regla del ESP32: humedad >= 90 %, estanque >= 15 % y es de noche. */
    private fun decidirRiego(l: Lectura) {
        val orden = if (Config.debeRegar(l.humedad, l.nivel, l.luz)) "ON" else "OFF"
        val ahora = System.currentTimeMillis()
        val yaPedida = orden == ultimaOrdenAuto && ahora - ultimaOrdenAutoTs < Config.REENVIO_AUTO_MS
        if (orden != riegoActual && !yaPedida) {
            ultimaOrdenAuto = orden; ultimaOrdenAutoTs = ahora
            enviarOrden(orden)
        }
    }

    private fun listener(f: (Int) -> Unit) = object : SeekBar.OnSeekBarChangeListener {
        override fun onProgressChanged(s: SeekBar?, p: Int, fromUser: Boolean) = f(p)
        override fun onStartTrackingTouch(s: SeekBar?) {}
        override fun onStopTrackingTouch(s: SeekBar?) {}
    }
}
