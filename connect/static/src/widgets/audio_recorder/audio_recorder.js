/** @odoo-module **/
"use strict"

import { Component, useState, useRef, onWillUnmount } from "@odoo/owl"
import { registry } from "@web/core/registry"
import { useService } from "@web/core/utils/hooks"
import { _t } from "@web/core/l10n/translation"
import { standardFieldProps } from "@web/views/fields/standard_field_props"
import { isBinarySize } from "@web/core/utils/binary"

// Hard ceiling on the encoded WAV before we base64-encode it. 16kHz mono PCM
// WAV = 32 KB/sec, so 5 MB ~= 2.5 minutes of speech — comfortable headroom
// for IVR prompts. Keep this in sync with the server-side cap, which reads
// ir.config_parameter 'connect.audio.max_recording_bytes' (default 5 MB).
// The server is the trust boundary; this check is a courtesy that prevents
// pointless round-trips. If an admin raises the server cap, this constant
// must be updated too.
const MAX_WAV_BYTES = 5 * 1024 * 1024

/**
 * Audio recorder field widget for connect.audio.recording_file.
 *
 * Captures microphone via MediaRecorder, decodes to PCM via AudioContext,
 * encodes to 16-bit mono 16kHz WAV (Twilio-playable), and writes a base64
 * blob to the bound Binary field.
 *
 * Why WAV: Twilio <Play> only supports MP3, WAV (PCM), AIFF, GSM, and µ-law.
 * Browser MediaRecorder defaults to WebM/Opus which Twilio cannot play.
 */
export class AudioRecorderField extends Component {
    static template = "connect.AudioRecorderField"
    static props = {
        ...standardFieldProps,
        filenameField: { type: String, optional: true },
    }

    setup() {
        this.notification = useService("notification")
        this.state = useState({
            status: "idle", // idle | recording | encoding | error
            error: null,
            durationMs: 0,
            previewUrl: null,
        })
        this.mediaRecorder = null
        this.chunks = []
        this.stream = null
        this.startTs = null
        this.tickHandle = null
        this.audioCtx = null

        onWillUnmount(() => this._cleanup())
        this._refreshPreview()
    }

    get hasRecording() {
        return !!this.props.record.data[this.props.name]
    }

    async onRecord() {
        if (!navigator.mediaDevices?.getUserMedia) {
            this.state.error = _t("This browser does not support audio recording.")
            this.state.status = "error"
            return
        }
        try {
            this.stream = await navigator.mediaDevices.getUserMedia({ audio: true })
        } catch (err) {
            this.state.error = _t("Microphone permission denied.")
            this.state.status = "error"
            return
        }
        this.chunks = []
        // Pick whatever the browser supports for capture; we'll re-encode to WAV.
        const supported = MediaRecorder.isTypeSupported?.("audio/webm;codecs=opus")
            ? "audio/webm;codecs=opus" : ""
        this.mediaRecorder = new MediaRecorder(this.stream,
            supported ? { mimeType: supported } : undefined)
        this.mediaRecorder.addEventListener("dataavailable", (e) => {
            if (e.data && e.data.size) this.chunks.push(e.data)
        })
        this.mediaRecorder.addEventListener("stop", () => this._onStop())
        this.mediaRecorder.start()
        this.startTs = Date.now()
        this.state.durationMs = 0
        this.state.status = "recording"
        this.tickHandle = setInterval(() => {
            this.state.durationMs = Date.now() - this.startTs
        }, 100)
    }

    async onStop() {
        if (!this.mediaRecorder || this.mediaRecorder.state === "inactive") {
            return
        }
        // Create the AudioContext *here* — this click handler is a live user
        // gesture, which Chrome's autoplay policy requires for context start.
        // If we defer creation to _onStop (fired from the MediaRecorder 'stop'
        // event), the gesture has already expired and AudioContext refuses to
        // start, leaving recording_file empty and failing the server's
        // source=record constraint on save.
        try {
            this.audioCtx = new (window.AudioContext || window.webkitAudioContext)()
            if (this.audioCtx.state === "suspended") {
                await this.audioCtx.resume()
            }
        } catch (err) {
            console.error("AudioContext init failed", err)
            this.state.error = _t("Could not start audio decoder: %s", err.message || err)
            this.state.status = "error"
            return
        }
        this.mediaRecorder.stop()
    }

    onClear() {
        this.props.record.update({ [this.props.name]: false })
        if (this.props.filenameField) {
            this.props.record.update({ [this.props.filenameField]: false })
        }
        this.state.previewUrl = null
        this.state.durationMs = 0
        this.state.status = "idle"
    }

    async _onStop() {
        this._stopTick()
        this._stopStream()
        this.state.status = "encoding"
        try {
            const captureBlob = new Blob(this.chunks,
                { type: this.mediaRecorder.mimeType || "audio/webm" })
            const arrayBuffer = await captureBlob.arrayBuffer()
            // audioCtx was created in onStop() under the user-gesture context.
            const audioBuffer = await this.audioCtx.decodeAudioData(arrayBuffer)
            const wavBlob = encodeWav(audioBuffer, 16000) // 16kHz mono PCM
            this.audioCtx.close()
            this.audioCtx = null
            if (wavBlob.size > MAX_WAV_BYTES) {
                const mb = (wavBlob.size / 1024 / 1024).toFixed(1)
                const maxMb = (MAX_WAV_BYTES / 1024 / 1024).toFixed(0)
                this.state.error = _t(
                    "Recording is too large (%(mb)s MB). Maximum is %(max)s MB.",
                    { mb, max: maxMb })
                this.state.status = "error"
                return
            }
            const base64 = await blobToBase64(wavBlob)
            const filename = `recording-${Date.now()}.wav`
            const updates = { [this.props.name]: base64 }
            if (this.props.filenameField) {
                updates[this.props.filenameField] = filename
            }
            await this.props.record.update(updates)
            this.state.status = "idle"
            this._refreshPreview()
        } catch (err) {
            console.error("Audio encode failed", err)
            this.state.error = _t("Failed to encode recording: %s", err.message || err)
            this.state.status = "error"
            if (this.audioCtx && this.audioCtx.state !== "closed") {
                try { this.audioCtx.close() } catch (e) { /* noop */ }
                this.audioCtx = null
            }
        }
    }

    _refreshPreview() {
        const value = this.props.record.data[this.props.name]
        if (!value) {
            this.state.previewUrl = null
            return
        }
        // Binary fields with attachment=True come back from the server as a
        // compact size hint ("5.12 Kb"), not as base64. We detect that and
        // stream via /web/content; only fresh in-memory captures hold actual
        // base64 ready for a data URL. Without this the <audio> element gets
        // a bogus "data:audio/wav;base64,5.12 Kb" URL and plays silence.
        if (isBinarySize(value)) {
            const { resModel, resId } = this.props.record
            const stamp = this.props.record.data.write_date || Date.now()
            this.state.previewUrl =
                `/web/content?model=${encodeURIComponent(resModel)}` +
                `&id=${encodeURIComponent(resId)}` +
                `&field=${encodeURIComponent(this.props.name)}` +
                `&unique=${encodeURIComponent(stamp)}`
        } else {
            this.state.previewUrl = `data:audio/wav;base64,${value}`
        }
    }

    _stopTick() {
        if (this.tickHandle) {
            clearInterval(this.tickHandle)
            this.tickHandle = null
        }
    }

    _stopStream() {
        if (this.stream) {
            for (const t of this.stream.getTracks()) t.stop()
            this.stream = null
        }
    }

    _cleanup() {
        this._stopTick()
        this._stopStream()
        if (this.mediaRecorder && this.mediaRecorder.state !== "inactive") {
            try { this.mediaRecorder.stop() } catch (e) { /* noop */ }
        }
        if (this.audioCtx && this.audioCtx.state !== "closed") {
            try { this.audioCtx.close() } catch (e) { /* noop */ }
            this.audioCtx = null
        }
    }

    formatDuration(ms) {
        const s = Math.floor(ms / 1000)
        return `${Math.floor(s / 60)}:${(s % 60).toString().padStart(2, "0")}`
    }
}

registry.category("fields").add("connect_audio_recorder", {
    component: AudioRecorderField,
    supportedTypes: ["binary"],
    extractProps: ({ attrs }) => ({
        filenameField: attrs.filename,
    }),
})


// ---- helpers ----

function blobToBase64(blob) {
    return new Promise((resolve, reject) => {
        const reader = new FileReader()
        reader.onload = () => {
            // result is "data:<mime>;base64,XXXX"
            const result = reader.result || ""
            const idx = result.indexOf(",")
            resolve(idx >= 0 ? result.slice(idx + 1) : result)
        }
        reader.onerror = reject
        reader.readAsDataURL(blob)
    })
}

/**
 * Encode an AudioBuffer to a 16-bit PCM WAV Blob, downmixed to mono and
 * resampled to `targetSampleRate` (default 16kHz — Twilio-friendly).
 */
function encodeWav(audioBuffer, targetSampleRate) {
    const sourceRate = audioBuffer.sampleRate
    const sourceLen = audioBuffer.length
    const channels = audioBuffer.numberOfChannels

    // Downmix to mono
    const mono = new Float32Array(sourceLen)
    for (let ch = 0; ch < channels; ch++) {
        const data = audioBuffer.getChannelData(ch)
        for (let i = 0; i < sourceLen; i++) mono[i] += data[i] / channels
    }

    // Linear resample
    const ratio = targetSampleRate / sourceRate
    const targetLen = Math.round(sourceLen * ratio)
    const resampled = new Float32Array(targetLen)
    for (let i = 0; i < targetLen; i++) {
        const srcIdx = i / ratio
        const i0 = Math.floor(srcIdx)
        const i1 = Math.min(i0 + 1, sourceLen - 1)
        const frac = srcIdx - i0
        resampled[i] = mono[i0] * (1 - frac) + mono[i1] * frac
    }

    // 16-bit PCM
    const pcm = new Int16Array(targetLen)
    for (let i = 0; i < targetLen; i++) {
        const s = Math.max(-1, Math.min(1, resampled[i]))
        pcm[i] = s < 0 ? s * 0x8000 : s * 0x7FFF
    }

    // WAV header
    const dataSize = pcm.length * 2
    const buffer = new ArrayBuffer(44 + dataSize)
    const view = new DataView(buffer)
    writeStr(view, 0, "RIFF")
    view.setUint32(4, 36 + dataSize, true)
    writeStr(view, 8, "WAVE")
    writeStr(view, 12, "fmt ")
    view.setUint32(16, 16, true)            // fmt chunk size
    view.setUint16(20, 1, true)             // PCM
    view.setUint16(22, 1, true)             // 1 channel
    view.setUint32(24, targetSampleRate, true)
    view.setUint32(28, targetSampleRate * 2, true) // byte rate
    view.setUint16(32, 2, true)             // block align
    view.setUint16(34, 16, true)            // bits per sample
    writeStr(view, 36, "data")
    view.setUint32(40, dataSize, true)
    new Int16Array(buffer, 44).set(pcm)
    return new Blob([buffer], { type: "audio/wav" })
}

function writeStr(view, offset, s) {
    for (let i = 0; i < s.length; i++) view.setUint8(offset + i, s.charCodeAt(i))
}
