/** @odoo-module **/
"use strict"
/**
 * The phone mounts from line records. A line carries its own provider, needs no
 * token to be a line, and only asks for a credential under a tool's context when
 * there is another line to tell it from.
 */

import {after, animationFrame, describe, expect, test} from "@odoo/hoot"
import {onRpc} from "@web/../tests/web_test_helpers"
import {registry} from "@web/core/registry"
import {defineMailModels} from "@mail/../tests/mail_test_helpers"
import {mountPhoneLines} from "@connect/js/main"
import {LINE_UNAVAILABLE} from "@connect/components/phone/phone/phone_line"
import {registerLoopbackProvider} from "@connect/../tests/helpers/loopback_transport"
import {SINGLE_LINE_NONCE, mountLines} from "@connect/../tests/unit/helpers/single_line_phone"

describe.current.tags("desktop")
defineMailModels()

const NEUTRAL = "neutral_provider"
const TOKEN_LINE = {
    key: "twilio",
    tool_id: 7,
    label: "Desk",
    provider: "twilio",
    media: "browser",
    token_data: {token: "first-token"},
}
const NEUTRAL_LINE = {
    key: NEUTRAL,
    tool_id: 8,
    label: "Other",
    provider: NEUTRAL,
    media: "browser",
    token_data: {token: "second-token"},
}

function registerNeutral() {
    registerLoopbackProvider(NEUTRAL)
    after(() => registry.category("connect.telephony_transports").remove(NEUTRAL))
}

/** Every credential request, as {nonce, toolId, hasToolKey}. */
function watchMints() {
    const mints = []
    onRpc("connect.user", "get_client_token", ({kwargs}) => {
        const context = kwargs.context || {}
        mints.push({
            nonce: kwargs.nonce,
            toolId: context.connect_tool_id,
            hasToolKey: "connect_tool_id" in context,
        })
        return {token: "refreshed-token"}
    })
    onRpc("connect.user", "get_phone_lines", () => {
        throw new Error("A mounted phone never asks for its lines.")
    })
    return mints
}

test("a bridge line with no token mounts, and asks the server for nothing", async () => {
    registerNeutral()
    const mints = watchMints()
    const phone = await mountLines([{
        key: NEUTRAL,
        tool_id: 9,
        label: "Sales plan",
        provider: NEUTRAL,
        media: "bridge",
        token_data: {media: "bridge", label: "Sales plan"},
    }])
    await animationFrame()
    const [line] = phone.lines
    expect(phone.lines.length).toBe(1)
    expect(line.media).toBe("bridge")
    expect(line.provider).toBe(NEUTRAL)
    expect(line.toolId).toBe(9)
    expect(mints).toEqual([])
    expect(".o_root_connect_phone").toHaveCount(1)
})

test("the provider comes from the line record, not its token", async () => {
    registerNeutral()
    const phone = await mountLines([{
        ...NEUTRAL_LINE,
        token_data: {token: "t", provider: "twilio"},
    }])
    expect(phone.provider).toBe(NEUTRAL)
    expect(phone.transport.constructor).toBe(
        registry.category("connect.telephony_transports").get(NEUTRAL))
})

test("a line with no provider says it is unavailable, in words", async () => {
    const mints = watchMints()
    const phone = await mountLines([{...TOKEN_LINE, provider: false, tool_id: false}])
    await animationFrame()
    expect(phone.transport).toBe(null)
    expect(phone.state.connectionStatus).toBe("error")
    expect(".connect-status-banner.error").toHaveCount(1)
    expect(document.querySelector(".connect-status-banner.error").textContent).toInclude(LINE_UNAVAILABLE)

    await phone._reconnect()
    expect(phone.state.connectionError).toBe(LINE_UNAVAILABLE)
    expect(mints).toEqual([])

    await phone.makeCall({phone: "+15550100"})
    expect(phone.state.inCall).toBe(false)
    expect(phone.state.lastOperationError).toBe(LINE_UNAVAILABLE)
})

test("one line refreshes and reconnects with no tool context", async () => {
    // Even a line that knows its tool: with one line the call stays what it was.
    const mints = watchMints()
    const phone = await mountLines([TOKEN_LINE])
    await animationFrame()
    expect(phone.state.connectionStatus).toBe("ready")
    await phone.lines[0].updateToken()
    await phone.lines[0].reconnect()
    expect(mints).toEqual([
        {nonce: SINGLE_LINE_NONCE, toolId: undefined, hasToolKey: false},
        {nonce: SINGLE_LINE_NONCE, toolId: undefined, hasToolKey: false},
    ])
})

test("two lines each refresh and reconnect under their own tool", async () => {
    registerNeutral()
    const mints = watchMints()
    const phone = await mountLines([TOKEN_LINE, NEUTRAL_LINE])
    await animationFrame()
    expect(phone.lines.map((line) => line.key)).toEqual(["twilio", NEUTRAL])
    await phone.lines[1].updateToken()
    await phone.lines[0].updateToken()
    await phone.lines[1].reconnect()
    expect(mints.map(({toolId}) => toolId)).toEqual([8, 7, 8])
    expect(mints.every(({nonce}) => nonce === SINGLE_LINE_NONCE)).toBe(true)
})

function fakeOrm(result) {
    const calls = []
    return {
        calls,
        async call(...args) {
            calls.push(args)
            return result
        },
    }
}

function cleanUpMount() {
    after(() => {
        registry.category("main_components").remove("connectPhone")
        registry.category("systray").remove("connectPhoneSysTray")
        delete window.connectBus
    })
}

test("the service mounts from lines, with no token gate", async () => {
    cleanUpMount()
    const lines = [{...TOKEN_LINE, media: "bridge", token_data: {media: "bridge"}}]
    const orm = fakeOrm({lines, error: false})
    await mountPhoneLines(orm, SINGLE_LINE_NONCE)
    expect(orm.calls).toEqual([["connect.user", "get_phone_lines", [], {nonce: SINGLE_LINE_NONCE}]])
    const {props} = registry.category("main_components").get("connectPhone")
    expect(props.lines).toBe(lines)
    expect(props.browserSessionNonce).toBe(SINGLE_LINE_NONCE)
    expect(registry.category("systray").contains("connectPhoneSysTray")).toBe(true)
    expect(window.connectBus.clickToCallLocal).toBe(false)
})

test("click-to-call dials locally by the first line's token data", async () => {
    cleanUpMount()
    await mountPhoneLines(fakeOrm({
        lines: [
            {...TOKEN_LINE, token_data: {token: "t", first_callflow: "client"}},
            {...NEUTRAL_LINE, token_data: {token: "t", first_callflow: "sip"}},
        ],
        error: false,
    }), SINGLE_LINE_NONCE)
    expect(window.connectBus.clickToCallLocal).toBe(true)
    expect(window.connectBus.browserSessionNonce).toBe(SINGLE_LINE_NONCE)
})

test("no lines mounts nothing", async () => {
    cleanUpMount()
    await mountPhoneLines(fakeOrm({lines: [], error: false}), SINGLE_LINE_NONCE)
    expect(registry.category("main_components").contains("connectPhone")).toBe(false)
    expect(registry.category("systray").contains("connectPhoneSysTray")).toBe(false)
})
