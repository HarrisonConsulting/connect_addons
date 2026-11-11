/** Actual service/phone handlers and template predicates under controlled RPCs.
 * Mounted OWL and real ORM coverage live in the module's Hoot/Python suites. */
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
let PhonePatch;
const context = vm.createContext({
    reactive: (state) => state,
    registry: {category: () => ({add() {}})}, browser: {addEventListener() {}},
    patch: (target, extension) => { PhonePatch = extension; }, PhoneField: class {},
    _t: (x) => x, user: {context: {}},
});
function load(path) {
    const source = fs.readFileSync(path, 'utf8').replace(/^import .*$/gm, '').replace(/^export /gm, '');
    vm.runInContext(source, context, {filename: path});
}
load('connect_twilio/static/src/widgets/phone_field/phone_field.js');
const create = context.createWhatsAppCapabilities;
const template = fs.readFileSync('connect_twilio/static/src/widgets/phone_field/phone_field.xml', 'utf8');
const conditions = [...template.matchAll(/<a\s+t-if="([^"]+)"[^>]*title="([^"]+)"/g)];
assert.equal(conditions.length, 2);
function visible(service, number = '+15550000000') {
    return conditions.filter(([, condition]) => {
        const expression = condition.replace(/\band\b/g, '&&').replace(/\bnot\b/g, '!');
        return vm.runInNewContext(expression, {
            props: {name: 'phone', record: {data: {phone: number}}}, whatsappCapabilities: service.state,
        });
    }).map(([, , label]) => label);
}
(async () => {
    let resolve;
    const wait = new Promise((done) => { resolve = done; });
    let calls = 0;
    let response = {call: false, message: true};
    const service = create({call: async (model, method, args) => {
        calls++;
        assert.equal(model, 'connect.user'); assert.equal(method, 'get_whatsapp_capabilities');
        assert.deepEqual([...args], []);
        await wait;
        return response;
    }});
    assert.deepEqual(visible(service), []);
    const loading = service.refresh();
    const shared = service.refresh();
    assert.deepEqual(visible(service), []);
    resolve(); await loading; await shared;
    assert.equal(calls, 1);
    assert.deepEqual(visible(service), ['Send WhatsApp Message']);
    response = {call: true, message: true}; await service.refresh();
    assert.deepEqual(visible(service), ['WhatsApp Call', 'Send WhatsApp Message']);
    assert.deepEqual(visible(service, ''), []);
    response = {call: false, message: false}; await service.refresh();
    assert.deepEqual(visible(service), []);
    const failed = create({call() { throw new Error('read denied'); }});
    await failed.refresh(); assert.deepEqual(visible(failed), []);
    await failed.refresh(); assert.equal(failed.state.pending, false);
    let originates = 0, saves = 0, actions = 0;
    const warnings = [];
    const field = Object.assign(Object.create(PhonePatch), {
        whatsapp: service,
        notification: {add(message) { warnings.push(message); }},
        _originateCall() { originates++; },
        props: {name: 'phone', record: {
            data: {phone: '+15550000000'}, save: async () => { saves++; },
            resModel: 'res.partner', resId: 1,
        }},
        action: {doAction() { actions++; }},
    });
    await field._onClickWhatsappCallButton({preventDefault() {}});
    await field._onClickWhatsappMessageButton({preventDefault() {}});
    assert.equal(originates, 0); assert.equal(saves, 0); assert.equal(actions, 0);
    assert.equal(warnings.length, 2);
    response = {call: true, message: true};
    await field._onClickWhatsappCallButton({preventDefault() {}});
    await field._onClickWhatsappMessageButton({preventDefault() {}});
    assert.equal(originates, 1); assert.equal(saves, 1); assert.equal(actions, 1);
    console.log('PASS actual WhatsApp service, template predicates and handlers: absent/pending/failure, separate capabilities, refresh/coalescing, stale clicks and allowed actions');
})().catch((error) => { console.error(error); process.exitCode = 1; });
