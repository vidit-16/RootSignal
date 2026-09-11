# RootSignal — a runnable image of the whole project.
#
# The point is not deployment. It is that someone can look at the results
# without installing Python, and can re-run the checks that produced them
# rather than taking the README's word for it.
#
#   docker build -t rootsignal .
#   docker run --rm -p 8501:8501 rootsignal          # the dashboard
#   docker run --rm rootsignal pytest                # verify the claims
#   docker run --rm rootsignal python scripts/detect_signals.py --current-period 2026-02-16

FROM python:3.12-slim-bookworm

# Python in a container: no .pyc files, unbuffered output so logs appear as
# they happen rather than when the buffer flushes.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    # Streamlit otherwise tries to open a browser and prompts for an email.
    STREAMLIT_SERVER_HEADLESS=true \
    STREAMLIT_BROWSER_GATHER_USAGE_STATS=false

WORKDIR /app

# Dependencies are installed from pyproject.toml alone, against a placeholder
# package, so that editing source code does not invalidate the layer holding
# pandas, numpy and streamlit.
COPY pyproject.toml README.md ./
RUN mkdir -p src/rootsignal \
 && touch src/rootsignal/__init__.py \
 && pip install --no-cache-dir ".[dev,dashboard,ai]" \
 && rm -rf src

COPY . .

# The real package, without re-resolving the dependencies already installed.
RUN pip install --no-cache-dir --no-deps -e .

# Run as a normal user. Nothing here needs root, and the scripts write into
# data/ and reports/, so those have to belong to the user that runs them.
RUN useradd --create-home --uid 10001 rootsignal \
 && chown -R rootsignal:rootsignal /app
USER rootsignal

# The sample dataset is generated at build time rather than shipped. It is
# deterministic (seed 42), so the image carries exactly the data the committed
# numbers were measured on, and the generator is exercised on every build.
RUN python scripts/generate_sample_data.py --output-dir data/raw/generated

EXPOSE 8501

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8501/_stcore/health')" || exit 1

CMD ["streamlit", "run", "app/streamlit_app.py", \
     "--server.address=0.0.0.0", "--server.port=8501"]
