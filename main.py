# --- IMPORTACIÓN DE LIBRERÍAS ---
# Estas son las herramientas que el script necesita para funcionar.

# `glob` se usa para encontrar archivos que coinciden con un patrón, como buscar todos los archivos de un tipo.
# `json` es para trabajar con datos en formato JSON, un estándar muy común para intercambiar información en la web.
# `logging` permite registrar mensajes sobre lo que el script está haciendo, lo cual es vital para depurar errores.
# `requests` es una librería muy popular para hacer peticiones HTTP, es decir, para comunicarse con APIs y páginas web.
# `pytz` se usa para manejar zonas horarias, asegurando que los cálculos de tiempo sean correctos sin importar dónde se ejecute el script.
# `os` proporciona funciones para interactuar con el sistema operativo, como leer variables de entorno.
# `pyodbc` es un driver que permite a Python conectarse y comunicarse con bases de datos que usan el estándar ODBC (como SQL Server).
# `subprocess` permite ejecutar comandos del sistema directamente desde el script.
import glob, json, logging, requests, pytz, os, pyodbc, subprocess

# `datetime` y `timedelta` son clases específicas para trabajar con fechas, horas y diferencias de tiempo.
from datetime import datetime, timedelta

# --- IMPORTACIONES DE MÓDULOS PROPIOS ---
# Estos son archivos de código que pertenecen a este mismo proyecto.
# Desde `app.config`, se importan variables de configuración:
# - `API_URL_ASSETS`, `API_URL_TRIPS`: Las direcciones web (URLs) de la API de donde se sacan los datos.
# - `auth_headers`: La función que prepara las cabeceras de autenticación para poder acceder a la API.
# - `BD_TABLE_HIST`, `BD_TABLE_NOW`: Los nombres de las tablas en la base de datos donde se guardarán los datos.
# Desde `app.samsara_req`, se importan funciones para interactuar con la API de Samsara (un sistema de gestión de flotas):
# - `obtain_assets`: Obtiene la lista de vehículos/activos.
# - `request_travel_time`: Pide el tiempo de viaje para un vehículo.
# - `dt_to_ms`, `ms_to_dt`: Convierten fechas de Python a milisegundos (formato que requiere la API) y viceversa.
# Desde `app.db`, se importan funciones para manejar la base de datos:
# - `save_to_database`: Guarda los datos procesados en la base de datos.
# - `save_to_file`: (No se usa en `run`) Probablemente una función para guardar datos en un archivo.
from app.config import API_URL_ASSETS, API_URL_TRIPS, SAMSARA_TAGS_URL, auth_headers, BD_TABLE_HIST, BD_TABLE_NOW
from app.samsara_tags_handler import obtener_datos_proyectos_ec
from app.samsara_req import obtain_assets, request_travel_time, dt_to_ms, ms_to_dt, request_stopped_time
from app.db import save_to_database, save_to_file

# Define el nombre del archivo donde se guardarán los logs.
log_filename = 'Logs\test_log.log'

# --- CONFIGURACIÓN DEL LOGGING ---
# Se configura el sistema de logging para que muestre mensajes de nivel INFO o superior.
# El formato incluye la fecha, hora, nivel del mensaje y el mensaje en sí.
# Esto es fundamental para saber qué ha hecho el script, especialmente si se ejecuta de forma automática.
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

def _debug_odbc():
    """
    Función de diagnóstico para verificar la configuración del driver ODBC.
    Cuando un script se ejecuta en un servidor o un contenedor (como en AWS Lambda),
    la conexión a la base de datos puede fallar si los drivers no están instalados
    o configurados correctamente. Esta función imprime información clave para
    ayudar a diagnosticar esos problemas.
    """
    print("--- INICIANDO DIAGNÓSTICO DE ODBC ---")
    # Imprime variables de entorno que le dicen al sistema dónde encontrar los drivers.
    print("LD_LIBRARY_PATH (ruta de librerías):", os.environ.get("LD_LIBRARY_PATH"))
    print("ODBCINSTINI (configuración de drivers):", os.environ.get("ODBCINSTINI"))
    try:
        # Intenta leer el archivo de configuración principal de ODBC en sistemas Linux.
        out = subprocess.check_output(["cat", "/etc/odbcinst.ini"]).decode()
        print("/etc/odbcinst.ini:\n", out)
    except Exception as e:
        print("Error al leer /etc/odbcinst.ini:", repr(e))
    try:
        # Pide a `pyodbc` que liste los drivers que ha encontrado. Si está vacío, hay un problema.
        print("Drivers de pyodbc encontrados:", pyodbc.drivers())
    except Exception as e:
        print("Error al llamar a pyodbc.drivers():", repr(e))
    # Busca físicamente los archivos del driver de Microsoft SQL Server en la ruta esperada.
    print("Archivos de driver de MSODBCSQL18 encontrados:", glob.glob("/opt/microsoft/msodbcsql18/lib64/libmsodbcsql-*.so*"))
    print("--- FIN DE DIAGNÓSTICO ---")



def run() -> dict:
    """
    Función principal que orquesta todo el proceso de ETL (Extracción, Transformación, Carga).
    1. EXTRAE datos de vehículos y sus tiempos de viaje de la API de Samsara.
    2. ENRIQUECE los datos con información de Proyectos y EC desde la API de Tags.
    3. TRANSFORMA estos datos en un formato adecuado.
    4. CARGA (guarda) los resultados en una base de datos SQL.
    Retorna un diccionario resumiendo la operación.
    """
    
    # --- CÁLCULO DEL RANGO DE TIEMPO ---
    execution_time = datetime.now()
    local_time = execution_time.astimezone(pytz.timezone("America/Mexico_City"))
    hoy_utc = local_time.astimezone(pytz.utc)
    hour = hoy_utc.hour
    minute = hoy_utc.minute

    hoy_inicio_local = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    hoy_inicio = hoy_inicio_local.astimezone(pytz.timezone("America/Mexico_City")).astimezone(pytz.utc)

    # --- LÓGICA DE NEGOCIO PARA DETERMINAR EL PERÍODO A PROCESAR ---
    if hour == 6 and minute == 0:
        ayer_inicio = hoy_inicio - timedelta(days=1)
        start_ms = dt_to_ms(ayer_inicio)
        end_ms = dt_to_ms(hoy_inicio)
        fecha_str = ayer_inicio.strftime('%Y-%m-%d')
        tbl = BD_TABLE_HIST
    else:
        start_ms = dt_to_ms(hoy_inicio)
        end_ms = dt_to_ms(hoy_utc)
        fecha_str = hoy_inicio.strftime('%Y-%m-%d')
        tbl = BD_TABLE_NOW
    
    # --- EXTRACCIÓN Y ENRIQUECIMIENTO DE DATOS ---
    
    headers = auth_headers()
    session = requests.Session()
    
    # 1. Obtener datos de Proyectos y EC.
    # Se crea un diccionario para búsqueda rápida: {'nombreVehiculo': {'Proyecto': 'P-01', 'EC': 'EC-01'}}
    df_proyectos = obtener_datos_proyectos_ec(headers, SAMSARA_TAGS_URL)
    proyectos_lookup = {}
    if not df_proyectos.empty:
        # Usamos 'name' que es el nombre del vehículo, y lo hacemos el índice para búsqueda rápida.
        proyectos_lookup = df_proyectos.set_index('name')[['Proyecto', 'EC']].to_dict('index')
    
    # 2. Obtener la lista de todos los vehículos ("assets").
    assets = obtain_assets(session, API_URL_ASSETS, headers)
    logging.info(f"Se obtuvieron {len(assets)} assets (vehículos).")
    print(f"Assets obtenidos: {len(assets)}")
    
    # 3. Para cada vehículo, obtener su tiempo total de viaje y enriquecerlo.
    rows = []
    count = 1
    for asset in assets:
        secs = request_travel_time(session, API_URL_TRIPS, asset, start_ms, end_ms, headers)
        stopped_secs = request_stopped_time(session, API_URL_TRIPS, asset, start_ms, end_ms, headers)
        
        # Búsqueda de datos de proyecto y EC para el vehículo actual.
        asset_name = asset['name']
        proyecto_data = proyectos_lookup.get(asset_name, {})
        proyecto = proyecto_data.get('Proyecto', None) # Valor por defecto si no se encuentra.
        ec = proyecto_data.get('EC', None)             # Valor por defecto si no se encuentra.

        print(f"Vehículo {count}/{len(assets)}: {asset_name} - Viaje: {str(timedelta(seconds=secs))} - Detenido: {str(timedelta(seconds=stopped_secs))} - Proyecto: {proyecto} - EC: {ec}")
        count += 1
        
        # Se guardan los datos enriquecidos en la tupla.
        rows.append((asset['id'], asset_name, secs, str(timedelta(seconds=secs)), stopped_secs, proyecto, ec))
        
    # --- CARGA DE DATOS EN LA BASE DE DATOS ---
    
    # 4. Guardar todas las filas procesadas en la base de datos.
    inserted = save_to_database(rows, fecha_str, tbl)
    logging.info(f"Se insertaron {inserted} registros en la tabla {tbl} de la base de datos.")
    
    # --- PREPARACIÓN DEL RESULTADO FINAL ---
    
    result = {
        "date": fecha_str,
        "assets_processed": len(assets),
        "records_inserted": inserted,
        "total_seconds_all_assets": sum(r[2] for r in rows),
        "total_stopped_seconds_all_assets": sum(r[4] for r in rows)
    }

    logging.info(f"Resultado de la ejecución: {result}")
    return result

def lambda_handler(event, context):
    """
    Función "manejadora" de AWS Lambda. Este es el punto de entrada cuando el script
    es ejecutado por el servicio de AWS Lambda.
    - `event`: Contiene datos sobre el evento que disparó la ejecución (ej. una programación de tiempo).
    - `context`: Contiene información sobre el entorno de ejecución de Lambda.
    """
    try:
        # Es una buena práctica ejecutar el diagnóstico de ODBC al inicio,
        # ya que si falla, el error se registrará y será más fácil de depurar.
        _debug_odbc()
        
        # Se llama a la función principal que hace todo el trabajo.
        res = run()
        
        # Si todo sale bien, se retorna un código de estado 200 (OK) y el resultado en formato JSON.
        # Esto es lo que verá el servicio que haya llamado a la Lambda (ej. API Gateway).
        return {"statusCode": 200, "body": json.dumps(res)}
    except Exception as e:
        # Si ocurre cualquier error inesperado durante la ejecución de `run()`, se captura aquí.
        logging.error(f"Error fatal en lambda_handler: {e}", exc_info=True) # `exc_info=True` añade el traceback al log.
        
        # Se retorna un código de estado 500 (Error Interno del Servidor) y un mensaje de error.
        # Esto permite que los sistemas de monitoreo detecten que la ejecución falló.
        return {"statusCode": 500, "body": json.dumps({"error": str(e)})}

# --- BLOQUE DE EJECUCIÓN PRINCIPAL ---
# Este código solo se ejecuta si corres el script directamente con `python main.py`.
# No se ejecuta cuando AWS Lambda importa el archivo.
if __name__ == "__main__":
    """
    Este bloque es para probar el script en un entorno de desarrollo local,
    sin necesidad de subirlo a AWS.
    """
    print("Ejecutando el script localmente...")
    # Se llama a la función `run` y se imprime su resultado en la consola
    # con un formato JSON legible.
    resultado_local = run()
    print(json.dumps(resultado_local, ensure_ascii=False, indent=4))
    print("Ejecución local finalizada.")

