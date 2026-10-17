FROM alpine:3.20

RUN apk add --no-cache python3

COPY receiver.py /usr/local/bin/receiver.py
RUN chmod 0755 /usr/local/bin/receiver.py

ENV DE_SCREEN_DATA=/data/screens
ENV DE_SCREEN_SECRET=/etc/digital-examen/proxy.secret
ENV DE_SCREEN_PORT=9840
ENV DE_SCREEN_KEEP_DAYS=10

EXPOSE 9840

CMD ["python3", "/usr/local/bin/receiver.py"]
