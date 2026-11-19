# Worker image with Playwright (for JS-rendered store sites). Run on any VM / Fly.io / Railway cron.
FROM mcr.microsoft.com/playwright/python:v1.49.0-jammy
WORKDIR /app
COPY pyproject.toml ./
COPY pricewatch ./pricewatch
RUN pip install --no-cache-dir -e ".[vision,browser]"
COPY config ./config
COPY fixtures ./fixtures
COPY plugins ./plugins
ENV PRICEWATCH_PLUGIN_PATHS=/app/plugins
ENTRYPOINT ["pricewatch"]
CMD ["run", "catalog", "--all"]
