
# ---- Stage 1: build nsjail from source (portable) ----
FROM debian:bookworm-slim AS nsjail-build

RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential pkg-config git ca-certificates \
        libprotobuf-dev protobuf-compiler \
        libnl-route-3-dev libcap-dev libseccomp-dev \
        bison flex clang \
    && rm -rf /var/lib/apt/lists/*

# Clone with submodules (kafel)
RUN git clone --depth 1 --recurse-submodules https://github.com/google/nsjail.git /src/nsjail
WORKDIR /src/nsjail

# Build nsjail; clang is fine on Bookworm
RUN make -j"$(nproc)" CC=clang
RUN strip /src/nsjail/nsjail

# ---- Stage 2: runtime (Python + runtime libs for nsjail and numpy/pandas) ----
FROM python:3.11-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PORT=8080

# Runtime shared libs that the nsjail binary needs + numpy/pandas deps
RUN apt-get update && apt-get install -y --no-install-recommends \
            libprotobuf32 \
            libnl-3-200 libnl-route-3-200 \
            libcap2 libseccomp2 \
            libstdc++6 libssl3 libgomp1 libgfortran5 \
        && rm -rf /var/lib/apt/lists/*

# Put nsjail on PATH
COPY --from=nsjail-build /src/nsjail/nsjail /usr/local/bin/nsjail

WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py /app/app.py
COPY nsjail /app/nsjail
COPY sandbox /app/sandbox

# # Drop privileges
# RUN useradd -m -u 10001 appuser
# USER appuser

EXPOSE 8080
CMD ["gunicorn", "-b", "0.0.0.0:8080", "app:app"]
