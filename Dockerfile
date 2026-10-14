FROM python:3.12-slim
WORKDIR /app
COPY bot.py /app/bot.py
ENV DATA_DIR=/data
CMD ["python", "-u", "bot.py"]
