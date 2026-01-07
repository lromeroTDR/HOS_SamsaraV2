# --- MÓDULO DE BASE DE DATOS ---
# Este archivo contiene toda la lógica para interactuar con la base de datos.
# Abstrae las operaciones de conexión, inserción y manejo de errores,
# para que el resto del código no necesite conocer los detalles de la implementación.

import logging
import pyodbc
import json
from .config import conn_str, BD_TABLE_NOW

def connectdb():
    """
    Establece y retorna una conexión con la base de datos.

    Utiliza la cadena de conexión generada en el módulo de configuración.
    Si la conexión falla, registra un error y propaga la excepción
    para que el proceso principal se detenga.

    Returns:
        pyodbc.Connection: Un objeto de conexión a la base de datos.
    
    Raises:
        Exception: Si `pyodbc.connect` falla.
    """
    try:
        # Llama a la función `conn_str` para obtener la cadena de conexión
        # y la usa para establecer la conexión con la base de datos.
        conn = pyodbc.connect(conn_str())
        logging.info("Conexión a la base de datos establecida exitosamente.")
        return conn
    except Exception as e:
        # Si hay un error (ej. credenciales incorrectas, servidor no accesible, driver no encontrado),
        # se registra el error detallado.
        logging.error("Error crítico al conectar a la base de datos: %s", e)
        # Se relanza la excepción para detener la ejecución del script.
        raise

def save_to_database(rows: list[tuple], fecha_str: str, tbl: str) -> int:
    """
    Guarda una lista de filas en la tabla especificada de la base de datos.

    Maneja la transacción completa: abre la conexión, inserta los datos y
    cierra la conexión. Si algo falla, revierte los cambios (rollback).

    Args:
        rows (list[tuple]): Una lista de tuplas, donde cada tupla representa una fila a insertar.
                            Se espera el formato (idUnidad, nombreUnidad, tiempoViaje_secs, 
                            tiempoViaje_human, tiempoDetenido, Proyecto, EC).
        fecha_str (str): La fecha del viaje en formato de texto, que se aplicará a todas las filas.
        tbl (str): El nombre de la tabla de destino.

    Returns:
        int: El número de filas insertadas exitosamente.
    """
    # Si no hay filas para insertar, no se hace nada para evitar trabajo innecesario.
    if not rows:
        logging.warning("No hay datos para guardar en la base de datos.")
        return 0
    
    inserted = 0
    conn = None  # Se inicializa la conexión como None.
    
    try:
        # Se obtiene una conexión a la base de datos.
        conn = connectdb()
        # Se crea un "cursor", que es el objeto que permite ejecutar comandos SQL.
        cur = conn.cursor()
    
        # `fast_executemany` es una optimización de `pyodbc` para inserciones masivas.
        # Aumenta drásticamente el rendimiento. Se activa si está disponible.
        try:
            cur.fast_executemany = True
        except Exception:
            # Si la versión del driver no lo soporta, simplemente se ignora.
            pass

        # LÓGICA ESPECIAL: Si la tabla es la de datos actuales (`BD_TABLE_NOW`),
        # se vacía completamente antes de insertar los nuevos datos.
        # Esto asegura que la tabla siempre contenga solo la información más reciente del día.
        if tbl == BD_TABLE_NOW:
            logging.info(f"Truncando la tabla de datos actuales: {tbl}")
            cur.execute(f"TRUNCATE TABLE {tbl}")
        
        # Se define la plantilla de la consulta SQL para la inserción.
        # Se añaden las nuevas columnas Proyecto y EC.
        insert_sql = f"""
        INSERT INTO {tbl} (idUnidad, nombreUnidad, fechaViaje, tiempoViaje, totalTiempoViaje, tiempoDetenido, Proyecto, EC)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """
        
        # Se preparan los datos (`payload`) para la inserción.
        # Se transforma la lista de `rows` para que coincida con los campos de la consulta SQL, incluyendo los nuevos campos.
        payload = [(a_id, a_name, fecha_str, secs, human, stopped, proyecto, ec) for (a_id, a_name, secs, human, stopped, proyecto, ec) in rows]
        
        # Se ejecuta la inserción de todas las filas de una sola vez.
        cur.executemany(insert_sql, payload)
        
        # `commit()` confirma la transacción, haciendo los cambios permanentes en la base de datos.
        conn.commit()
        inserted = len(rows)
        logging.info(f"{inserted} filas insertadas en la tabla {tbl}.")
        return inserted
    
    except Exception as e: 
        # Si ocurre cualquier error durante la inserción...
        logging.error("Error al guardar en la base de datos: %s", e)
        if conn: 
            # `rollback()` revierte todos los cambios hechos en la transacción actual.
            # Esto asegura que no queden datos a medio insertar en la base de datos.
            logging.warning("Realizando rollback de la transacción.")
            conn.rollback()
        # Se relanza la excepción para que el proceso principal sepa que algo falló.
        raise
    
    finally:
        # El bloque `finally` se ejecuta siempre, haya o no un error.
        if conn:
            # Es crucial cerrar siempre la conexión para liberar recursos en el servidor de la base de datos.
            conn.close()
            logging.info("Conexión a la base de datos cerrada.")
            
def save_to_file(data, name):
    """
    Función de utilidad para guardar datos en un archivo JSON.
    Útil para depuración, para poder inspeccionar los datos que se reciben de una API.
    
    Args:
        data: El objeto de Python (ej. dict, list) a guardar.
        name (str): El nombre del archivo de salida.
    """
    with open(name, 'w', encoding="utf-8") as f:
        # `json.dump` escribe el objeto `data` en el archivo `f`.
        # `indent=4` formatea el JSON para que sea legible por humanos.
        # `ensure_ascii=False` permite que se escriban caracteres como 'ñ' o acentos correctamente.
        json.dump(data, f, indent=4, ensure_ascii=False)
