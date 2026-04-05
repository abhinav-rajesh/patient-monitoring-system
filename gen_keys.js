const fs = require('fs');
const crypto = require('crypto');

function generateVapidKeys() {
    // A quick hack using native crypto to generate VAPID keys requires ecdh
    // But since the user has npm available, let's just shell out to npx web-push.
    const cp = require('child_process');
    try {
        const output = cp.execSync('npx web-push generate-vapid-keys --json').toString();
        const { publicKey, privateKey } = JSON.parse(output);
        fs.writeFileSync('vapid_keys.json', JSON.stringify({
            public_key: publicKey,
            private_key: privateKey
        }, null, 2), 'utf8');
        console.log("Keys generated successfully in utf8.");
    } catch (e) {
        console.error("Error generating keys", e);
    }
}
generateVapidKeys();
