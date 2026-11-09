package cl.inacap.atrapanieblas

/** El hash de contraseñas se hace en el servidor (API en AWS); aquí solo la política de contraseña. */
object Seguridad {
    fun passwordValida(p: String) = p.length >= 8 && p.any { it.isLetter() } && p.any { it.isDigit() }
}
