FROM python:3.12-slim

# Non-root, no cache, nothing but the package
RUN useradd --create-home --uid 10001 repaydesk
WORKDIR /app
COPY pyproject.toml ./
COPY src ./src
RUN pip install --no-cache-dir . && rm -rf /root/.cache
# Where the messaging simulator keeps its store: a named volume mounted here takes this owner.
RUN mkdir /sim && chown repaydesk /sim

USER repaydesk
ENV PYTHONUNBUFFERED=1
CMD ["python", "-m", "repaydesk.seed"]
