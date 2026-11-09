package cl.inacap.atrapanieblas

import android.content.ContentValues
import android.content.Context
import android.database.sqlite.SQLiteDatabase
import android.database.sqlite.SQLiteOpenHelper

/**
 * Almacenamiento temporal local (SQLite) de la app:
 *  - cola_ordenes: orden de riego que no pudo enviarse por falta de conexión (se envía al volver; solo se guarda la última).
 *  - cache_lecturas: últimas lecturas, para mostrarlas cuando no hay conexión.
 */
class LocalDb(ctx: Context) : SQLiteOpenHelper(ctx, "atrapanieblas.db", null, 1) {

    override fun onCreate(db: SQLiteDatabase) {
        db.execSQL("CREATE TABLE cola_ordenes (id INTEGER PRIMARY KEY AUTOINCREMENT, accion TEXT NOT NULL, ts INTEGER NOT NULL)")
        db.execSQL("CREATE TABLE cache_lecturas (ts INTEGER PRIMARY KEY, humedad REAL NOT NULL, nivel INTEGER NOT NULL, luz INTEGER NOT NULL, integra INTEGER NOT NULL)")
    }

    override fun onUpgrade(db: SQLiteDatabase, o: Int, n: Int) {}

    // ----- órdenes pendientes -----
    data class Orden(val id: Long, val accion: String, val ts: Long)

    @Synchronized
    fun encolarOrden(accion: String) {
        val db = writableDatabase
        db.delete("cola_ordenes", null, null)          // la última orden reemplaza a las anteriores
        db.insert("cola_ordenes", null, ContentValues().apply {
            put("accion", accion); put("ts", System.currentTimeMillis())
        })
    }

    @Synchronized
    fun ordenPendiente(): Orden? =
        readableDatabase.rawQuery("SELECT id, accion, ts FROM cola_ordenes ORDER BY id DESC LIMIT 1", null).use {
            if (it.moveToFirst()) Orden(it.getLong(0), it.getString(1), it.getLong(2)) else null
        }

    @Synchronized
    fun borrarOrden(id: Long) { writableDatabase.delete("cola_ordenes", "id=?", arrayOf(id.toString())) }

    // ----- caché de lecturas -----
    @Synchronized
    fun guardarLecturas(lista: List<Lectura>) {
        val db = writableDatabase
        db.beginTransaction()
        try {
            lista.forEach {
                db.insertWithOnConflict("cache_lecturas", null, ContentValues().apply {
                    put("ts", it.ts); put("humedad", it.humedad); put("nivel", it.nivel)
                    put("luz", if (it.luz) 1 else 0); put("integra", if (it.integra) 1 else 0)
                }, SQLiteDatabase.CONFLICT_REPLACE)
            }
            db.execSQL("DELETE FROM cache_lecturas WHERE ts NOT IN (SELECT ts FROM cache_lecturas ORDER BY ts DESC LIMIT 100)")
            db.setTransactionSuccessful()
        } finally { db.endTransaction() }
    }

    @Synchronized
    fun lecturasGuardadas(limite: Int): List<Lectura> =
        readableDatabase.rawQuery(
            "SELECT ts, humedad, nivel, luz, integra FROM cache_lecturas ORDER BY ts DESC LIMIT $limite", null).use {
            val r = mutableListOf<Lectura>()
            while (it.moveToNext()) r += Lectura(it.getLong(0), it.getDouble(1), it.getInt(2), it.getInt(3) == 1, it.getInt(4) == 1)
            r
        }
}
