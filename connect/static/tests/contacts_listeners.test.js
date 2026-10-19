import {expect, test} from "@odoo/hoot"
import {animationFrame} from "@odoo/hoot-mock"
import {makeMockEnv, mockService, mountWithCleanup} from "@web/../tests/web_test_helpers"
import {Component, EventBus, useState, xml} from "@odoo/owl"
import {Contacts} from "@connect/components/phone/contacts/contacts"


// Regression: Contacts used to register its busContact* handlers with a bare
// addEventListener on the page-lifetime phone bus and never removed them, so
// a search triggered after the panel was destroyed ran against a dead
// component ("Component is destroyed"). Upstream NG still has the bug.
test.tags("desktop")
test("destroyed Contacts ignores busContactSearchQuery and busContactSetState", async () => {
    const bus = new EventBus()
    const calls = []
    mockService("orm", {
        searchRead: (...args) => {
            calls.push(args)
            return Promise.resolve([])
        },
    })
    mockService("action", {doAction() {}})
    mockService("bus_service", {subscribe() {}, unsubscribe() {}})
    await makeMockEnv()

    class Host extends Component {
        static components = {Contacts}
        static props = ["*"]
        static template = xml`<div><Contacts t-if="state.show" bus="bus"/></div>`
        setup() {
            this.bus = bus
            this.state = useState({show: true})
        }
    }
    const host = await mountWithCleanup(Host)

    // Sanity: while mounted, the listener is live and searches.
    bus.trigger("busContactSearchQuery", {searchQuery: "x"})
    await animationFrame()
    expect(calls.length).toBeGreaterThan(0)

    host.state.show = false
    await animationFrame()
    calls.length = 0

    const errors = []
    const onError = (ev) => errors.push(ev)
    window.addEventListener("unhandledrejection", onError)
    try {
        bus.trigger("busContactSearchQuery", {searchQuery: "x"})
        bus.trigger("busContactSetState", {isContactMode: true})
        await animationFrame()
    } finally {
        window.removeEventListener("unhandledrejection", onError)
    }

    expect(calls).toHaveLength(0)
    expect(errors).toHaveLength(0)
})
