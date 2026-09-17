FROM python:3.12-slim
WORKDIR /service
RUN pip install --no-cache-dir fastapi==0.115.8 uvicorn==0.34.0 onnxruntime==1.20.1 numpy==2.2.3 tokenizers==0.21.0 huggingface-hub==0.29.1
COPY download.py .
RUN python download.py
COPY server.py .
ENV HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1
EXPOSE 8091
CMD ["uvicorn","server:app","--host","0.0.0.0","--port","8091"]
