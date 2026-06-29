FROM python:3.14-alpine@sha256:26730869004e2b9c4b9ad09cab8625e81d256d1ce97e72df5520e806b1709f92 AS runtime

# OCI image metadata. VCS_REF/IMAGE_SOURCE are wired from CI build-args; the
# defaults keep labels meaningful for local/standalone builds.
ARG VCS_REF=unknown
ARG IMAGE_SOURCE=https://github.com/RamazanKara/private-ai-platform-kit
LABEL org.opencontainers.image.source="${IMAGE_SOURCE}" \
      org.opencontainers.image.revision="${VCS_REF}" \
      org.opencontainers.image.description="Retrieval-augmented generation (RAG) service for the Private AI Platform Kit" \
      org.opencontainers.image.licenses="Apache-2.0"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app
COPY requirements.lock .
RUN python -m pip install --no-cache-dir --disable-pip-version-check --require-hashes -r requirements.lock \
    && adduser -D -H -u 10001 appuser

COPY app ./app
USER 10001
EXPOSE 8080

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
