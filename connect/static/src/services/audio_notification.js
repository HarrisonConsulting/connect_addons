/** @odoo-module **/
import { registry } from "@web/core/registry"

const NOTIFICATION_SOUNDS = {
    park: { name: "Call Parked", default_frequency: 800, default_pattern: "double_beep" },
    unpark: { name: "Call Retrieved", default_frequency: 600, default_pattern: "single_beep" },
    transfer_complete: { name: "Transfer Complete", default_frequency: 1000, default_pattern: "ascending" },
    transfer_failed: { name: "Transfer Failed", default_frequency: 400, default_pattern: "descending" },
    queue_join: { name: "Caller Joined Queue", default_frequency: 700, default_pattern: "single_beep" },
    error: { name: "Error", default_frequency: 300, default_pattern: "triple_beep" },
    connected: { name: "Connected", default_frequency: 900, default_pattern: "single_beep" },
}

const PATTERNS = {
    single_beep(ctx, freq, vol) {
        const osc = ctx.createOscillator()
        const gain = ctx.createGain()
        osc.connect(gain)
        gain.connect(ctx.destination)
        osc.frequency.value = freq
        osc.type = "sine"
        gain.gain.value = vol
        osc.start()
        gain.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + 0.15)
        osc.stop(ctx.currentTime + 0.15)
    },
    double_beep(ctx, freq, vol) {
        [0, 0.2].forEach(offset => {
            const osc = ctx.createOscillator()
            const gain = ctx.createGain()
            osc.connect(gain)
            gain.connect(ctx.destination)
            osc.frequency.value = freq
            osc.type = "sine"
            gain.gain.value = vol
            osc.start(ctx.currentTime + offset)
            gain.gain.setValueAtTime(vol, ctx.currentTime + offset)
            gain.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + offset + 0.12)
            osc.stop(ctx.currentTime + offset + 0.12)
        })
    },
    ascending(ctx, freq, vol) {
        const osc = ctx.createOscillator()
        const gain = ctx.createGain()
        osc.connect(gain)
        gain.connect(ctx.destination)
        osc.frequency.value = freq
        osc.frequency.linearRampToValueAtTime(freq * 1.5, ctx.currentTime + 0.3)
        osc.type = "sine"
        gain.gain.value = vol
        gain.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + 0.3)
        osc.start()
        osc.stop(ctx.currentTime + 0.3)
    },
    descending(ctx, freq, vol) {
        const osc = ctx.createOscillator()
        const gain = ctx.createGain()
        osc.connect(gain)
        gain.connect(ctx.destination)
        osc.frequency.value = freq
        osc.frequency.linearRampToValueAtTime(freq * 0.5, ctx.currentTime + 0.3)
        osc.type = "sine"
        gain.gain.value = vol
        gain.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + 0.3)
        osc.start()
        osc.stop(ctx.currentTime + 0.3)
    },
    triple_beep(ctx, freq, vol) {
        [0, 0.15, 0.3].forEach(offset => {
            const osc = ctx.createOscillator()
            const gain = ctx.createGain()
            osc.connect(gain)
            gain.connect(ctx.destination)
            osc.frequency.value = freq
            osc.type = "sine"
            gain.gain.value = vol
            gain.gain.setValueAtTime(vol, ctx.currentTime + offset)
            gain.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + offset + 0.1)
            osc.start(ctx.currentTime + offset)
            osc.stop(ctx.currentTime + offset + 0.1)
        })
    },
}

export const audioNotificationService = {
    dependencies: [],
    start() {
        let audioCtx = null
        let volume = parseFloat(localStorage.getItem('connect_audio_volume') || '0.3')
        let enabled = localStorage.getItem('connect_audio_enabled') !== 'false'

        function getContext() {
            if (!audioCtx || audioCtx.state === "closed") {
                audioCtx = new (window.AudioContext || window.webkitAudioContext)()
            }
            if (audioCtx.state === "suspended") {
                audioCtx.resume()
            }
            return audioCtx
        }

        return {
            play(soundKey) {
                if (!enabled) return
                const sound = NOTIFICATION_SOUNDS[soundKey]
                if (!sound) return
                try {
                    const ctx = getContext()
                    const pattern = PATTERNS[sound.default_pattern]
                    if (pattern) {
                        pattern(ctx, sound.default_frequency, volume)
                    }
                } catch (e) {
                    console.warn("Audio notification failed:", e)
                }
            },
            setVolume(v) {
                volume = Math.max(0, Math.min(1, v))
                localStorage.setItem('connect_audio_volume', String(volume))
            },
            setEnabled(e) {
                enabled = e
                localStorage.setItem('connect_audio_enabled', String(e))
            },
            getVolume() { return volume },
            isEnabled() { return enabled },
            getSounds() { return NOTIFICATION_SOUNDS },
            preview(soundKey) { this.play(soundKey) },
        }
    },
}

registry.category("services").add("connect_audio", audioNotificationService)
