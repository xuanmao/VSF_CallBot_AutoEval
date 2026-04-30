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

# Build mod_audio_stream with write-frame injection patch
ARG MOD_AUDIO_STREAM_REF=main
RUN git clone --depth 1 --recurse-submodules --shallow-submodules \
        --branch ${MOD_AUDIO_STREAM_REF} \
        https://github.com/amigniter/mod_audio_stream.git /tmp/mod_audio_stream

# Apply patch: add direct write-frame injection so the bridge can stream
# Gemini audio back to the caller via streamAudio WebSocket messages instead
# of the file+ESL workaround.
RUN python3 - <<'PATCH'
import sys

# ── audio_streamer_glue.cpp ──────────────────────────────────────────────────
with open('/tmp/mod_audio_stream/audio_streamer_glue.cpp') as f:
    src = f.read()

# 1. Add playback buffer members as PUBLIC after m_stateMutex so stream_write_frame
#    (a free function) can access them directly.
assert 'std::mutex m_stateMutex;' in src, "anchor not found: m_stateMutex"
src = src.replace(
    'std::mutex m_stateMutex;',
    'std::mutex m_stateMutex;\n'
    'public:\n'
    '    // Write-frame injection buffer — filled by processMessage, drained by stream_write_frame\n'
    '    std::vector<uint8_t> m_playback_buf;\n'
    '    std::mutex           m_playback_mutex;\n'
    'private:'
)

# 2a. Before the streamAudio type-check, intercept killAudio and clear the buffer.
#     Without this, killAudio is silently discarded (line 301: return out).
kill_anchor = 'if (!jsonType || std::strcmp(jsonType, "streamAudio") != 0) {'
assert kill_anchor in src, "anchor not found: streamAudio type check"
src = src.replace(
    kill_anchor,
    '// Write-frame injection: flush buffer on killAudio so the bot stops mid-speech\n'
    '    if (jsonType && std::strcmp(jsonType, "killAudio") == 0) {\n'
    '        std::lock_guard<std::mutex> _lk(m_playback_mutex);\n'
    '        m_playback_buf.clear();\n'
    '        return out;\n'
    '    }\n'
    + kill_anchor
)

# 2b. After base64_decode in processMessage(), push bytes into the buffer.
#    processMessage is a member function so it can access private/public members.
assert 'decoded = base64_decode(jsonAudio->valuestring)' in src, "anchor not found: base64_decode"
src = src.replace(
    'decoded = base64_decode(jsonAudio->valuestring)',
    'decoded = base64_decode(jsonAudio->valuestring);\n'
    '        {\n'
    '            std::lock_guard<std::mutex> _lk(m_playback_mutex);\n'
    '            m_playback_buf.insert(m_playback_buf.end(),\n'
    '                reinterpret_cast<const uint8_t*>(decoded.data()),\n'
    '                reinterpret_cast<const uint8_t*>(decoded.data()) + decoded.size());\n'
    '        }\n'
    '        // Audio is in the write-frame buffer — skip disk write entirely.\n'
    '        return out;'
)

# 3. Add stream_write_frame() before stream_session_cleanup().
#    Uses the same static_cast pattern as stream_frame() to unwrap pAudioStreamer.
write_fn = '''
switch_bool_t stream_write_frame(switch_media_bug_t *bug)
{
    static std::atomic<int> s_call_count{0};
    int call_no = ++s_call_count;

    auto *tech_pvt = (private_t *) switch_core_media_bug_get_user_data(bug);
    if (!tech_pvt || !tech_pvt->pAudioStreamer) return SWITCH_TRUE;

    auto sp_ptr = static_cast<std::shared_ptr<AudioStreamer>*>(tech_pvt->pAudioStreamer);
    if (!sp_ptr || !(*sp_ptr)) return SWITCH_TRUE;
    auto streamer = *sp_ptr;

    switch_frame_t *frame = switch_core_media_bug_get_write_replace_frame(bug);

    // Log first 5 calls and every 500th to see if we are being invoked and
    // what the frame dimensions look like.
    if (call_no <= 5 || call_no % 500 == 0) {
        switch_log_printf(SWITCH_CHANNEL_LOG, SWITCH_LOG_INFO,
            "stream_write_frame #%d: frame=%p datalen=%u samples=%u buf=%zu\\n",
            call_no,
            (void*)frame,
            frame ? frame->datalen : 0,
            frame ? frame->samples : 0,
            streamer->m_playback_buf.size());
    }

    if (!frame || !frame->data) return SWITCH_TRUE;

    uint32_t needed = frame->datalen > 0 ? frame->datalen : frame->samples * 2;
    if (needed == 0) return SWITCH_TRUE;

    std::lock_guard<std::mutex> _lk(streamer->m_playback_mutex);
    if (streamer->m_playback_buf.size() < needed) return SWITCH_TRUE;

    memcpy(frame->data, streamer->m_playback_buf.data(), needed);
    streamer->m_playback_buf.erase(
        streamer->m_playback_buf.begin(),
        streamer->m_playback_buf.begin() + needed);
    frame->datalen = needed;
    frame->samples = needed / 2;
    switch_core_media_bug_set_write_replace_frame(bug, frame);

    if (call_no <= 5) {
        switch_log_printf(SWITCH_CHANNEL_LOG, SWITCH_LOG_INFO,
            "stream_write_frame #%d: INJECTED %u bytes\\n", call_no, needed);
    }
    return SWITCH_TRUE;
}
'''
assert 'switch_status_t stream_session_cleanup(' in src, "anchor not found: stream_session_cleanup"
src = src.replace(
    'switch_status_t stream_session_cleanup(',
    write_fn + 'switch_status_t stream_session_cleanup('
)

with open('/tmp/mod_audio_stream/audio_streamer_glue.cpp', 'w') as f:
    f.write(src)
print("audio_streamer_glue.cpp patched OK")

# ── audio_streamer_glue.h ────────────────────────────────────────────────────
with open('/tmp/mod_audio_stream/audio_streamer_glue.h') as f:
    src = f.read()

assert 'switch_bool_t stream_frame(switch_media_bug_t *bug);' in src, "anchor not found: stream_frame"
src = src.replace(
    'switch_bool_t stream_frame(switch_media_bug_t *bug);',
    'switch_bool_t stream_frame(switch_media_bug_t *bug);\n'
    'switch_bool_t stream_write_frame(switch_media_bug_t *bug);'
)

with open('/tmp/mod_audio_stream/audio_streamer_glue.h', 'w') as f:
    f.write(src)
print("audio_streamer_glue.h patched OK")

# ── mod_audio_stream.c ───────────────────────────────────────────────────────
with open('/tmp/mod_audio_stream/mod_audio_stream.c') as f:
    src = f.read()

# Add WRITE_REPLACE case before the existing WRITE/default fallthrough
assert 'case SWITCH_ABC_TYPE_WRITE:\n        default:\n            break;' in src, "anchor not found: WRITE case"
src = src.replace(
    'case SWITCH_ABC_TYPE_WRITE:\n        default:\n            break;',
    'case SWITCH_ABC_TYPE_WRITE_REPLACE:\n'
    '            return stream_write_frame(bug);\n\n'
    '        case SWITCH_ABC_TYPE_WRITE:\n'
    '        default:\n'
    '            break;'
)

# Enable WRITE_REPLACE on the media bug
assert 'switch_media_bug_flag_t flags = SMBF_READ_STREAM;' in src, "anchor not found: SMBF_READ_STREAM"
src = src.replace(
    'switch_media_bug_flag_t flags = SMBF_READ_STREAM;',
    'switch_media_bug_flag_t flags = SMBF_READ_STREAM | SMBF_WRITE_REPLACE;'
)

with open('/tmp/mod_audio_stream/mod_audio_stream.c', 'w') as f:
    f.write(src)
print("mod_audio_stream.c patched OK")
PATCH

RUN cd /tmp/mod_audio_stream \
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
