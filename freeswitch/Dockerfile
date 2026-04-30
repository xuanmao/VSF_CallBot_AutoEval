FROM safarov/freeswitch:latest AS freeswitch-runtime

# ── Build stage ──────────────────────────────────────────────────────────────
FROM ubuntu:22.04 AS builder
ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y --no-install-recommends \
        git cmake build-essential pkg-config \
        libssl-dev libspeex-dev libspeexdsp-dev \
        libapr1-dev libaprutil1-dev \
        libevent-dev \
        zlib1g-dev ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Copy FreeSWITCH shared lib from the runtime image so the linker can resolve it
COPY --from=freeswitch-runtime /usr/lib/libfreeswitch.so.1.0.0 /usr/lib/
RUN ln -sf libfreeswitch.so.1.0.0 /usr/lib/libfreeswitch.so.1 \
    && ln -sf libfreeswitch.so.1 /usr/lib/libfreeswitch.so \
    && ldconfig

# Sparse-clone FreeSWITCH source at the exact matching version (headers only)
RUN git clone --depth 1 --filter=blob:none --sparse \
        --branch v1.10.12 \
        https://github.com/signalwire/freeswitch.git /tmp/freeswitch-src \
    && cd /tmp/freeswitch-src \
    && git sparse-checkout set src/include libs/libteletone/src

# Write switch_am_config.h directly (Linux x86_64 values)
RUN printf '%s\n' \
    '#ifndef SWITCH_AM_CONFIG_H' \
    '#define SWITCH_AM_CONFIG_H' \
    '#define SWITCH_INT_16  short' \
    '#define SWITCH_INT_32  int' \
    '#define SWITCH_INT_64  long' \
    '#define SWITCH_SIZE_T  size_t' \
    '#define SWITCH_SSIZE_T ssize_t' \
    '#define SWITCH_SIZEOF_VOIDP 8' \
    '#define SWITCH_PREFIX_DIR "/usr"' \
    '#define SWITCH_SSIZE_T_FMT "%ld"' \
    '#define SWITCH_SIZE_T_FMT "%zu"' \
    '#define SWITCH_INT64_T_FMT "%ld"' \
    '#define SWITCH_UINT64_T_FMT "%lu"' \
    '#endif' \
    > /tmp/freeswitch-src/src/include/switch_am_config.h

# Generate switch_version.h (cmake template substitution)
RUN printf '%s\n' \
    '#ifndef SWITCH_VERSION_H' \
    '#define SWITCH_VERSION_H' \
    '#define SWITCH_VERSION_MAJOR 1' \
    '#define SWITCH_VERSION_MINOR 10' \
    '#define SWITCH_VERSION_MICRO 12' \
    '#define SWITCH_VERSION_REVISION ""' \
    '#define SWITCH_VERSION_REVISION_HUMAN ""' \
    '#define SWITCH_VERSION_NUMBER "1.10.12"' \
    '#define SWITCH_VERSION_FULL "1.10.12-release"' \
    '#endif' \
    > /tmp/freeswitch-src/src/include/switch_version.h

# Create pkg-config file so mod_audio_stream cmake can find FreeSWITCH
RUN mkdir -p /usr/lib/pkgconfig /tmp/fs-mod-out && \
    printf 'prefix=/usr\nexec_prefix=${prefix}\nlibdir=${prefix}/lib\nincludedir=/tmp/freeswitch-src/src/include\n\nName: freeswitch\nDescription: FreeSWITCH\nVersion: 1.10.12\nLibs: -L${libdir} -lfreeswitch\nCflags: -I${includedir} -I/tmp/freeswitch-src/libs/libteletone/src\nmodulesdir=/tmp/fs-mod-out\n' \
    > /usr/lib/pkgconfig/freeswitch.pc

# Build mod_audio_stream; cmake reads modulesdir from the .pc above
ARG MOD_AUDIO_STREAM_REF=main
RUN git clone --depth 1 --recurse-submodules --shallow-submodules \
        --branch ${MOD_AUDIO_STREAM_REF} \
        https://github.com/amigniter/mod_audio_stream.git /tmp/mod_audio_stream \
    && cd /tmp/mod_audio_stream \
    && cmake -B build -S . -DCMAKE_BUILD_TYPE=Release \
    && cmake --build build --target install -j"$(nproc)"

# ── Runtime stage ─────────────────────────────────────────────────────────────
FROM safarov/freeswitch:latest

COPY --from=builder /tmp/fs-mod-out/mod_audio_stream.so /usr/lib/freeswitch/mod/

# libevent is required by mod_audio_stream (libwsc) but not in the minimal image
COPY --from=builder /usr/lib/x86_64-linux-gnu/libevent-2.1.so.7 /usr/lib/x86_64-linux-gnu/
COPY --from=builder /usr/lib/x86_64-linux-gnu/libevent_pthreads-2.1.so.7 /usr/lib/x86_64-linux-gnu/
COPY --from=builder /usr/lib/x86_64-linux-gnu/libevent_core-2.1.so.7 /usr/lib/x86_64-linux-gnu/

EXPOSE 5060/udp 5060/tcp 16384-16484/udp

CMD ["freeswitch", "-nf", "-nonat"]
