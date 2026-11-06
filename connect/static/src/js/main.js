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
// For a provider whose SIP credential is stateful, unlike a Twilio JWT,
// without a stable per-tab identity two tabs of the same user would
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

/**
 * Mounts the phone from the server's lines. A line needs no token to be a line:
 * the server has already dropped the ones it cannot use.
 */
export async function mountPhoneLines(orm, browserSessionNonce) {
    const {lines, error} = await orm.call(
        'connect.user', 'get_phone_lines', [], {nonce: browserSessionNonce})
    if (error) {
        console.warn(error)
    }
    if (lines.length) {
        let bus = new EventBus()
        // Expose the bus globally for the systray component to access
        window.connectBus = bus
        // Click-to-call from a form dials in this browser when the
        // user's first callflow is the web client, and otherwise
        // asks the server to originate towards this tab's nonce.
        bus.browserSessionNonce = browserSessionNonce
        bus.clickToCallLocal = lines[0].token_data.first_callflow === 'client'
        sysTrayRegistry.add('connectPhoneSysTray', {Component: PhoneSysTray, props: {bus}})
        mainComponents.add('connectPhone', {Component: Phone, props: {bus, lines, browserSessionNonce}})
    }
    else if (!error) {
        console.log('Web Phone is not enabled for user.')
    }
}

export const phoneService = {
    dependencies: ["orm"],
    async start(env, {orm}) {
        const pathname = document.location.pathname
        if (pathname.includes("/odoo")) {
            await mountPhoneLines(orm, getBrowserSessionNonce())
        } else {
            console.log(`[Phone] Doesn't work on path: ${pathname}`)
        }
    }
}
serviceRegistry.add("ConnectPhoneService", phoneService)