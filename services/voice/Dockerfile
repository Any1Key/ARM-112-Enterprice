FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends espeak-ng sox curl ca-certificates && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir fastapi==0.115.8 uvicorn==0.34.0 piper-tts==1.8.0
RUN mkdir -p /models && for voice in dmitri irina ruslan denis; do \
      curl -fL --retry 3 -o /models/ru_RU-${voice}-medium.onnx https://huggingface.co/rhasspy/piper-voices/resolve/main/ru/ru_RU/${voice}/medium/ru_RU-${voice}-medium.onnx; \
      curl -fL --retry 3 -o /models/ru_RU-${voice}-medium.onnx.json https://huggingface.co/rhasspy/piper-voices/resolve/main/ru/ru_RU/${voice}/medium/ru_RU-${voice}-medium.onnx.json; \
    done
WORKDIR /service
COPY server.py selection.py pronunciation.py pronunciation_ru.json ./
CMD ["uvicorn","server:app","--host","0.0.0.0","--port","8092"]
