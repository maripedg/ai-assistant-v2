FROM python:3.11-slim

# Evita .pyc y buffer en logs
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    STREAMLIT_SERVER_HEADLESS=true

WORKDIR /app

# Instala deps


RUN python -m pip install --upgrade "pip==25.3"

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

# Copia código
COPY . /app

# Puerto interno de streamlit
EXPOSE 8501

# Usa FRONTEND_PORT si está definido, si no 8501
ENV STREAMLIT_SERVER_PORT=${FRONTEND_PORT:-8501}

# Arranque (ajusta la ruta si tu entrypoint es otro)
CMD ["streamlit", "run", "app/main.py"]
