const assert = require('node:assert/strict');
const test = require('node:test');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const vm = require('node:vm');
const {EventEmitter} = require('node:events');
const source = path.resolve(__dirname, '..');
const version = require('../package.json').version;
for (const [platform, tty] of [['win32', false], ['win32', true], ['linux', true], ['linux', false]]) {
  test(`console signal policy on ${platform}, TTY=${tty}`, async () => {
    const folder = fs.mkdtempSync(path.join(os.tmpdir(), 'cuanta-signals-'));
    const runtime = path.join(folder, version);
    const bin = path.join(runtime, platform === 'win32' ? 'Scripts' : 'bin');
    fs.mkdirSync(bin, {recursive: true});
    fs.writeFileSync(path.join(runtime, '.ready'), version);
    fs.writeFileSync(path.join(bin, platform === 'win32' ? 'cuanta.exe' : 'cuanta'), 'fixture');
    const child = new EventEmitter();
    const forwarded = [];
    child.kill = signal => forwarded.push(signal);
    const parent = Object.assign(new EventEmitter(), {platform, env: {CUANTA_RUNTIME_DIR: folder}, stdin: {isTTY: tty}, stderr: {write() {}}});
    const context = {module: {exports: {}}, process: parent, Buffer, setTimeout,
      require: name => name === 'node:child_process' ? {spawn: () => child} : require(name)};
    vm.runInNewContext(fs.readFileSync(path.join(source, 'lib', 'launcher.js'), 'utf8'), context);
    const running = context.module.exports.launch([], source);
    try {
      parent.emit('SIGINT');
      const shared = platform === 'win32' || tty;
      assert.deepEqual(forwarded, shared ? [] : ['SIGINT']);
      if (shared) {
        assert.equal(parent.listenerCount('SIGBREAK'), 1);
        parent.emit('SIGBREAK');
        assert.deepEqual(forwarded, []);
      }
      parent.emit('SIGTERM');
      parent.emit('SIGHUP');
      assert.deepEqual(forwarded, shared ? ['SIGTERM', 'SIGHUP'] : ['SIGINT', 'SIGTERM', 'SIGHUP']);
      let exited = false;
      running.then(() => { exited = true; });
      await Promise.resolve();
      assert.equal(exited, false);
      child.emit('exit', 37, null);
      assert.equal(await running, 37);
      for (const signal of ['SIGINT', 'SIGBREAK', 'SIGTERM', 'SIGHUP']) assert.equal(parent.listenerCount(signal), 0);
    } finally {
      child.emit('exit', 37, null);
      fs.rmSync(folder, {recursive: true, force: true});
    }
  });
}
