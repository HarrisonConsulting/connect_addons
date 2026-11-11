/** Rebuild shipped Twilio 2.15.0 bundles after the documented Connect patch. */
const fs = require('node:fs');
const path = require('node:path');
const esbuild = require(process.argv[2] || 'esbuild');
if (esbuild.version !== '0.21.5') throw new Error(`Expected esbuild 0.21.5, got ${esbuild.version}`);
const root = path.resolve(__dirname, '..');
const source = fs.readFileSync(path.join(root, 'connect/static/src/lib/twilio.js'), 'utf8');
const result = esbuild.transformSync(source, {
    loader: 'js', target: 'es2017', legalComments: 'inline',
    minifyWhitespace: true, minifySyntax: true, minifyIdentifiers: false,
    charset: 'utf8',
}).code.replace(/[ \t]+$/gm, '');
for (const name of ['connect/static/src/lib/twilio.min.js',
                    'connect_website/static/src/snippets/s_connect_button/twilio.min.js']) {
    const file = path.join(root, name);
    if (process.argv.includes('--check')) {
        if (fs.readFileSync(file, 'utf8') !== result) throw new Error(`Stale vendor bundle: ${name}`);
        console.log(`PASS reproducible bundle: ${name}`);
    } else {
        fs.writeFileSync(file, result);
        console.log(`Built ${name}`);
    }
}
