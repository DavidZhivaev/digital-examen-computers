FROM python:3.11-slim

RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates \
 && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir --extra-index-url https://download.pytorch.org/whl/cpu -r /tmp/requirements.txt

COPY service.py model.py markers.py train.py night.sh /usr/local/bin/
RUN chmod 0755 /usr/local/bin/service.py /usr/local/bin/train.py

ENV PYTHONPATH=/usr/local/bin
ENV PYTHONUNBUFFERED=1
ENV OMP_NUM_THREADS=2
ENV DE_AI_MODEL_DIR=/models
ENV DE_AI_POLICY_DIR=/etc/digital-examen/policy
ENV DE_AI_PORT=9850

EXPOSE 9850

CMD ["python3", "/usr/local/bin/service.py"]
