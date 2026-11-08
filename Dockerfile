FROM python:3.11-slim

WORKDIR /app
COPY . /app

ENV PORT=8765
EXPOSE 8765

CMD ["sh", "-c", "if [ -f samples/emergencies.json ]; then exec python3 main.py --demo --web; else exec python3 main.py --dataset data/toronto-downtown.json --emergencies tests/fixtures/toronto_emergencies.json --disruptions tests/fixtures/toronto_disruptions.json --web; fi"]
