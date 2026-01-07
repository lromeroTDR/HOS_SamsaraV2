# Este Dockerfile está diseñado para crear un contenedor que simula el entorno de
# producción pero es más fácil de depurar. No está optimizado para AWS Lambda.

FROM amazonlinux:2023

# Instala paquetes básicos del sistema operativo necesarios para la aplicación.
# 'python3' y 'pip' para ejecutar el script.
# 'unixODBC' y 'unixODBC-devel' son necesarios para que `pyodbc` pueda compilarse y funcionar.
# 'tzdata' para el manejo de zonas horarias.
# 'shadow-utils' contiene herramientas de usuario como `useradd`.
RUN dnf -y install \
    python3 \
    python3-pip \
    python3-devel \
    unixODBC \
    unixODBC-devel \
    tzdata \
    shadow-utils \
    && dnf clean all

# Acepta automáticamente el Acuerdo de Licencia de Usuario Final (EULA) de Microsoft.
# Es obligatorio para poder instalar sus productos de forma desatendida.
ENV ACCEPT_EULA=Y

# Instala el driver ODBC 18 de Microsoft para SQL Server.
# 1. Importa la clave GPG de Microsoft para verificar la autenticidad de los paquetes.
# 2. Agrega el repositorio de paquetes de Microsoft a la configuración de `dnf`.
# 3. Instala el driver `msodbcsql18`.
RUN rpm --import https://packages.microsoft.com/keys/microsoft.asc && \
    curl -sSL -o /etc/yum.repos.d/msprod.repo \
        https://packages.microsoft.com/config/rhel/9/prod.repo && \
    dnf -y install msodbcsql18 && \
    dnf clean all

# Pasos de verificación opcionales durante la construcción.
# Ayudan a depurar si la instalación del driver falló.
RUN odbcinst -q -d || true && \
    ls -l /opt/microsoft/msodbcsql18/lib64/ || true && \
    grep -A2 "ODBC Driver 18 for SQL Server" /etc/odbcinst.ini || true

# Establece el directorio de trabajo dentro del contenedor.
WORKDIR /app
# Copia el archivo de requerimientos de Python.
COPY requirements.txt .
# Instala las librerías de Python.
RUN pip3 install --no-cache-dir -r requirements.txt
# Copia el código de la aplicación.
COPY app/ ./app
COPY main.py .

# Crea un usuario sin privilegios de root para ejecutar la aplicación.
# Es una buena práctica de seguridad para no ejecutar procesos como root.
RUN useradd -m appuser && chown -R appuser:appuser /app
USER appuser

# Comando por defecto que se ejecutará cuando el contenedor inicie.
CMD ["python3", "main.py"]
