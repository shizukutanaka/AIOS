# aictl — lightweight development container
# No GPU required — for testing, CI, and development

FROM python:3.12-slim

LABEL maintainer="shizukutanaka"
LABEL description="aictl — AI Native Linux OS CLI"

WORKDIR /opt/aios

# Copy project
COPY aictl/ aictl/
COPY pyproject.toml README.md LICENSE ./

# Install
RUN pip install --no-cache-dir -e .

# Verify
RUN aictl --version && \
    python3 -m py_compile aictl/__main__.py

# State directory
RUN mkdir -p /var/lib/aios && chmod 700 /var/lib/aios
ENV AIOS_STATE_DIR=/var/lib/aios

EXPOSE 7700 8080 9999

ENTRYPOINT ["aictl"]
CMD ["--help"]
