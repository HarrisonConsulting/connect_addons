/** @odoo-module **/
import {expect, test} from "@odoo/hoot"
import {Phone} from "@connect/components/phone/phone/phone"
import {LineSelector} from "@connect/components/phone/line_selector/line_selector"

function deferred() {
    let resolve
    const promise = new Promise((done) => { resolve = done })
    return {promise, resolve}
}
function phone() {
    const result = Object.create(Phone.prototype)
    Object.assign(result, {
        state: {phoneNumber: '', dialSuggestions: [], dialSuggestionIndex: -1, inCall: false, inIncoming: false},
        _dialQueryVersion: 0, _dialHistory: null, _dialHistoryIndex: -1, _dialDraft: '',
        phoneInput: {el: {value: '', focus() {}}},
        debounceEnterPhoneNumber() {},
        orm: {call: async () => [], searchRead: async () => []},
        dials: [],
        _onClickMakeCall() { this.dials.push(this.state.phoneNumber) },
    })
    return result
}
function input(p, value) {
    p.phoneInput.el.value = value
    p._onDialInput({target: p.phoneInput.el})
}
function key(p, value) {
    return p._onDialKeydown({key: value, preventDefault() {}, stopPropagation() {}})
}
const ALICE = {key: 'alice', name: 'Alice', number: '+15745646338'}

test('Enter flushes pending name lookup, fills the full number, and only a later Enter dials', async () => {
    const p = phone()
    const pending = deferred()
    p.orm.call = () => pending.promise
    input(p, 'Ali')
    const enter = key(p, 'Enter')
    expect(p.dials).toEqual([])
    pending.resolve([ALICE])
    await enter
    expect(p.state.phoneNumber).toBe(ALICE.number)
    expect(p.dials).toEqual([])
    await key(p, 'Enter')
    expect(p.dials).toEqual([ALICE.number])
})

test('Down chooses a suggestion and Enter fills without dialing', async () => {
    const p = phone()
    input(p, 'Alice')
    p.state.dialSuggestions = [ALICE, {...ALICE, key: 'second', number: '+442079460018'}]
    await key(p, 'ArrowDown')
    expect(p.state.dialSuggestionIndex).toBe(0)
    await key(p, 'ArrowDown')
    await key(p, 'Enter')
    expect(p.state.phoneNumber).toBe('+442079460018')
    expect(p.dials).toEqual([])
})

test('stale suggestions cannot replace newer edits, completion, incoming state or unmount', async () => {
    for (const change of [
        (p) => input(p, 'Bob'),
        (p) => p._completeDialSuggestion({...ALICE, number: '+442079460018'}),
        (p) => { p.state.inIncoming = true },
        (p) => { p._dialDisposed = true },
    ]) {
        const p = phone()
        const pending = deferred()
        p.orm.call = () => pending.promise
        input(p, 'Alice')
        const request = p._searchDialSuggestions()
        change(p)
        pending.resolve([ALICE])
        await request
        expect(p.state.dialSuggestions).toEqual([])
    }
})

test('history Up walks recent unique numbers; Down returns the exact draft', async () => {
    const p = phone()
    p.orm.searchRead = async () => [{called: '+15550001'}, {called: '+15550001'}, {called: '+15550002'}]
    input(p, '  (574) 564-6338 x12  ')
    await key(p, 'ArrowUp')
    expect(p.state.phoneNumber).toBe('+15550001')
    await key(p, 'ArrowUp')
    expect(p.state.phoneNumber).toBe('+15550002')
    await key(p, 'ArrowDown')
    await key(p, 'ArrowDown')
    expect(p.state.phoneNumber).toBe('  (574) 564-6338 x12  ')
    expect(p.phoneInput.el.value).toBe(p.state.phoneNumber)
})

test('history reply cannot overwrite edits or a Down cancelling the pending Up', async () => {
    for (const cancel of [(p) => input(p, 'new draft'), (p) => key(p, 'ArrowDown')]) {
        const p = phone()
        const pending = deferred()
        p.orm.searchRead = () => pending.promise
        input(p, 'original')
        const request = key(p, 'ArrowUp')
        await cancel(p)
        const expected = p.state.phoneNumber
        pending.resolve([{called: '+15550001'}])
        await request
        expect(p.state.phoneNumber).toBe(expected)
    }
})

test('empty history and Down without choices preserve input; unmatched names never dial', async () => {
    const p = phone()
    input(p, 'Nobody')
    await key(p, 'ArrowDown')
    await key(p, 'ArrowUp')
    expect(p.state.phoneNumber).toBe('Nobody')
    await key(p, 'Enter')
    expect(p.dials).toEqual([])
})

test('an Enter lookup finishing after an edit cannot dial that edit', async () => {
    const p = phone()
    const pending = deferred()
    p.orm.call = () => pending.promise
    input(p, '574')
    const enter = key(p, 'Enter')
    input(p, '5745646338')
    pending.resolve([])
    await enter
    expect(p.dials).toEqual([])
})

function selector(lines) {
    const result = Object.create(LineSelector.prototype)
    result.props = {
        lines, line: lines[0], callerId: lines[0].callerIds[0], problems: {}, readonly: false,
        onSelect(key) { result.props.line = lines.find((line) => line.key === key) },
        onPickCallerId(number) { result.props.callerId = number },
    }
    return result
}
function line(key, callerIds) {
    return {key, callerIds, defaultCallerId: callerIds[0] || '', label: key}
}

test('number choices work within one provider and across providers with full hover identity', () => {
    for (const lines of [[line('Twilio', ['+15745646338', '+15550002222'])],
        [line('Twilio', ['+15745646338']), line('VoiceTel', ['+15550002222'])]]) {
        const s = selector(lines)
        expect(s.choices.map((choice) => choice.label)).toEqual(['6338', '2222'])
        s.selectChoice(s.choices[1].key)
        expect(s.props.callerId).toBe('+15550002222')
        expect(s.choices[1].selected).toBe(true)
        expect(s.choices[1].title).toBe(`${s.props.line.label} · +15550002222`)
    }
})

test('radio arrows wrap and move focus; readonly selection refuses changes', () => {
    const s = selector([line('Twilio', ['+15745646338', '+15550002222'])])
    let focused = -1
    const buttons = [0, 1].map((index) => ({focus() { focused = index }}))
    s.onChoiceKeydown({key: 'ArrowLeft', preventDefault() {}, currentTarget: {parentElement: {querySelectorAll: () => buttons}}}, s.choices[0].key)
    expect(focused).toBe(1)
    expect(s.choices[1].selected).toBe(true)
    s.props.readonly = true
    s.selectChoice(s.choices[0].key)
    expect(s.choices[1].selected).toBe(true)
})

test('keypad and backspace invalidate pending replies without moving focus to Contacts', async () => {
    const p = phone()
    const pending = deferred()
    p.orm.call = () => pending.promise
    input(p, '57')
    const request = p._searchDialSuggestions()
    p._onClickKeypadButton({target: {textContent: '4'}})
    expect(p.state.phoneNumber).toBe('574')
    expect(p.state.isContactList).toBe(false)
    p._onClickBackSpace()
    expect(p.state.phoneNumber).toBe('57')
    pending.resolve([ALICE])
    await request
    expect(p.state.dialSuggestions).toEqual([])
})

test('in-call keypad sends only DTMF; incoming phone does not search or navigate history', async () => {
    const p = phone()
    const tones = []
    p.sendDTMF = (tone) => tones.push(tone)
    p.state.inCall = true
    await key(p, 'Enter')
    await key(p, 'ArrowUp')
    await key(p, '5')
    expect(tones).toEqual(['5'])
    p.state.inCall = false
    p.state.inIncoming = true
    p.orm.call = () => { throw new Error('unexpected search') }
    p.orm.searchRead = () => { throw new Error('unexpected history') }
    await p._searchDialSuggestions()
    await key(p, 'ArrowUp')
    expect(p.state.phoneNumber).toBe('')
})

test('a trailing debounce after completion cannot reopen suggestions for the filled number', async () => {
    const p = phone()
    input(p, 'Alice')
    p._completeDialSuggestion(ALICE)
    let queries = 0
    p.orm.call = async () => { queries++; return [ALICE] }
    await p._searchDialSuggestions()
    expect(queries).toBe(0)
    expect(p.state.dialSuggestions).toEqual([])
})

test('Enter on a numeric match completes the matched mobile and a second Enter dials it', async () => {
    const p = phone()
    const mobile = {...ALICE, number: '+12025550124'}
    p.orm.call = async () => [mobile]
    input(p, '2025550124')
    await key(p, 'Enter')
    expect(p.state.phoneNumber).toBe(mobile.number)
    expect(p.dials).toEqual([])
    await key(p, 'Enter')
    expect(p.dials).toEqual([mobile.number])
})

test('outbound validation refuses a PSTN extension before SDK, lookup and history while retaining input', async () => {
    const p = phone()
    const original = '+12025550123x123'
    input(p, original)
    const words = []
    p._refuseDial = (message) => words.push(message)
    p.orm.call = async () => { throw {data: {message: 'Call the main number first, then enter the extension with the in-call keypad.'}} }
    p.searchPartner = () => { throw new Error('unexpected partner lookup') }
    p.makeCall = () => { throw new Error('unexpected provider/history') }
    await p.prepareCall({phone: original})
    expect(p.state.phoneNumber).toBe(original)
    expect(words.length).toBe(1)
    expect(p._preparingCall).toBe(false)
    // Direct makeCall callers retain the same boundary before line/provider access.
    await Phone.prototype.makeCall.call(p, {phone: original})
    expect(words.length).toBe(2)
})

test('keypad Call keeps the exact draft, suggestions and history until validation accepts it', async () => {
    const p = phone()
    const original = '  (202) 555-0123 ext. 123  '
    input(p, original)
    const history = ['+12025550124']
    p._dialHistory = history
    p._dialDraft = original
    p.state.dialSuggestions = [ALICE]
    p.state.callPhoneNumber = 'previous destination'
    const pending = deferred()
    p.orm.call = async () => {
        await pending.promise
        throw {data: {message: 'Use the in-call keypad for the extension.'}}
    }
    const words = []
    p._refuseDial = (message) => words.push(message)
    p.searchPartner = () => { throw new Error('unexpected lookup') }
    p.makeCall = () => { throw new Error('unexpected provider/history') }
    const attempt = Phone.prototype._onClickMakeCall.call(p)
    expect(p.phoneInput.el.value).toBe(original)
    expect(p.state.phoneNumber).toBe(original)
    expect(p.state.dialSuggestions).toEqual([ALICE])
    expect(p._dialHistory).toEqual(history)
    pending.resolve()
    await attempt
    expect(p.phoneInput.el.value).toBe(original)
    expect(p.state.phoneNumber).toBe(original)
    expect(p._dialDraft).toBe(original)
    expect(p.state.dialSuggestions).toEqual([ALICE])
    expect(p._dialHistory).toEqual(history)
    expect(p.state.callPhoneNumber).toBe('previous destination')
    expect(words.length).toBe(1)
})

test('accepted keypad draft clears only after validation and preserves newer edits', async () => {
    for (const edit of [false, true]) {
        const p = phone()
        input(p, '(202) 555-0123')
        p._dialHistory = ['+12025550124']
        const pending = deferred()
        p.orm.call = () => pending.promise
        p.searchPartner = async () => {}
        p.makeCall = (props) => p.dials.push(props.phone)
        const attempt = Phone.prototype._onClickMakeCall.call(p)
        expect(p.state.phoneNumber).toBe('(202) 555-0123')
        if (edit) input(p, 'new draft')
        pending.resolve('+12025550123')
        await attempt
        expect(p.state.phoneNumber).toBe(edit ? 'new draft' : '')
        expect(p.phoneInput.el.value).toBe(edit ? 'new draft' : '')
        expect(p.dials).toEqual(['+12025550123'])
    }
})
