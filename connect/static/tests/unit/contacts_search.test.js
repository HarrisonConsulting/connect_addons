/** @odoo-module **/
"use strict"

import {animationFrame, describe, expect, test} from "@odoo/hoot"
import {EventBus} from "@odoo/owl"
import {mountWithCleanup, onRpc} from "@web/../tests/web_test_helpers"
import {defineMailModels} from "@mail/../tests/mail_test_helpers"
import {Contacts} from "@connect/components/phone/contacts/contacts"

describe.current.tags("desktop")
defineMailModels()

const ANN = {id: 7, name: "Ann Lee", user_id: 42, exten_number: "101"}

function mockSearch(records) {
    const queries = []
    onRpc("connect.user", "search_directory", ({args}) => {
        queries.push(args[0])
        return records
    })
    onRpc("connect.user", "search_read", () => {
        throw new Error("connect.user must not be read directly")
    })
    onRpc("res.partner", "search_read", () => [])
    return queries
}

async function search(query, props = {}) {
    const bus = new EventBus()
    const contacts = await mountWithCleanup(Contacts, {
        props: {bus, isContact: true, ...props},
    })
    bus.trigger("busContactSearchQuery", {searchQuery: query})
    await animationFrame()
    await animationFrame()
    return {bus, contacts}
}

test("a colleague is found through the directory and takes the template's shape", async () => {
    const queries = mockSearch([ANN])
    const {contacts} = await search("ann")
    expect(queries).toEqual(["ann"])
    expect(contacts.state.users).toEqual([{...ANN, user: [42, "Ann Lee"]}])
})

test("the row shows the colleague, offline, and dials the extension", async () => {
    mockSearch([ANN])
    await search("ann")
    expect(".o_contact_table img.o_contact_avatar").toHaveCount(1)
    expect(".o_presence_indicator").toHaveClass("offline")
    expect(".o_presence_indicator").toHaveAttribute("title", "offline")
    expect(".o_contact_table").toHaveText(/Ann Lee/)
    expect(".o_contact_phone").toHaveText("101")
    expect("button[title='Call to 101']").toHaveCount(1)
})

test("a colleague whose presence is known shows it", async () => {
    mockSearch([{...ANN, presence_status: "available"}])
    await search("ann")
    expect(".o_presence_indicator").toHaveClass("available")
    expect(".o_presence_indicator").toHaveAttribute("title", "available")
})

test("enter on one result dials that colleague's extension", async () => {
    mockSearch([ANN])
    const {bus, contacts} = await search("ann")
    const dialled = []
    bus.addEventListener("busPhoneMakeCall", ({detail}) => dialled.push(detail.phone))
    contacts._contactCall()
    expect(dialled).toEqual(["101"])
})

test("a long unbroken name stays inside the phone and the call button remains visible", async () => {
    onRpc("connect.user", "search_directory", () => [])
    onRpc("res.partner", "search_read", () => [{
        id: 9,
        name: "david.porter@voraventures.com",
        email: "david.porter@voraventures.com",
        phone: "+1 216-501-0317",
        mobile: false,
        commercial_company_name: "Yoder Connections, LLC",
    }])
    await search("dav")
    const panel = document.querySelector(".o_contacts_panel")
    const shell = document.createElement("div")
    const body = document.createElement("div")
    shell.className = "o_root_connect_phone"
    body.className = "o_body_phone"
    shell.appendChild(body)
    body.appendChild(panel)
    document.body.appendChild(shell)
    try {
        await animationFrame()
        const list = panel.querySelector(".o_dial_contact_list")
        const button = panel.querySelector(".o_contact_call_button button")
        const listBox = list.getBoundingClientRect()
        const buttonBox = button.getBoundingClientRect()
        expect(list.clientWidth).toBeGreaterThan(250)
        expect(list.scrollWidth).toBeLessThan(list.clientWidth + 2)
        expect(buttonBox.right).toBeLessThan(listBox.right + 2)
        expect(buttonBox.left).toBeGreaterThan(listBox.left - 1)
        const input = panel.querySelector(".o_phone_number_input")
        input.parentElement.classList.remove("o_hide")
        expect(getComputedStyle(input).fontSize).toBe("14px")
        expect(panel.querySelector(".o_contact_name_text")).toHaveText("david.porter@voraventures.com")
    } finally {
        shell.remove()
    }
})

test("an empty search asks nothing and clears the list", async () => {
    const queries = mockSearch([ANN])
    const {bus, contacts} = await search("ann")
    bus.trigger("busContactSearchQuery", {searchQuery: ""})
    await animationFrame()
    expect(queries).toEqual(["ann"])
    expect(contacts.state.users).toEqual([])
})

test("a partners-only search does not ask the directory", async () => {
    const queries = mockSearch([ANN])
    await search("ann", {contactSearch: "partners"})
    expect(queries).toEqual([])
})
