/** @odoo-module **/

import { Component, useState, useRef, onMounted } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { rpc } from "@web/core/network/rpc";
import { standardFieldProps } from "@web/views/fields/standard_field_props";
import { useRecordObserver } from "@web/model/relational_model/utils";

export class ConnectChatThread extends Component {
    static template = "connect.ChatThread";
    static props = { ...standardFieldProps };

    setup() {
        this.orm = useService("orm");
        this.notification = useService("notification");
        this.action = useService("action");
        this.scrollRef = useRef("scrollContainer");

        this.state = useState({
            messages: [],
            loading: true,
            composerBody: "",
            sending: false,
            channelType: "sms",
            conversationId: false,
        });

        useRecordObserver(async (record) => {
            const newId = record.resId || false;
            this.state.channelType = record.data.channel_type || "sms";
            if (newId && newId !== this.state.conversationId) {
                this.state.conversationId = newId;
                await this.loadMessages(newId);
            }
        });

        onMounted(() => this.scrollToBottom());
    }

    async loadMessages(conversationId) {
        if (!conversationId) return;
        this.state.loading = true;
        try {
            const messages = await this.orm.call(
                "connect.conversation",
                "get_messages",
                [conversationId],
            );
            this.state.messages = messages;
        } catch (e) {
            this.state.messages = [];
        }
        this.state.loading = false;
        requestAnimationFrame(() => this.scrollToBottom());
    }

    scrollToBottom() {
        const el = this.scrollRef.el;
        if (el) {
            el.scrollTop = el.scrollHeight;
        }
    }

    formatTime(dateStr) {
        if (!dateStr) return "";
        const d = new Date(dateStr);
        return d.toLocaleString(undefined, {
            month: "short",
            day: "numeric",
            hour: "2-digit",
            minute: "2-digit",
        });
    }

    formatDate(dateStr) {
        if (!dateStr) return "";
        const d = new Date(dateStr);
        return d.toLocaleDateString(undefined, {
            weekday: "long",
            month: "long",
            day: "numeric",
            year: "numeric",
        });
    }

    getDateKey(dateStr) {
        if (!dateStr) return "";
        return new Date(dateStr).toDateString();
    }

    shouldShowDate(index) {
        if (index === 0) return true;
        const prev = this.state.messages[index - 1];
        const curr = this.state.messages[index];
        return this.getDateKey(prev.create_date) !== this.getDateKey(curr.create_date);
    }

    getStatusIcon(status) {
        const s = (status || "").toLowerCase();
        if (["sent", "sending"].includes(s)) return "fa-paper-plane";
        if (["delivered", "read", "received"].includes(s)) return "fa-check-circle";
        if (["queued", "deferred"].includes(s)) return "fa-clock-o";
        if (["failed", "undeliverable", "error"].includes(s)) return "fa-times-circle text-danger";
        if (s === "draft") return "fa-pencil-square-o";
        return "";
    }

    updateComposer(ev) {
        this.state.composerBody = ev.target.value;
    }

    onComposerKeydown(ev) {
        if (ev.key === "Enter" && !ev.shiftKey) {
            ev.preventDefault();
            this.sendMessage();
        }
    }

    async sendMessage() {
        const body = (this.state.composerBody || "").trim();
        if (!body || this.state.sending || !this.state.conversationId) return;
        this.state.sending = true;
        try {
            await this.orm.call(
                "connect.conversation",
                "send_message",
                [this.state.conversationId, body],
            );
            this.state.composerBody = "";
            await this.loadMessages(this.state.conversationId);
        } catch (e) {
            this.notification.add(
                e.message || "Failed to send message",
                { type: "danger" },
            );
        }
        this.state.sending = false;
    }

    onInstallSms() {
        this.action.doAction({
            type: "ir.actions.act_window",
            res_model: "ir.module.module",
            view_mode: "list,form",
            views: [[false, "list"], [false, "form"]],
            domain: [["name", "=", "connect_sms"]],
            target: "current",
        });
    }
}

export const connectChatThread = {
    component: ConnectChatThread,
};

registry.category("fields").add("connect_chat_thread", connectChatThread);
