/** @odoo-module **/

import {useService} from "@web/core/utils/hooks"
import {setFocus} from "@connect/js/utils"
import {Component, useState, useRef, onWillStart, onMounted} from "@odoo/owl"

const searching = {
    all: 'all',
    extensions: 'extensions',
    partners: 'partners',
}

export class Contacts extends Component {
    static template = 'connect.contacts'
    static props = {
        bus: Object,
        isTransfer: {type: Boolean, optional: true},
        isContact: {type: Boolean, optional: true},
        isAddParticipant: {type: Boolean, optional: true},
        contactSearch: {type: String, optional: true},
    }

    constructor() {
        super(...arguments)
        const {bus, isTransfer = false, isContact = false, isAddParticipant = false, contactSearch = 'all'} = this.props
        this.bus = bus
        this.isTransfer = isTransfer
        this.isContact = isContact
        this.isAddParticipant = isAddParticipant
        this.contactSearch = contactSearch
        this.searchQuery = ''
        this.users = []
    }

    setup(props) {
        super.setup()
        this.orm = useService('orm')
        this.action = useService('action')
        this.busService = useService('bus_service')
        this.contactInput = useRef('contact-input')
        this.state = useState({
            isContactMode: false,
            partners: [],
            users: this.users,
        })

        onWillStart(async () => {
            this.bus.addEventListener('busContactSetState', ({detail}) => this._busContactSetState(detail))
            this.bus.addEventListener('busContactSearchQuery', ({detail}) => this._busContactSearchQuery(detail))
        })

        onMounted(() => {
            this._presenceHandler = (payload) => this._onPresenceUpdate(payload)
            this.busService.subscribe('presence_update', this._presenceHandler)
        })
    }

    _onPresenceUpdate(payload) {
        // Update presence status for matching user in current results
        const userId = payload.user_id
        const status = payload.status
        this.state.users = this.state.users.map(u => {
            if (u.id === userId) {
                return {...u, presence_status: status}
            }
            return u
        })
    }

    _busContactSetState({isTransfer = false, isContact = false, isAddParticipant = false, isContactMode = false}) {
        this.state.isContactMode = isContactMode
        this.isTransfer = isTransfer
        this.isContact = isContact
        this.isAddParticipant = isAddParticipant
        this.state.partners = []
        this.state.users = []
        this.searchQuery = ''
        if (this.contactInput.el) {
            this.contactInput.el.value = ''
            setFocus(this.contactInput.el)
        }
    }

    _busContactSearchQuery({searchQuery = ''}) {
        this.searchQuery = searchQuery
        this.searchUser()
        this.searchPartner()
    }

    _onSearchContact(ev) {
        if (ev.key === "Enter") {
            this._contactCall()
        } else {
            this._contactSearchQuery({searchQuery: ev.target.value})
        }
    }

    _onClickClearSearchContact(ev) {
        this._contactSearchQuery({searchQuery: ''})
        this.contactInput.el.value = ''
        setFocus(this.contactInput.el)
    }

    _contactSearchQuery({searchQuery = ''}) {
        this.searchQuery = searchQuery
        this.searchUser()
        this.searchPartner()
    }

    _contactCall() {
        let phoneNumber
        if (this.state.partners.length + this.state.users.length === 1) {
            if (this.state.partners.length) {
                const contact = this.state.partners[0]
                phoneNumber = contact.phone ? contact.phone : contact.mobile
            } else {
                phoneNumber = this.state.users[0].exten
            }
        } else {
            phoneNumber = this.searchQuery
        }

        if (this.isTransfer) {
            this._onClickMakeTransfer(phoneNumber)
        } else if (this.isContact) {
            this._onClickMakeCall(phoneNumber)
        } else if (this.isAddParticipant) {
            this._onClickAddParticipant(phoneNumber)
        }
    }

    searchPartner() {
        if (this.contactSearch !== searching.all && this.contactSearch !== searching.partners) return
        const self = this
        if (self.searchQuery) {
            // Search across multiple fields: name, company, email, phone, mobile
            self.orm.searchRead(
                "res.partner",
                [
                    '|', '|', '|', '|',
                    ['name', '=ilike', `%${self.searchQuery}%`],
                    ['commercial_company_name', '=ilike', `%${self.searchQuery}%`],
                    ['email', '=ilike', `%${self.searchQuery}%`],
                    ['phone', '=ilike', `%${self.searchQuery}%`],
                    ['mobile', '=ilike', `%${self.searchQuery}%`],
                ],
                ['id', 'name', 'email', 'phone', 'mobile', 'phone_sanitized', 'commercial_company_name'],
                {order: 'name asc', limit: 10}
            ).then((records) => {
                self.state.partners = records
            })
        } else {
            self.state.partners = []
        }
    }

    searchUser() {
        if (this.contactSearch !== searching.all && this.contactSearch !== searching.extensions) return
        const self = this
        if (self.searchQuery) {
            self.orm.searchRead(
                "connect.user",
                [
                    '|', ['exten_number', '=ilike', `%${self.searchQuery}%`],
                    ['user', '=ilike', `%${self.searchQuery}%`]
                ],
                ['id', 'name', 'exten_number', 'user', 'presence_status'],
                {order: 'exten_number asc', limit: 10}
            ).then((records) => {
                self.state.users = records
            })
        } else {
            self.state.users = []
        }
    }

    _onClickMakeCall(phoneNumber) {
        this.bus.trigger('busPhoneMakeCall', {phone: phoneNumber})
    }

    _onClickMakeTransfer(phoneNumber) {
        this.bus.trigger('busPhoneMakeTransfer', {phoneNumber})
    }

    _onClickAddParticipant(phoneNumber) {
        this.bus.trigger('busPhoneAddParticipant', {phoneNumber})
    }

    _openPartner(id) {
        this.action.doAction({
            res_id: id,
            res_model: "res.partner",
            target: 'new',
            type: 'ir.actions.act_window',
            views: [[false, 'form']],
        })
    }
}
