/** Exercise the shipped SDK, with only its private Browserify loader exposed in a test VM. */
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const assert = require('node:assert/strict');
const tick = () => new Promise((resolve) => setImmediate(resolve));
const root = path.resolve(__dirname, '..');
function load(name) {
    const source = process.argv.includes('--baseline')
        ? require('node:child_process').execFileSync('git', ['show', `HEAD:${name}`], {cwd: root, encoding: 'utf8'})
        : fs.readFileSync(path.join(root, name), 'utf8');
    const anchor = /Voice\s*=\s*bundle\(1\);/;
    assert.ok(anchor.test(source), 'SDK loader instrumentation anchor');
    const logs = [];
    const quiet = {debug() {}, info() {}, warn() {}, error(...args) { logs.push(args); }};
    const context = vm.createContext({console: quiet, setTimeout, clearTimeout, setInterval, clearInterval,
        navigator: {userAgent: 'Connect source regression', mediaDevices: {}},
    });
    vm.runInContext(source.replace(anchor, '$&globalThis.sdkRequire=bundle;'), context, {filename: name});
    return {require: context.sdkRequire, logs};
}
async function check(name) {
    const sdk = load(name);
    const AudioHelper = sdk.require(3).default;
    const OutputCollection = sdk.require(19).default;
    const Device = sdk.require(12).default;
    const Call = sdk.require(9).default;
    const PeerConnection = sdk.require(29).default;
    let checks = 0;
    // Actual AudioHelper enumeration -> lost-output cleanup -> actual output
    // collection. An unsupported browser must never reach active-call sink selection.
    for (const supported of [false, true]) {
        const changes = [];
        const peer = Object.create(PeerConnection.prototype);
        peer._isSinkSupported = supported;
        peer.version = null;
        const call = Object.create(Call.prototype);
        call._mediaHandler = peer;
        const device = Object.create(Device.prototype);
        device._activeCall = call;
        device._soundcache = new Map();
        const helper = new AudioHelper((kind, ids) => {
            changes.push([kind, [...ids]]);
            return kind === 'speaker'
                ? device._updateSpeakerSinkIds(ids) : Promise.resolve();
        },
            () => Promise.resolve(), {AudioContext: false,
                setSinkId: supported ? function () {} : undefined,
                mediaDevices: {}, enumerateDevices: async () => [],
            });
        await tick();
        changes.length = 0;
        const lost = {deviceId: 'usb-speaker', kind: 'audiooutput', label: 'USB speaker'};
        const fallback = {deviceId: 'default', kind: 'audiooutput', label: 'Default'};
        helper.availableOutputDevices.set(lost.deviceId, lost);
        helper.availableOutputDevices.set(fallback.deviceId, fallback);
        helper.speakerDevices.get().add(lost);
        helper.ringtoneDevices.get().add(lost);
        const events = [];
        helper.on('deviceChange', (devices) => events.push([...devices]));
        helper._enumerateDevices = async () => [fallback];
        await helper._updateAvailableDevices();
        await tick();
        assert.equal(helper.isOutputSelectionSupported, supported);
        assert.equal(helper.availableOutputDevices.has(lost.deviceId), false);
        assert.deepEqual([...helper.speakerDevices.get()].map((d) => d.deviceId), ['default']);
        assert.equal(events.length, 1);
        assert.equal(events[0][0], lost);
        assert.equal(changes.length, supported ? 2 : 0);
        if (supported) {
            assert.deepEqual(changes, [['speaker', ['default']], ['ringtone', ['default']]]);
            assert.deepEqual([...peer.sinkIds], ['default']);
        }
        helper._destroy();
        checks++;
    }
    const lost = {deviceId: 'lost'};
    const fallback = {deviceId: 'default'};
    const available = new Map([['default', fallback]]);
    // Cleanup remains synchronous for its boolean/bookkeeping callers, while
    // both asynchronous and synchronous genuine failures retain their object.
    for (const synchronous of [false, true]) {
        const error = new Error(synchronous ? 'selection threw' : 'selection denied');
        const logged = [];
        const collection = new OutputCollection('speaker', available, () => {
            if (synchronous) throw error;
            return Promise.reject(error);
        }, true);
        collection._log = {debug() {}, error(...args) { logged.push(args); }};
        collection.get().add(lost);
        assert.equal(collection.delete(lost), true);
        await tick();
        assert.equal(logged.length, 1);
        assert.equal(logged[0][1], error);
        assert.match(logged[0][0], /device removal/);
        checks++;
    }
    // Explicit supported selection still propagates a real refusal to its caller.
    const denied = new Error('Permission denied');
    const collection = new OutputCollection('speaker', available, () => Promise.reject(denied), true);
    await assert.rejects(collection.set('default'), (error) => error === denied);
    assert.equal(collection.get().size, 0);
    checks++;
    let unsupportedChanges = 0;
    const unsupported = new OutputCollection('speaker', available, () => { unsupportedChanges++; }, false);
    await assert.rejects(unsupported.set('default'), /does not support audio output selection/);
    assert.equal(unsupportedChanges, 0);
    checks++;
    console.log(`PASS ${name}: ${checks} SDK cleanup/selection cases`);
}
(async () => {
    const unhandled = [];
    const onUnhandled = (error) => unhandled.push(error);
    process.on('unhandledRejection', onUnhandled);
    try {
        for (const name of ['connect/static/src/lib/twilio.js', 'connect/static/src/lib/twilio.min.js',
                           'connect_website/static/src/snippets/s_connect_button/twilio.min.js']) await check(name);
        await tick();
        assert.deepEqual(unhandled, [], 'SDK output cleanup must own every rejected promise');
        console.log('PASS no unhandled cleanup rejections across all bundles');
    } finally { process.off('unhandledRejection', onUnhandled); }
})().catch((error) => { console.error(error); process.exitCode = 1; });
