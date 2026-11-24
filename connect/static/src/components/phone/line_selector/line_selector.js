/** @odoo-module **/
"use strict"

import {Component} from "@odoo/owl"
import {_t} from "@web/core/l10n/translation"
import {browser} from "@web/core/browser/browser"

const SELECTED_LINE_KEY = 'connect_phone_line'

/**
 * The line this browser last chose, or null. Storage can refuse at any time, and
 * losing a remembered choice must never stop the phone.
 */
export function loadSelectedLine() {
    try {
        return browser.localStorage.getItem(SELECTED_LINE_KEY)
    } catch {
        return null
    }
}

export function saveSelectedLine(key) {
    try {
        browser.localStorage.setItem(SELECTED_LINE_KEY, key)
    } catch {
        // The choice still holds until this page is reloaded.
    }
}

/**
 * How a line's calls reach the person, in words. rings_first is the optional
 * number of the phone a bridge line rings first; only its end is ever shown.
 */
export function howLineConnects(line) {
    if (line.media !== 'bridge') {
        return _t("Uses this browser's microphone")
    }
    const ringsFirst = line.tokenData.rings_first
    return ringsFirst
        ? _t("Rings your phone ...%s first", String(ringsFirst).slice(-4))
        : _t("Rings your phone first")
}

/**
 * The From row. It only ever reports a choice: Phone holds the selection, so
 * nothing here can switch a line by itself. Each line shows its own state, whether
 * or not it is the one chosen, because the choice is only for the next call.
 */
export class LineSelector extends Component {
    static template = 'connect.line_selector'
    static props = {
        lines: Array,
        line: Object,
        callerId: {type: String, optional: true},
        states: {type: Object, optional: true},
        readonly: Boolean,
        onSelect: Function,
        onPickCallerId: Function,
        onChangeHow: Function,
    }

    get choices() {
        return this.props.lines.flatMap((line) => {
            const numbers = line.callerIds.length ? line.callerIds : [line.defaultCallerId || '']
            return numbers.map((number) => ({
                key: JSON.stringify([line.key, number]), line, number,
                label: number ? number.slice(-4) : line.label,
                kind: this.stateKind(line),
                title: [line.label, number, this.problem(line) || (this.stateKind(line) === 'connecting' && this.stateWords(line))]
                    .filter(Boolean).join(' · '),
                words: [line.label, number, this.stateWords(line)].filter(Boolean).join(' · '),
                stateLabel: `${line.label}: ${this.stateWords(line)}`,
                selected: line.key === this.props.line.key && number === (this.props.callerId || ''),
            }))
        })
    }

    get usesDropdown() {
        return this.choices.length >= 6
    }

    selectChoice(key) {
        const choice = this.choices.find((item) => item.key === key)
        if (!choice || this.props.readonly) return
        this.props.onSelect(choice.line.key)
        if (choice.number) this.props.onPickCallerId(choice.number)
    }

    onChoiceKeydown(ev, key) {
        if (this.props.readonly || !['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown', 'Home', 'End'].includes(ev.key)) return
        ev.preventDefault()
        const choices = this.choices
        const index = choices.findIndex((choice) => choice.key === key)
        const next = ev.key === 'Home' ? 0 : ev.key === 'End' ? choices.length - 1
            : (index + (['ArrowLeft', 'ArrowUp'].includes(ev.key) ? -1 : 1) + choices.length) % choices.length
        this.selectChoice(choices[next].key)
        ev.currentTarget.parentElement.querySelectorAll('[role="radio"]')[next]?.focus()
    }

    /** 'ready', 'connecting' (signing in) or 'down' for a line the phone has a state for, else nothing. */
    stateKind(line) {
        const status = this.props.states?.[line.key]?.status
        if (!status) {
            return ''
        }
        return status === 'ready' || status === 'connecting' ? status : 'down'
    }

    /** What is wrong with a line, in words, or nothing. */
    problem(line) {
        return this.stateKind(line) === 'down'
            ? this.props.states[line.key].error || _t("This line is not connected.")
            : ''
    }

    /** What a line is doing, in words that stand alone. */
    stateWords(line) {
        const kind = this.stateKind(line)
        if (kind === 'down') {
            const error = this.props.states[line.key].error
            const down = line.unavailable ? _t("not available") : _t("not connected")
            return error ? `${down}: ${error}` : down
        }
        return kind === 'ready' ? _t("ready") : kind === 'connecting' ? _t("signing in") : ''
    }

    /** The caption while the chosen line signs in. */
    get signingInWords() {
        return _t("Signing in to %s...", this.props.line.label)
    }

    get howItConnects() {
        return howLineConnects(this.props.line)
    }
}
