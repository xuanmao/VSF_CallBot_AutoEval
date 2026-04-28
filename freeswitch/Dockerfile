FROM signalwire/freeswitch:1.10.12

# Build dependencies for mod_audio_stream
RUN apt-get update && apt-get install -y --no-install-recommends \
        git cmake build-essential pkg-config \
        libssl-dev libspeex-dev libspeexdsp-dev \
        zlib1g-dev ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# mod_audio_stream — opens a WebSocket per channel and pushes raw L16 audio
# in both directions. Used by the dialplan via the `uuid_audio_stream` API.
ARG MOD_AUDIO_STREAM_REF=master
RUN git clone --depth 1 --branch ${MOD_AUDIO_STREAM_REF} \
        https://github.com/amigniter/mod_audio_stream.git /tmp/mod_audio_stream \
    && cd /tmp/mod_audio_stream \
    && cmake -B build -S . -DCMAKE_BUILD_TYPE=Release \
    && cmake --build build --target install -j"$(nproc)" \
    && rm -rf /tmp/mod_audio_stream

EXPOSE 5060/udp 5060/tcp 16384-16484/udp

CMD ["freeswitch", "-nf", "-nonat"]
