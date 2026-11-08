FROM python:3.11-slim

WORKDIR /app
COPY . /app

ENV PORT=8765
EXPOSE 8765

CMD ["python3", "main.py", "--web"]
