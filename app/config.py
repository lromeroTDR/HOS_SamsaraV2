# --- ARCHIVO DE CONFIGURACIÓN ---
# Este archivo centraliza la configuración de la aplicación, especialmente la que proviene
# de variables de entorno. Esto permite que el código sea portable entre diferentes
# entornos (desarrollo, producción) sin necesidad de modificarlo.

import glob
import os
from dotenv import load_dotenv

# --- CARGA DE VARIABLES DE ENTORNO LOCALES ---
# `load_dotenv()` lee un archivo llamado `.env` en la raíz del proyecto y carga
# las variables definidas en él como si fueran variables de entorno del sistema.
# Esto es extremadamente útil para el desarrollo local, ya que evita tener que
# configurar las variables manualmente en la terminal cada vez.
# En un entorno de producción (como un contenedor Docker o AWS Lambda), estas
# variables se configurarían directamente en el servicio.
load_dotenv()

# --- VALIDACIÓN DE VARIABLES DE ENTORNO ---
# Se define una lista con todas las variables de entorno que son OBLIGATORIAS
# para que la aplicación funcione correctamente.
required_env_vars = [
    "API_TOKEN",           # Token de autenticación para la API de Samsara.
    "API_URL_TRIPS",       # URL del endpoint de la API para obtener los viajes.
    "API_URL_ASSETS",      # URL del endpoint de la API para obtener los vehículos/activos.
    "SAMSARA_TAGS_URL",    # URL del endpoint de la API para obtener los tags.
    "BD_DRIVER",           # Ruta al driver ODBC (usado principalmente en desarrollo local).
    "BD_SERVER",           # Dirección del servidor de la base de datos.
    "BD_DATABASE",         # Nombre de la base de datos a la que conectarse.
    # "BD_USERNAME",         # Usuario para la conexión a la base de datos.
    # "BD_PASSWORD",         # Contraseña para la conexión a la base de datos.
    "BD_TABLE_HISTORICO",  # Nombre de la tabla para los datos históricos (cargas diarias).
    "BD_TABLE_ACTUAL"      # Nombre de la tabla para los datos del día en curso (se sobreescribe).
]

# Se comprueba si alguna de las variables requeridas no está definida.
missing = [k for k in required_env_vars if not os.getenv(k)]
if missing:
    # Si faltan variables, se lanza un error inmediatamente. Esto previene fallos
    # inesperados más adelante y deja claro qué configuración falta.
    raise EnvironmentError(f"Faltan las siguientes variables de entorno: {', '.join(missing)}")

# --- ASIGNACIÓN DE VARIABLES A CONSTANTES ---
# Se asignan los valores de las variables de entorno a constantes de Python
# para un acceso más fácil y legible en el resto del código.
API_TOKEN = os.getenv("API_TOKEN")
API_URL_TRIPS = os.getenv("API_URL_TRIPS")
API_URL_ASSETS = os.getenv("API_URL_ASSETS")
SAMSARA_TAGS_URL = os.getenv("SAMSARA_TAGS_URL")
BD_TABLE_HIST = os.getenv("BD_TABLE_HISTORICO")
BD_TABLE_NOW = os.getenv("BD_TABLE_ACTUAL")
BD_DRIVER = os.getenv("BD_DRIVER")

def conn_str() -> str:
    """
    Construye y retorna la cadena de conexión para la base de datos (ODBC).

    Esta función es dinámica:
    - En un contenedor (entorno de producción), busca la ruta del driver ODBC
      automáticamente en la ruta estándar `/opt/microsoft/msodbcsql18/lib64/`.
    - Para desarrollo local, se usa la variable BD_DRIVER.

    Returns:
        str: La cadena de conexión completa para pyodbc.
    """
    # Para desarrollo local, se usa la variable BD_DRIVER.
    driver_path = BD_DRIVER 

    # Se construye la cadena de conexión con los parámetros base.
    connection_string = (
        f'DRIVER={driver_path};'
        f'SERVER={os.getenv("BD_SERVER")};'
        f'DATABASE={os.getenv("BD_DATABASE")};'
    )

    # Añadir autenticación basada en usuario/contraseña si están presentes, de lo contrario, usar autenticación de Windows.
    if os.getenv("BD_USERNAME") and os.getenv("BD_PASSWORD"):
        connection_string += (
            f'UID={os.getenv("BD_USERNAME")};'
            f'PWD={os.getenv("BD_PASSWORD")};'
        )
    else:
        connection_string += 'Trusted_Connection=yes;'

    # Opciones adicionales para la conexión con SQL Server en entornos modernos.
    connection_string += 'TrustServerCertificate=yes;Encrypt=yes;'
    
    return connection_string

def auth_headers() -> dict:
    """
    Crea y retorna el diccionario de cabeceras (headers) para la autenticación
    en la API de Samsara.

    Returns:
        dict: Un diccionario con las cabeceras 'accept' y 'authorization'.
    """
    return {
        "accept": "application/json",  # Indica que esperamos una respuesta en formato JSON.
        "authorization": "Bearer " + API_TOKEN  # Usa el esquema de autenticación "Bearer Token".
    }
