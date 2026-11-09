package cl.inacap.atrapanieblas

import android.os.Bundle
import android.widget.ArrayAdapter
import androidx.appcompat.app.AppCompatActivity
import cl.inacap.atrapanieblas.databinding.ActivityHistorialBinding
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

class HistorialActivity : AppCompatActivity() {
    private lateinit var b: ActivityHistorialBinding
    private lateinit var db: LocalDb

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        b = ActivityHistorialBinding.inflate(layoutInflater)
        setContentView(b.root)
        db = LocalDb(this)
        b.btnBorrar.setOnClickListener { cargar() }
        cargar()
    }

    /** Rescata el historial guardado en DynamoDB (vía API). Sin conexión muestra la copia local. */
    private fun cargar() {
        b.tvResumen.text = "Cargando…"
        Api.ejecutar({ Api.historial() },
            ok = { (datos, prom) ->
                db.guardarLecturas(datos)
                mostrar(datos, "${datos.size} lecturas · humedad promedio %.1f %%".format(prom))
            },
            error = {
                val local = db.lecturasGuardadas(100)
                mostrar(local, "Sin conexión (${it.message}). Mostrando ${local.size} lecturas guardadas en el teléfono")
            })
    }

    private fun mostrar(datos: List<Lectura>, resumen: String) {
        val fmt = SimpleDateFormat("dd/MM HH:mm:ss", Locale.getDefault())
        b.lvLecturas.adapter = ArrayAdapter(this, android.R.layout.simple_list_item_1, datos.map {
            // ✔ = firma HMAC válida (integridad comprobada) · ✖ = lectura alterada o sin firma
            "${if (it.integra) "✔" else "✖"} ${fmt.format(Date(it.ts))}  ·  Hum ${it.humedad}%  ·  Nivel ${it.nivel}%"
        })
        b.tvResumen.text = if (datos.isEmpty()) "Sin lecturas aún" else resumen
    }
}
