FROM python:3.11-slim
WORKDIR /app
COPY proxy.py .
RUN pip install --no-cache-dir asyncio
CMD ["python", "proxy.py"]
