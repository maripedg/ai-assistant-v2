FROM python:3.11-slim

# Evita .pyc y buffer en logs
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app

# Trabajaremos desde /app (el paquete 'backend' vivirá en /app/backend)
WORKDIR /app

# Instala dependencias (aprovecha caché)

RUN python -m pip install --upgrade "pip==25.3"

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

# Copia TODO el backend dentro de /app/backend
COPY . /app/backend

# Expone el puerto de Uvicorn
EXPOSE 8000

# Arranque apuntando al módulo con prefijo 'backend'
CMD ["uvicorn", "backend.app.main:app", "--host", "0.0.0.0", "--port", "8000"]
