FROM python:3.9-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY main_groq.py .
CMD ["uvicorn", "main_groq:app", "--host", "0.0.0.0", "--port", "8000"]