FROM python:3.11.15-slim-trixie@sha256:78b39ef14d8e2b4d71f8dc304f1328c37df95fe0ef99477c2ae6bd3d03784553
LABEL org.opencontainers.image.title="Blender Render" \
      org.opencontainers.image.version="0.2.0b1" \
      org.opencontainers.image.source="https://github.com/magnusbauer/blender_render" \
      org.opencontainers.image.licenses="MIT AND GPL-3.0-or-later"
COPY blender_render /opt/blender_render
COPY LICENSE THIRD_PARTY_NOTICES.md /opt/
COPY containers/runtime-install.sh /opt/runtime-install.sh
RUN sh /opt/runtime-install.sh
ENV PYTHONPATH=/opt:/opt/runtime/site \
    BLENDER_RENDER_SITE=/opt/runtime/site \
    PYTHONUNBUFFERED=1 \
    QT_QPA_PLATFORM=offscreen
WORKDIR /work
ENTRYPOINT ["python", "-m", "blender_render.cli"]
