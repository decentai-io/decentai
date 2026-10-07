# The speech container: speech to text and text to speech from models
# that run here (speech/service.py). Built from the repository root:
#   docker build -f speech/Dockerfile .
#
# The image carries the two engines and no model. Models are fetched
# once, into the volume at /data/models, from the pinned list in
# speech/catalogue.py.
FROM python:3.12-slim

WORKDIR /opt/decentai
COPY speech/requirements.txt ./speech/requirements.txt
RUN pip install --no-cache-dir -r speech/requirements.txt \
    && useradd --system --create-home decentai \
    && mkdir -p /data/models \
    && chown decentai /data/models

# The code, last: the only layer a change to it rebuilds.
COPY speech ./speech

ENV SPEECH_MODELS_DIR=/data/models
USER decentai
EXPOSE 8004
CMD ["python", "-m", "speech.service"]
