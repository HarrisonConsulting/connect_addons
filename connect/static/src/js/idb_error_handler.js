/** @odoo-module **/
/**
 * Suppress harmless IndexedDB errors from Odoo's core asset/translation cache.
 *
 * When a browser tab goes idle the browser may close IndexedDB connections.
 * Any in-flight transaction then throws InvalidStateError which bubbles up as
 * an UncaughtPromiseError and triggers Odoo's error dialog — purely cosmetic.
 *
 * Registering in the error_handlers registry runs *before* the dialog, so
 * returning true swallows the error silently.
 */

import { registry } from "@web/core/registry";

function idbErrorHandler(env, error, originalError) {
    if (
        originalError instanceof DOMException &&
        originalError.name === "InvalidStateError" &&
        originalError.message &&
        originalError.message.includes("database connection is closing")
    ) {
        return true;
    }
    return false;
}

registry
    .category("error_handlers")
    .add("idbErrorHandler", idbErrorHandler, { sequence: 1 });
