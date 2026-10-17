FROM alpine:3.20

RUN apk add --no-cache python3 imagemagick imagemagick-webp ffmpeg

COPY receiver.py frames.py glue.py recode.py lifecycle.py report.py space.py night.sh report-loop.sh /usr/local/bin/
RUN chmod 0755 /usr/local/bin/receiver.py /usr/local/bin/glue.py \
    /usr/local/bin/recode.py /usr/local/bin/lifecycle.py /usr/local/bin/report.py

ENV PYTHONPATH=/usr/local/bin
ENV DE_SCREEN_DATA=/data/screens
ENV DE_SCREEN_LIVE=/run/digital-examen/live
ENV DE_SCREEN_SECRET=/etc/digital-examen/proxy.secret
ENV DE_SCREEN_PORT=9840
ENV DE_SCREEN_GLUE=/usr/local/bin/glue.py
ENV DE_SCREEN_RECODE=/usr/local/bin/recode.py
ENV DE_SCREEN_PYTHON=/usr/bin/python3
ENV DE_SCREEN_STORE_WIDTH=1024
ENV DE_SCREEN_STORE_HEIGHT=576
ENV DE_SCREEN_LIMIT_GB=900
ENV DE_SCREEN_MIN_FREE_GB=50
ENV DE_SECRETS_DIR=/data/secrets

EXPOSE 9840

CMD ["python3", "/usr/local/bin/receiver.py"]
