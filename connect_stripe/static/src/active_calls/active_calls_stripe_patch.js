/** @odoo-module **/
import { ConnectStripePaymentDialog } from "@connect_stripe/components/payment_dialog/payment_dialog";
import { useService } from "@web/core/utils/hooks";
import { patch } from "@web/core/utils/patch";
import { ConnectActiveCallsPopup } from "@connect/services/active_calls/active_calls_popup";

patch(ConnectActiveCallsPopup.prototype, {
    setup() {
        super.setup(...arguments);
        this.dialog = useService("dialog");
    },

    _onClickTakePayment(ev, call) {
        ev.stopPropagation();
        this.dialog.add(ConnectStripePaymentDialog, {
            callId: call.id,
        });
    },
});
