#!/usr/bin/env node
const path = require('node:path');
const launcher = require('../lib/launcher.js');
launcher.launch(process.argv.slice(2), path.resolve(__dirname, '..')).then(code => {
  process.exitCode = code;
}).catch(error => {
  process.stderr.write(`cuanta: ${error.message}\n`);
  process.exitCode = error.exitCode || 1;
});
