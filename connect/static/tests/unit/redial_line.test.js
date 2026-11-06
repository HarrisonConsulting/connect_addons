/** @odoo-module **/
"use strict"
/**
 * A call from History or Favorites goes out on its own line, once. The selection
 * stays, and a line that has gone is said out loud rather than swapped for another.
 */

import {after, animationFrame, describe, expect, test} from "@odoo/hoot"
import {click, waitUntil} from "@odoo/hoot-dom"
import {onRpc, patchWithCleanup} from "@web/../tests/web_test_helpers"
import {browser} from "@web/core/browser/browser"
import {registry} from "@web/core/registry"
import {user} from "@web/core/user"
import {defineMailModels} from "@mail/../tests/mail_test_helpers"
import {LoopbackTransport, registerLoopbackProvider} from "@connect/../tests/helpers/loopback_transport"
import {mountLines} from "@connect/../tests/unit/helpers/single_line_phone"

describe.current.tags("desktop")
defineMailModels()

const NEUTRAL = "neutral_provider"
const NUMBER = "+15550199"
const STORAGE_KEY = "connect_phone_line"

function makeLine(key, label) {
    return {
        key,
        tool_id: 1,
        label,
        provider: key === "desk" ? "twilio" : NEUTRAL,
        media: "browser",
        capabilities: {},
        token_data: {token: `${key}-token`},
    }
}

function historyRow(id, line_key = false, line_label = false) {
    return {
        id,
        called: NUMBER,
        caller: "+15550100",
        caller_user: [user.userId, "Me"],
        called_users: [],
        partner: false,
        create_date: "2026-11-05 10:00:00",
        direction: "outgoing",
        line_key,
        line_label,
    }
}

/** Records each dial with the transport and the line showing when it went out. */
function watchDials(getPhone) {
    const dials = []
    patchWithCleanup(LoopbackTransport.prototype, {
        async dial(params) {
            dials.push({transport: this, params: {...params}, showing: getPhone().activeLine.key})
            return super.dial(params)
        },
    })
    return dials
}

async function mount(lines, {history = []} = {}) {
    onRpc("res.partner", "api_get_partner", () => false)
    onRpc("connect.user", "get_user_by_exten_number", () => false)
    onRpc("connect.call", "get_widget_calls", () => history.map((row) => ({...row})))
    registerLoopbackProvider(NEUTRAL)
    after(() => registry.category("connect.telephony_transports").remove(NEUTRAL))
    after(() => browser.localStorage.removeItem(STORAGE_KEY))
    const created = []
    onRpc("connect.favorite", "search", () => [])
    onRpc("connect.favorite", "create", ({args}) => {
        created.push(...args[0])
        return [1]
    })
    const phone = await mountLines(lines)
    await animationFrame()
    return {phone, created}
}

const TWO = () => [makeLine("desk", "Desk"), makeLine("sales", "Sales")]

async function openHistory() {
    await click("button[aria-label='History']")
    await waitUntil(() => document.querySelector(".o_history_table"))
    await animationFrame()
}

async function endCall(phone) {
    phone.session.disconnect()
    await waitUntil(() => phone.session === null && !phone.state.inCall)
    await animationFrame()
}

test("history names each call's line, by the name on the From row", async () => {
    await mount(
        [makeLine("desk", "Desk"), {...makeLine("sales", "Sales"), label: "Sales floor"}],
        {history: [historyRow(1, "sales", "Tool name"), historyRow(2)]},
    )
    await openHistory()
    expect(".o_call_line_label").toHaveCount(1)
    expect(".o_call_line_label").toHaveText("Sales floor")
})

test("a call on a line that has gone keeps its name in history", async () => {
    await mount(TWO(), {history: [historyRow(1, "night", "Night line")]})
    await openHistory()
    expect(".o_call_line_label").toHaveText("Night line")
})

test("one line shows no label, whatever the row says", async () => {
    await mount([makeLine("desk", "Desk")], {history: [historyRow(1, "desk", "Desk")]})
    await openHistory()
    expect(".o_history_table").toHaveCount(1)
    expect(".o_call_line_label").toHaveCount(0)
})

test("a redial uses the call's line once and leaves the selection", async () => {
    let phone
    const dials = watchDials(() => phone)
    ;({phone} = await mount(TWO(), {history: [historyRow(1, "sales", "Sales")]}))
    await openHistory()
    await click(".o_history_table .info-card")
    await waitUntil(() => dials.length > 0)
    expect(dials[0].transport).toBe(phone.lines[1].transport)
    expect(dials[0].params).toEqual({To: NUMBER, Called: NUMBER})
    expect(dials[0].showing).toBe("sales")
    await animationFrame()
    expect(phone.lines[1].session).not.toBe(null)
    expect(phone.state.selectedLineKey).toBe("desk")
    expect(browser.localStorage.getItem(STORAGE_KEY)).toBe(null)
    await endCall(phone)
    expect(phone.activeLine.key).toBe("desk")
    expect(".o_phone_line_chip.active").toHaveText("Desk")
    // Once: the next plain dial is on the selection again.
    phone.bus.trigger("busPhoneMakeCall", {phone: NUMBER})
    await waitUntil(() => dials.length > 1)
    expect(dials[1].transport).toBe(phone.lines[0].transport)
})

test("a call with no line redials on the visible selection", async () => {
    let phone
    const dials = watchDials(() => phone)
    ;({phone} = await mount(TWO(), {history: [historyRow(1)]}))
    await click(".o_phone_line_chip:last")
    await animationFrame()
    await openHistory()
    await click(".o_history_table .info-card")
    await waitUntil(() => dials.length > 0)
    expect(dials[0].transport).toBe(phone.lines[1].transport)
    expect(phone.state.lastOperationError).toBe(false)
})

test("a line that has gone says so, dials nothing and moves nothing", async () => {
    let phone
    const dials = watchDials(() => phone)
    ;({phone} = await mount(TWO(), {history: [historyRow(1, "night", "Night line")]}))
    await click(".o_phone_line_chip:last")
    await animationFrame()
    await openHistory()
    await click(".o_history_table .info-card")
    await waitUntil(() => phone.state.lastOperationError)
    await animationFrame()
    expect(dials).toEqual([])
    expect(phone.state.inCall).toBe(false)
    expect(phone.state.selectedLineKey).toBe("sales")
    expect(".connect-operation-error").toHaveCount(1)
    expect(".connect-operation-error").toHaveText(/no longer one of your lines/)
})

test("a line that has dropped refuses by name, and no other line takes the call", async () => {
    let phone
    const dials = watchDials(() => phone)
    ;({phone} = await mount(TWO(), {history: [historyRow(1, "sales", "Sales")]}))
    phone.lines[1].transport = null
    await openHistory()
    await click(".o_history_table .info-card")
    await waitUntil(() => phone.state.lastOperationError)
    expect(dials).toEqual([])
    expect(phone.state.lastOperationError).toInclude("Sales")
    expect(phone.state.selectedLineKey).toBe("desk")
})

test("one line dials its one line, whatever the row says", async () => {
    let phone
    const dials = watchDials(() => phone)
    ;({phone} = await mount([makeLine("desk", "Desk")], {history: [historyRow(1, "night", "Night line")]}))
    await openHistory()
    await click(".o_history_table .info-card")
    await waitUntil(() => dials.length > 0)
    expect(dials[0].transport).toBe(phone.lines[0].transport)
    expect(dials[0].params).toEqual({To: NUMBER, Called: NUMBER})
    expect(phone.state.lastOperationError).toBe(false)
})

test("starring a call keeps its line; a call with none stores none", async () => {
    const {created} = await mount(TWO(), {history: [historyRow(1, "sales", "Sales"), historyRow(2)]})
    await openHistory()
    await click(".o_history_table tr:nth-child(1) .o_favorite button")
    await waitUntil(() => created.length > 0)
    expect(created[0].line_key).toBe("sales")
    await click(".o_history_table tr:nth-child(2) .o_favorite button")
    await waitUntil(() => created.length > 1)
    expect("line_key" in created[1]).toBe(false)
})

function favoriteRow(id, line_key = false) {
    return {id, name: `Favorite ${id}`, partner: false, user: false, phone_number: NUMBER, line_key}
}

async function openFavorites(rows, lines = TWO()) {
    const box = await mount(lines)
    // The tab loads when it opens, and this answer replaces the helper's empty one.
    // It hands back only what was asked for, as the server does.
    onRpc("connect.favorite", "search_read", ({kwargs}) => rows.map((row) => Object.fromEntries(
        kwargs.fields.map((field) => [field, row[field]])
    )))
    return box
}

test("a favorite with a line uses it once and leaves the selection", async () => {
    let phone
    const dials = watchDials(() => phone)
    ;({phone} = await openFavorites([favoriteRow(1, "sales")]))
    await click("button[aria-label='Favorites']")
    await waitUntil(() => document.querySelector(".o_favorites_table tr"))
    await click(".o_favorites_table tr")
    await waitUntil(() => dials.length > 0)
    expect(dials[0].transport).toBe(phone.lines[1].transport)
    expect(phone.state.selectedLineKey).toBe("desk")
    expect(browser.localStorage.getItem(STORAGE_KEY)).toBe(null)
})

test("a favorite with no line uses the visible selection", async () => {
    let phone
    const dials = watchDials(() => phone)
    ;({phone} = await openFavorites([favoriteRow(1)]))
    await click(".o_phone_line_chip:last")
    await animationFrame()
    await click("button[aria-label='Favorites']")
    await waitUntil(() => document.querySelector(".o_favorites_table tr"))
    await click(".o_favorites_table tr")
    await waitUntil(() => dials.length > 0)
    expect(dials[0].transport).toBe(phone.lines[1].transport)
})

test("a favorite whose line has gone says so and dials nothing", async () => {
    let phone
    const dials = watchDials(() => phone)
    ;({phone} = await openFavorites([favoriteRow(1, "night")]))
    await click("button[aria-label='Favorites']")
    await waitUntil(() => document.querySelector(".o_favorites_table tr"))
    await click(".o_favorites_table tr")
    await waitUntil(() => phone.state.lastOperationError)
    expect(dials).toEqual([])
    expect(phone.state.inCall).toBe(false)
    expect(phone.state.lastOperationError).toInclude("no longer one of your lines")
})
