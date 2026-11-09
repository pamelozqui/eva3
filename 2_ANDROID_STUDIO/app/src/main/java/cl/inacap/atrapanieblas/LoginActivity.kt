package cl.inacap.atrapanieblas

import android.content.Context
import android.content.Intent
import android.os.Bundle
import androidx.appcompat.app.AppCompatActivity
import cl.inacap.atrapanieblas.databinding.ActivityLoginBinding

class LoginActivity : AppCompatActivity() {
    private lateinit var b: ActivityLoginBinding
    private var intentosFallidos = 0

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        b = ActivityLoginBinding.inflate(layoutInflater)
        setContentView(b.root)
        b.etHost.setText(getSharedPreferences("cfg", Context.MODE_PRIVATE).getString("host", ""))
        b.btnLogin.setOnClickListener { login() }
        b.btnRegistrar.setOnClickListener { registrar() }
    }

    private fun datosValidos(): Boolean {
        val host = b.etHost.text.toString().trim()
        if (host.isEmpty()) { msg("Ingresa la IP del servidor (emulador: 10.0.2.2)", false); return false }
        if (b.etUsuario.text.toString().trim().length < 3) { msg("El usuario debe tener al menos 3 caracteres", false); return false }
        Api.host = host
        getSharedPreferences("cfg", Context.MODE_PRIVATE).edit().putString("host", host).apply()
        return true
    }

    private fun login() {
        val u = b.etUsuario.text.toString().trim()
        val p = b.etPassword.text.toString()
        if (!datosValidos() || p.isEmpty()) return
        if (intentosFallidos >= 5) { msg("Demasiados intentos. Reinicia la aplicación.", false); return }
        msg("Conectando…", true)
        Api.ejecutar({ Api.login(u, p) },
            ok = {
                intentosFallidos = 0
                startActivity(Intent(this, MainActivity::class.java).putExtra("usuario", u))
                finish()
            },
            error = { intentosFallidos++; msg("${mensajeError(it)} (${intentosFallidos}/5)", false) })
    }

    private fun registrar() {
        val u = b.etUsuario.text.toString().trim()
        val p = b.etPassword.text.toString()
        if (!datosValidos()) return
        if (!Seguridad.passwordValida(p)) { msg("Contraseña: mín. 8 caracteres, con letras y números", false); return }
        Api.ejecutar({ Api.registrar(u, p) },
            ok = { msg("Cuenta creada. Ya puedes iniciar sesión.", true) },
            error = { msg(mensajeError(it), false) })
    }

    private fun mensajeError(e: Exception) =
        if (Api.esFalloDeRed(e)) "No se pudo conectar con el servidor" else (e.message ?: "Error")

    private fun msg(t: String, ok: Boolean) {
        b.tvMensaje.setTextColor(if (ok) 0xFF2E7D32.toInt() else 0xFFC62828.toInt())
        b.tvMensaje.text = t
    }
}
