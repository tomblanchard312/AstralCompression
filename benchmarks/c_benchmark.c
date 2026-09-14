#include <stdio.h>
#include <stdlib.h>
#include <stdint.h>
#include <string.h>
#include <math.h>
#include <zstd.h>
#include <time.h>

// Helper functions for big-endian conversion
uint32_t be32_to_cpu(const uint8_t *buf) {
    return (buf[0] << 24) | (buf[1] << 16) | (buf[2] << 8) | buf[3];
}

void cpu_to_be32(uint32_t val, uint8_t *buf) {
    buf[0] = (val >> 24) & 0xFF;
    buf[1] = (val >> 16) & 0xFF;
    buf[2] = (val >> 8) & 0xFF;
    buf[3] = val & 0xFF;
}

uint16_t be16_to_cpu(const uint8_t *buf) {
    return (buf[0] << 8) | buf[1];
}

void cpu_to_be16(uint16_t val, uint8_t *buf) {
    buf[0] = (val >> 8) & 0xFF;
    buf[1] = val & 0xFF;
}

// Telemetry compression: Q12 quantization + delta encoding + zstd
size_t compress_telemetry_c(const uint8_t *data, size_t data_len, size_t channels, uint8_t **output) {
    if (data_len % 4 != 0 || channels == 0) return 0;

    size_t n_floats = data_len / 4;
    size_t n_samples = n_floats / channels;
    if (n_floats % channels != 0) return 0;

    // Parse big-endian floats
    float *floats = malloc(n_floats * sizeof(float));
    for (size_t i = 0; i < n_floats; i++) {
        uint32_t be_val = be32_to_cpu(&data[i * 4]);
        memcpy(&floats[i], &be_val, sizeof(float));
    }

    // Allocate buffers
    size_t meta_size = channels * 8;  // min, span per channel (2 floats)
    size_t firsts_size = channels * 2;  // first value per channel (uint16)
    size_t deltas_size = channels * (n_samples > 1 ? n_samples - 1 : 0) * 2;  // deltas (int16)
    size_t payload_size = meta_size + firsts_size + deltas_size;

    uint8_t *payload = malloc(payload_size);
    uint8_t *meta_ptr = payload;
    uint8_t *firsts_ptr = payload + meta_size;
    uint8_t *deltas_ptr = payload + meta_size + firsts_size;

    for (size_t ch = 0; ch < channels; ch++) {
        // Extract channel data
        float *ch_vals = malloc(n_samples * sizeof(float));
        for (size_t t = 0; t < n_samples; t++) {
            ch_vals[t] = floats[t * channels + ch];
        }

        // Find min/max
        float mn = ch_vals[0], mx = ch_vals[0];
        for (size_t i = 1; i < n_samples; i++) {
            if (ch_vals[i] < mn) mn = ch_vals[i];
            if (ch_vals[i] > mx) mx = ch_vals[i];
        }
        float span = mx - mn;
        if (span < 1e-30f) span = 1.0f;

        // Write meta (min, span) as big-endian floats
        uint32_t mn_be, span_be;
        memcpy(&mn_be, &mn, sizeof(float));
        memcpy(&span_be, &span, sizeof(float));
        cpu_to_be32(mn_be, &meta_ptr[ch * 8]);
        cpu_to_be32(span_be, &meta_ptr[ch * 8 + 4]);

        // Quantize to Q12 (0-4095)
        uint16_t *q_vals = malloc(n_samples * sizeof(uint16_t));
        for (size_t i = 0; i < n_samples; i++) {
            float normalized = (ch_vals[i] - mn) / span;
            uint16_t q = (uint16_t)roundf(normalized * 4095.0f);
            q_vals[i] = q < 0 ? 0 : (q > 4095 ? 4095 : q);
        }

        // First value
        cpu_to_be16(q_vals[0], &firsts_ptr[ch * 2]);

        // Delta encode
        if (n_samples > 1) {
            uint8_t *delta_ptr = deltas_ptr + ch * (n_samples - 1) * 2;
            for (size_t i = 1; i < n_samples; i++) {
                int32_t delta = (int32_t)q_vals[i] - (int32_t)q_vals[i-1];
                int16_t delta_clamped = (int16_t)(delta < -32768 ? -32768 : (delta > 32767 ? 32767 : delta));
                cpu_to_be16(*(uint16_t*)&delta_clamped, &delta_ptr[(i-1) * 2]);
            }
        }

        free(ch_vals);
        free(q_vals);
    }

    free(floats);

    // Compress with zstd level 9
    size_t compress_bound = ZSTD_compressBound(payload_size);
    *output = malloc(compress_bound);
    size_t compressed_size = ZSTD_compress(*output, compress_bound, payload, payload_size, 9);

    free(payload);

    if (ZSTD_isError(compressed_size)) {
        free(*output);
        return 0;
    }

    return compressed_size;
}

// Telemetry decompression
size_t decompress_telemetry_c(const uint8_t *compressed, size_t compressed_len, size_t original_len, size_t channels, uint8_t **output) {
    if (original_len % 4 != 0 || channels == 0) return 0;

    size_t n_floats = original_len / 4;
    size_t n_samples = n_floats / channels;
    if (n_floats % channels != 0) return 0;

    // Decompress zstd
    size_t decompress_bound = ZSTD_getFrameContentSize(compressed, compressed_len);
    if (decompress_bound == ZSTD_CONTENTSIZE_ERROR || decompress_bound == ZSTD_CONTENTSIZE_UNKNOWN) {
        return 0;
    }

    uint8_t *payload = malloc(decompress_bound);
    size_t payload_size = ZSTD_decompress(payload, decompress_bound, compressed, compressed_len);

    if (ZSTD_isError(payload_size)) {
        free(payload);
        return 0;
    }

    // Parse payload
    size_t meta_sz = channels * 8;
    size_t first_sz = channels * 2;
    size_t delta_sz = channels * (n_samples > 1 ? n_samples - 1 : 0) * 2;

    if (payload_size < meta_sz + first_sz + delta_sz) {
        free(payload);
        return 0;
    }

    uint8_t *meta_b = payload;
    uint8_t *first_b = payload + meta_sz;
    uint8_t *delta_b = payload + meta_sz + first_sz;

    *output = malloc(original_len);
    float *result = malloc(channels * n_samples * sizeof(float));

    for (size_t ch = 0; ch < channels; ch++) {
        // Read meta
        uint32_t mn_be = be32_to_cpu(&meta_b[ch * 8]);
        uint32_t span_be = be32_to_cpu(&meta_b[ch * 8 + 4]);
        float mn, span;
        memcpy(&mn, &mn_be, sizeof(float));
        memcpy(&span, &span_be, sizeof(float));

        // Read first value
        uint16_t q0 = be16_to_cpu(&first_b[ch * 2]);

        // Reconstruct quantized values
        uint16_t *q = malloc(n_samples * sizeof(uint16_t));
        q[0] = q0;

        if (n_samples > 1) {
            uint8_t *ch_delta_b = delta_b + ch * (n_samples - 1) * 2;
            for (size_t i = 1; i < n_samples; i++) {
                int16_t delta = (int16_t)be16_to_cpu(&ch_delta_b[(i-1) * 2]);
                int32_t next_q = (int32_t)q[i-1] + delta;
                q[i] = (uint16_t)(next_q < 0 ? 0 : (next_q > 4095 ? 4095 : next_q));
            }
        }

        // Dequantize
        for (size_t i = 0; i < n_samples; i++) {
            result[ch * n_samples + i] = mn + (float)q[i] / 4095.0f * span;
        }

        free(q);
    }

    // Convert to big-endian bytes
    for (size_t t = 0; t < n_samples; t++) {
        for (size_t ch = 0; ch < channels; ch++) {
            float val = result[ch * n_samples + t];
            uint32_t be_val;
            memcpy(&be_val, &val, sizeof(float));
            cpu_to_be32(be_val, &(*output)[(t * channels + ch) * 4]);
        }
    }

    free(payload);
    free(result);
    return original_len;
}

// Binary float compression: byte reordering + zstd
size_t compress_binary_float_c(const uint8_t *data, size_t data_len, uint8_t **output) {
    if (data_len % 4 != 0) return 0;

    size_t n = data_len / 4;
    uint8_t *reordered = malloc(data_len);

    // Reorder bytes: all byte0, all byte1, all byte2, all byte3
    for (size_t i = 0; i < n; i++) {
        reordered[i] = data[i * 4];
        reordered[n + i] = data[i * 4 + 1];
        reordered[2 * n + i] = data[i * 4 + 2];
        reordered[3 * n + i] = data[i * 4 + 3];
    }

    // Compress with zstd level 9
    size_t compress_bound = ZSTD_compressBound(data_len);
    *output = malloc(compress_bound);
    size_t compressed_size = ZSTD_compress(*output, compress_bound, reordered, data_len, 9);

    free(reordered);

    if (ZSTD_isError(compressed_size)) {
        free(*output);
        return 0;
    }

    return compressed_size;
}

// Binary float decompression
size_t decompress_binary_float_c(const uint8_t *compressed, size_t compressed_len, size_t original_len, uint8_t **output) {
    if (original_len % 4 != 0) return 0;

    // Decompress zstd
    size_t decompress_bound = ZSTD_getFrameContentSize(compressed, compressed_len);
    if (decompress_bound == ZSTD_CONTENTSIZE_ERROR || decompress_bound == ZSTD_CONTENTSIZE_UNKNOWN) {
        return 0;
    }

    uint8_t *reordered = malloc(decompress_bound);
    size_t reordered_size = ZSTD_decompress(reordered, decompress_bound, compressed, compressed_len);

    if (ZSTD_isError(reordered_size) || reordered_size != original_len) {
        free(reordered);
        return 0;
    }

    size_t n = original_len / 4;
    *output = malloc(original_len);

    // Restore original byte order
    for (size_t i = 0; i < n; i++) {
        (*output)[i * 4] = reordered[i];
        (*output)[i * 4 + 1] = reordered[n + i];
        (*output)[i * 4 + 2] = reordered[2 * n + i];
        (*output)[i * 4 + 3] = reordered[3 * n + i];
    }

    free(reordered);
    return original_len;
}

// Text compression: abbreviation encoding + zstd
size_t compress_text_c(const uint8_t *data, size_t data_len, uint8_t **output) {
    // For simplicity, implement a basic version
    // In practice, you'd want the same abbreviation mapping as Rust/Python

    // Compress with zstd level 9
    size_t compress_bound = ZSTD_compressBound(data_len);
    *output = malloc(compress_bound);
    size_t compressed_size = ZSTD_compress(*output, compress_bound, data, data_len, 9);

    if (ZSTD_isError(compressed_size)) {
        free(*output);
        return 0;
    }

    return compressed_size;
}

// Text decompression
size_t decompress_text_c(const uint8_t *compressed, size_t compressed_len, uint8_t **output) {
    // Decompress zstd
    size_t decompress_bound = ZSTD_getFrameContentSize(compressed, compressed_len);
    if (decompress_bound == ZSTD_CONTENTSIZE_ERROR || decompress_bound == ZSTD_CONTENTSIZE_UNKNOWN) {
        return 0;
    }

    *output = malloc(decompress_bound);
    size_t output_size = ZSTD_decompress(*output, decompress_bound, compressed, compressed_len);

    if (ZSTD_isError(output_size)) {
        free(*output);
        return 0;
    }

    return output_size;
}