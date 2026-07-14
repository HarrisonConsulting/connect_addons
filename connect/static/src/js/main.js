/** @odoo-module **/
import {registry} from "@web/core/registry"
import {PhoneSysTray} from "@connect/components/phone/tray/tray"
import {Phone} from "@connect/components/phone/phone/phone"
import {user} from "@web/core/user"

const uid = user.userId
const serviceRegistry = registry.category("services")
const sysTrayRegistry = registry.category("systray")
const mainComponents = registry.category("main_components")
import {EventBus} from "@odoo/owl"

// Identifies this browser tab across the get_client_token() calls it makes
// over its lifetime (initial mount here, plus phone.js's later reconnect/
// token-refresh calls, which reuse the same value via the Phone prop below).
// VoiceTel-only: a VoiceTel SIP credential is stateful, unlike a Twilio JWT,
// so without a stable per-tab identity two tabs of the same user would
// rotate one shared SIP password out from under each other. sessionStorage
// is per-tab (unlike localStorage), so this survives reloads within a tab
// but not across tabs or after the tab closes.
function getBrowserSessionNonce() {
    const key = 'connect_browser_session_nonce'
    let nonce = window.sessionStorage.getItem(key)
    if (!nonce) {
        nonce = crypto.randomUUID()
        window.sessionStorage.setItem(key, nonce)
    }
    return nonce
}

export const phoneService = {
    dependencies: ["orm"],
    async start(env, {orm}) {
        const pathname = document.location.pathname
        if (pathname.includes("/odoo")) {
            const browserSessionNonce = getBrowserSessionNonce()
            const token_data = await orm.call(
                'connect.user', 'get_client_token', [], {nonce: browserSessionNonce})
            // Twilio responses carry 'token', VoiceTel responses carry
            // 'password' instead (no 'token' key at all) — either means a
            // usable credential was minted and the phone should mount.
            if (token_data.token || token_data.password) {
                let bus = new EventBus()
                // Expose the bus globally for the systray component to access
                window.connectBus = bus
                sysTrayRegistry.add('connectPhoneSysTray', {Component: PhoneSysTray, props: {bus}})
                mainComponents.add('connectPhone', {Component: Phone, props: {bus, token_data, browserSessionNonce}})
            }
            else if (token_data.error) {
                console.warn(token_data.error)
            }
            else {
                console.log('Web Phone is not enabled for user.')
            }
        } else {
            console.log(`[Phone] Doesn't work on path: ${pathname}`)
        }
    }
}
serviceRegistry.add("ConnectPhoneService", phoneService)