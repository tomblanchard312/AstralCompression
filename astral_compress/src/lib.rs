use pyo3::prelude::*;
use pyo3::exceptions::PyValueError;
use pyo3::types::PyBytes;
use pyo3::Py;
use zstd;
use regex::Regex;

/// Compress telemetry data using Q16 quantization and delta encoding with zstd compression
#[pyfunction]
#[pyo3(signature = (data, channels))]
fn compress_telemetry(py: Python, data: &[u8], channels: usize) -> PyResult<Py<PyBytes>> {
    if data.len() % 4 != 0 {
        return Err(PyValueError::new_err("Data length must be multiple of 4 for float32"));
    }
    if channels == 0 {
        return Err(PyValueError::new_err("Channels must be > 0"));
    }
    
    let n_floats = data.len() / 4;
    let n_samples = n_floats / channels;
    if n_floats % channels != 0 {
        return Err(PyValueError::new_err("Data length not compatible with channels"));
    }
    
    // Parse big-endian floats
    let mut floats = Vec::with_capacity(n_floats);
    for i in 0..n_floats {
        let start = i * 4;
        let bytes = [data[start], data[start+1], data[start+2], data[start+3]];
        let float = f32::from_be_bytes(bytes);
        floats.push(float);
    }
    
    let mut meta = Vec::new();
    let mut firsts = Vec::new();
    let mut deltas = Vec::new();
    
    for ch in 0..channels {
        let mut ch_vals = Vec::with_capacity(n_samples);
        for t in 0..n_samples {
            ch_vals.push(floats[t * channels + ch]);
        }
        
        let mn = ch_vals.iter().fold(f32::INFINITY, |a, &b| a.min(b));
        let mx = ch_vals.iter().fold(f32::NEG_INFINITY, |a, &b| a.max(b));
        let span = mx - mn;
        let span = if span < 1e-30 { 1.0 } else { span };
        
        // Write meta (min, span) as big-endian floats
        meta.extend_from_slice(&mn.to_be_bytes());
        meta.extend_from_slice(&span.to_be_bytes());
        
        // Quantize to Q16
        let mut q_vals = Vec::with_capacity(n_samples);
        for &val in &ch_vals {
            let q = ((val - mn) / span * 65535.0).round() as u16;
            let q = q.min(65535).max(0);
            q_vals.push(q);
        }
        
        // First value
        firsts.extend_from_slice(&q_vals[0].to_be_bytes());
        
        // Delta encode
        if n_samples > 1 {
            for i in 1..n_samples {
                let delta = q_vals[i] as i32 - q_vals[i-1] as i32;
                let delta = delta.max(-32768).min(32767) as i16;
                deltas.extend_from_slice(&delta.to_be_bytes());
            }
        }
    }
    
    let mut payload = Vec::new();
    payload.extend_from_slice(&meta);
    payload.extend_from_slice(&firsts);
    payload.extend_from_slice(&deltas);
    
    // Compress with zstd level 9 (similar to LZMA preset 9)
    let compressed = zstd::encode_all(&payload[..], 9)
        .map_err(|e| PyValueError::new_err(format!("zstd compression failed: {}", e)))?;
    
    Ok(PyBytes::new_bound(py, &compressed).unbind())
}

/// Decompress telemetry data from zstd-compressed Q16 quantized format
#[pyfunction]
#[pyo3(signature = (payload_bytes, original_length, channels))]
fn decompress_telemetry(py: Python, payload_bytes: &[u8], original_length: usize, channels: usize) -> PyResult<Py<PyBytes>> {
    if original_length % 4 != 0 {
        return Err(PyValueError::new_err("Original length must be multiple of 4"));
    }
    if channels == 0 {
        return Err(PyValueError::new_err("Channels must be > 0"));
    }
    
    let n_floats = original_length / 4;
    let n_samples = n_floats / channels;
    if n_floats % channels != 0 {
        return Err(PyValueError::new_err("Original length not compatible with channels"));
    }
    
    // Decompress zstd
    let payload = zstd::decode_all(payload_bytes)
        .map_err(|e| PyValueError::new_err(format!("zstd decompression failed: {}", e)))?;
    
    let meta_sz = channels * 8;
    let first_sz = channels * 2;
    let delta_sz = channels * (n_samples.saturating_sub(1)) * 2;
    
    if payload.len() < meta_sz + first_sz + delta_sz {
        return Err(PyValueError::new_err("Payload too short"));
    }
    
    let meta_b = &payload[0..meta_sz];
    let first_b = &payload[meta_sz..meta_sz + first_sz];
    let delta_b = &payload[meta_sz + first_sz..meta_sz + first_sz + delta_sz];
    
    let mut result = vec![vec![0.0f32; n_samples]; channels];
    
    for ch in 0..channels {
        let mn_bytes = &meta_b[ch*8..(ch+1)*8];
        let mn = f32::from_be_bytes([mn_bytes[0], mn_bytes[1], mn_bytes[2], mn_bytes[3]]);
        let span = f32::from_be_bytes([mn_bytes[4], mn_bytes[5], mn_bytes[6], mn_bytes[7]]);
        
        let q0_bytes = &first_b[ch*2..(ch+1)*2];
        let q0 = u16::from_be_bytes([q0_bytes[0], q0_bytes[1]]);
        
        let mut q = vec![q0];
        if n_samples > 1 {
            let off = ch * (n_samples - 1) * 2;
            for i in 0..(n_samples - 1) {
                let delta_bytes = &delta_b[off + i*2..off + (i+1)*2];
                let delta = i16::from_be_bytes([delta_bytes[0], delta_bytes[1]]);
                let next_q = (q[i] as i32 + delta as i32).max(0).min(65535) as u16;
                q.push(next_q);
            }
        }
        
        for (i, &qv) in q.iter().enumerate() {
            result[ch][i] = mn + (qv as f32) / 65535.0 * span;
        }
    }
    
    let mut out = Vec::with_capacity(original_length);
    for t in 0..n_samples {
        for ch in 0..channels {
            let float_bytes = result[ch][t].to_be_bytes();
            out.extend_from_slice(&float_bytes);
        }
    }
    
    Ok(PyBytes::new_bound(py, &out).unbind())
}

/// Compress binary float data by reordering bytes and using zstd compression
#[pyfunction]
fn compress_binary_float(py: Python, data: &[u8]) -> PyResult<Py<PyBytes>> {
    if data.len() % 4 != 0 {
        return Err(PyValueError::new_err("Data length must be multiple of 4"));
    }
    
    let n = data.len() / 4;
    let mut b0 = Vec::with_capacity(n);
    let mut b1 = Vec::with_capacity(n);
    let mut b2 = Vec::with_capacity(n);
    let mut b3 = Vec::with_capacity(n);
    
    for i in 0..n {
        b0.push(data[i*4]);
        b1.push(data[i*4 + 1]);
        b2.push(data[i*4 + 2]);
        b3.push(data[i*4 + 3]);
    }
    
    let mut reordered = Vec::with_capacity(data.len());
    reordered.extend_from_slice(&b0);
    reordered.extend_from_slice(&b1);
    reordered.extend_from_slice(&b2);
    reordered.extend_from_slice(&b3);
    
    // Compress with zstd level 9
    let compressed = zstd::encode_all(&reordered[..], 9)
        .map_err(|e| PyValueError::new_err(format!("zstd compression failed: {}", e)))?;
    
    Ok(PyBytes::new_bound(py, &compressed).unbind())
}

/// Decompress binary float data from zstd-compressed reordered format
#[pyfunction]
#[pyo3(signature = (payload_bytes, original_length))]
fn decompress_binary_float(py: Python, payload_bytes: &[u8], original_length: usize) -> PyResult<Py<PyBytes>> {
    if original_length % 4 != 0 {
        return Err(PyValueError::new_err("Original length must be multiple of 4"));
    }
    
    // Decompress zstd
    let reordered = zstd::decode_all(payload_bytes)
        .map_err(|e| PyValueError::new_err(format!("zstd decompression failed: {}", e)))?;
    
    let n = original_length / 4;
    if reordered.len() != original_length {
        return Err(PyValueError::new_err("Decompressed data length mismatch"));
    }
    
    let mut out = vec![0u8; original_length];
    for i in 0..n {
        out[i*4] = reordered[i];
        out[i*4 + 1] = reordered[n + i];
        out[i*4 + 2] = reordered[2*n + i];
        out[i*4 + 3] = reordered[3*n + i];
    }
    
    Ok(PyBytes::new_bound(py, &out).unbind())
}

/// Compress text data using abbreviation encoding and zstd compression
#[pyfunction]
fn compress_text(py: Python, data: &[u8]) -> PyResult<Py<PyBytes>> {
    let text = std::str::from_utf8(data)
        .map_err(|e| PyValueError::new_err(format!("Invalid UTF-8: {}", e)))?;
    
    // Abbreviation mapping (same as Python)
    let abbrevs: std::collections::HashMap<&str, &str> = [
        ("nominal", "NOM"), ("satellite", "SAT"), ("battery", "BAT"), ("temperature", "TMP"),
        ("attitude", "ATT"), ("telemetry", "TLM"), ("command", "CMD"), ("systems", "SYS"),
        ("percent", "PCT"), ("contact", "CNT"), ("entering", "ENT"), ("established", "EST"),
        ("anomaly", "ANM"), ("downlink", "DLK"), ("uplink", "ULK"), ("payload", "PLD"),
        ("minutes", "MIN"), ("seconds", "SEC"), ("warning", "WRN"), ("critical", "CRT"),
        ("interface", "IFC"), ("transmit", "TX"), ("receive", "RX"), ("subsystem", "SSM"),
    ].iter().cloned().collect();
    
    let mut abbrev_words: Vec<&str> = abbrevs.keys().cloned().collect();
    abbrev_words.sort();
    let mut abbrev_to_id = std::collections::HashMap::new();
    for (i, &word) in abbrev_words.iter().enumerate() {
        abbrev_to_id.insert(word, i as u8);
    }
    
    // Tokenize and abbreviate - split on non-word chars but keep them (like Python re.split(r"(\W+)", text))
    use regex::Regex;
    let token_re = Regex::new(r"\w+|\W+").unwrap();
    let mut out_parts = Vec::new();
    let marker = "\x1e";
    
    for token in token_re.find_iter(text) {
        let token_str = token.as_str();
        if token_str.chars().all(|c| c.is_alphabetic()) {
            let lower = token_str.to_lowercase();
            if let Some(&abbr) = abbrevs.get(lower.as_str()) {
                let flag = if token_str.chars().next().unwrap().is_uppercase() {
                    if token_str.chars().all(|c| c.is_uppercase()) { 2 } else { 1 }
                } else { 0 };
                out_parts.push(format!("{}{:02x}{}", marker, abbrev_to_id[lower.as_str()], flag));
                continue;
            }
        }
        out_parts.push(token_str.to_string());
    }
    
    let abbr_text = out_parts.join("");
    let abbr_bytes = abbr_text.as_bytes();
    
    // Compress with zstd level 9
    let compressed = zstd::encode_all(abbr_bytes, 9)
        .map_err(|e| PyValueError::new_err(format!("zstd compression failed: {}", e)))?;
    
    Ok(PyBytes::new_bound(py, &compressed).unbind())
}

/// Decompress text data from zstd-compressed abbreviated format
#[pyfunction]
fn decompress_text(py: Python, data: &[u8]) -> PyResult<Py<PyBytes>> {
    // Decompress zstd first
    let abbr_bytes = zstd::decode_all(data)
        .map_err(|e| PyValueError::new_err(format!("zstd decompression failed: {}", e)))?;
    
    let text = std::str::from_utf8(&abbr_bytes)
        .map_err(|e| PyValueError::new_err(format!("Invalid UTF-8 in decompressed data: {}", e)))?;
    
    // Abbreviation mapping (same as Python)
    let abbrevs: std::collections::HashMap<&str, &str> = [
        ("nominal", "NOM"), ("satellite", "SAT"), ("battery", "BAT"), ("temperature", "TMP"),
        ("attitude", "ATT"), ("telemetry", "TLM"), ("command", "CMD"), ("systems", "SYS"),
        ("percent", "PCT"), ("contact", "CNT"), ("entering", "ENT"), ("established", "EST"),
        ("anomaly", "ANM"), ("downlink", "DLK"), ("uplink", "ULK"), ("payload", "PLD"),
        ("minutes", "MIN"), ("seconds", "SEC"), ("warning", "WRN"), ("critical", "CRT"),
        ("interface", "IFC"), ("transmit", "TX"), ("receive", "RX"), ("subsystem", "SSM"),
    ].iter().cloned().collect();
    
    let mut abbrev_words: Vec<&str> = abbrevs.keys().cloned().collect();
    abbrev_words.sort();
    let mut id_to_abbrev = std::collections::HashMap::new();
    for (i, &word) in abbrev_words.iter().enumerate() {
        id_to_abbrev.insert(i as u8, word);
    }
    
    // Apply case and restore abbreviations
    let repl_re = Regex::new(r"\x1e([0-9a-f]{2})([012])").unwrap();
    let restored = repl_re.replace_all(text, |caps: &regex::Captures| {
        let idx = u8::from_str_radix(&caps[1], 16).unwrap_or(0);
        let flag = caps[2].parse::<u8>().unwrap_or(0);
        if let Some(base) = id_to_abbrev.get(&idx) {
            match flag {
                1 => base.chars().enumerate().map(|(i, c)| if i == 0 { c.to_uppercase().next().unwrap() } else { c }).collect::<String>(),
                2 => base.to_uppercase(),
                _ => base.to_string(),
            }
        } else {
            caps[0].to_string()
        }
    });
    
    Ok(PyBytes::new_bound(py, restored.as_bytes()).unbind())
}

/// Python module definition
#[pymodule]
fn astral_compress(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(compress_telemetry, m)?)?;
    m.add_function(wrap_pyfunction!(decompress_telemetry, m)?)?;
    m.add_function(wrap_pyfunction!(compress_binary_float, m)?)?;
    m.add_function(wrap_pyfunction!(decompress_binary_float, m)?)?;
    m.add_function(wrap_pyfunction!(compress_text, m)?)?;
    m.add_function(wrap_pyfunction!(decompress_text, m)?)?;
    Ok(())
}