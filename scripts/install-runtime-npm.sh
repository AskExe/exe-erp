#!/bin/bash
# Replace the official Node distribution's entire npm tooling tree,
# including bundled dependencies, from one integrity-verified upstream tarball.
set -euo pipefail
node -e 'const [major, minor] = process.versions.node.split(".").map(Number); if (!((major === 20 && minor >= 17) || major > 22 || (major === 22 && minor >= 9))) throw new Error("npm 11 requires Node ^20.17.0 || >=22.9.0")'
archive=$(mktemp -d)
trap 'rm -rf "$archive"' EXIT
curl -fsSL https://registry.npmjs.org/npm/-/npm-11.21.1.tgz -o "$archive/npm.tgz"
node - "$archive/npm.tgz" <<'NODE'
const fs = require('node:fs');
const crypto = require('node:crypto');
const actual = crypto.createHash('sha512').update(fs.readFileSync(process.argv[2])).digest('base64');
if (actual !== 'YTzL6QBEJXyHcTu0JiUc7RKMp+mzlJtl7rGLmERaXUQeFHIMc9atdr6G9M9C8vDWbv40epoTQAzNL2/PtZVDhQ==') {
  throw new Error('npm 11.21.1 tarball integrity mismatch');
}
NODE
tar -xzf "$archive/npm.tgz" -C "$archive"
test "$(node -p "require('$archive/package/package.json').version")" = 11.21.1
rm -rf /usr/local/lib/node_modules/npm
mv "$archive/package" /usr/local/lib/node_modules/npm
ln -sf /usr/local/lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm
ln -sf /usr/local/lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx
test "$(npm --version)" = 11.21.1
test "$(npx --version)" = 11.21.1
