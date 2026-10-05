const assert = require('node:assert/strict');
const test = require('node:test');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const crypto = require('node:crypto');
const cp = require('node:child_process');
const launcher = require('../lib/launcher.js');
const source = path.resolve(__dirname, '..');
const version = require('../package.json').version;
const win = process.platform === 'win32';
const suffix = win ? '.exe' : '';
const stub = `"cuanta-test-shim";
const fs = require('node:fs');
const path = require('node:path');
const name = path.basename(process.argv[1]).replace(/\\.exe$/, '');
const args = process.argv.slice(2);
fs.appendFileSync(process.env.SHIM_LOG, JSON.stringify({name, args}) + '\\n');
const content = fs.readFileSync(__filename, 'utf8');
function runtime(folder) {
  const bin = path.join(folder, process.platform === 'win32' ? 'Scripts' : 'bin');
  fs.mkdirSync(bin, {recursive: true});
  for (const file of ['python', 'cuanta']) fs.writeFileSync(path.join(bin, file + (process.platform === 'win32' ? '.exe' : '')), content, {mode: 0o755});
}
if (name === 'cuanta') {
  if (args[0] === 'wait') {
    process.stdout.write('ready\\n');
    for (const signal of ['SIGINT', 'SIGTERM', 'SIGHUP']) process.on(signal, () => process.exit(37));
    setInterval(() => {}, 1000);
  } else { process.stdout.write(JSON.stringify(args)); process.exit(Number(process.env.SHIM_EXIT || 0)); }
} else if (args.includes('-c')) { process.stdout.write('3.13'); }
else if (args[0] === 'venv' || args.includes('venv')) {
  if (process.env.SHIM_FAIL === '1') process.exit(19);
  runtime(args.at(-1));
} else if (args.includes('install')) {
  if (process.env.SHIM_FAIL === '1') process.exit(19);
  process.stderr.write('installing\\n');
}
`;
const bridge = `const cp = require('node:child_process');
const fs = require('node:fs');
function fake(file) { try { return fs.readFileSync(file, 'utf8').startsWith('"cuanta-test-shim";'); } catch { return false; } }
for (const name of ['spawn', 'spawnSync']) {
  const original = cp[name];
  cp[name] = function(file, args, options) { return fake(file) ? original(process.execPath, [file, ...args], options) : original(file, args, options); };
}
`;
function fixture(tools = ['uv']) {
  const folder = fs.mkdtempSync(path.join(os.tmpdir(), 'cuanta-node-test-'));
  const pkg = path.join(folder, 'package');
  fs.mkdirSync(pkg);
  for (const name of ['bin', 'lib']) fs.cpSync(path.join(source, name), path.join(pkg, name), {recursive: true});
  fs.copyFileSync(path.join(source, 'package.json'), path.join(pkg, 'package.json'));
  const payload = path.join(pkg, 'payload');
  fs.mkdirSync(payload);
  fs.writeFileSync(path.join(payload, 'cuanta.whl'), 'wheel');
  fs.writeFileSync(path.join(payload, 'requirements.txt'), 'example==1 --hash=sha256:abc');
  fs.writeFileSync(path.join(payload, 'manifest.json'), JSON.stringify({version, wheel: 'cuanta.whl', sha256: crypto.createHash('sha256').update('wheel').digest('hex')}));
  const bin = path.join(folder, 'tools');
  fs.mkdirSync(bin);
  for (const name of tools) fs.writeFileSync(path.join(bin, name + suffix), stub, {mode: 0o755});
  const preload = path.join(folder, 'bridge.cjs');
  fs.writeFileSync(preload, bridge);
  const log = path.join(folder, 'calls.jsonl');
  fs.writeFileSync(log, '');
  const env = {...process.env, PATH: bin, CUANTA_RUNTIME_DIR: path.join(folder, 'cache'), NODE_OPTIONS: `--require="${preload.split(path.sep).join('/')}"`, SHIM_LOG: log};
  const command = path.join(pkg, 'bin', 'cuanta.js');
  function run(args = ['sample', '--json'], extra = {}) { return cp.spawnSync(process.execPath, [command, ...args], {env: {...env, ...extra}, encoding: 'utf8', timeout: 20000}); }
  function calls() { return fs.readFileSync(log, 'utf8').trim().split('\n').filter(Boolean).map(line => JSON.parse(line)); }
  return {folder, pkg, env, command, run, calls, clean: () => fs.rmSync(folder, {recursive: true, force: true})};
}
function seed(f) {
  const runtime = path.join(f.env.CUANTA_RUNTIME_DIR, version);
  const bin = path.join(runtime, win ? 'Scripts' : 'bin');
  fs.mkdirSync(bin, {recursive: true});
  fs.writeFileSync(path.join(bin, 'cuanta' + suffix), stub, {mode: 0o755});
  fs.writeFileSync(path.join(runtime, '.ready'), version);
  return runtime;
}
test('cache locations and override are platform specific', () => {
  assert.equal(launcher.cacheRoot('win32', {LOCALAPPDATA: 'local'}, 'home'), path.join('local', 'cuanta', 'npm'));
  assert.equal(launcher.cacheRoot('darwin', {}, 'home'), path.join('home', 'Library', 'Caches', 'cuanta', 'npm'));
  assert.equal(launcher.cacheRoot('linux', {XDG_CACHE_HOME: 'xdg'}, 'home'), path.join('xdg', 'cuanta', 'npm'));
  assert.equal(launcher.cacheRoot('linux', {CUANTA_RUNTIME_DIR: 'override'}, 'home'), path.resolve('override'));
});
test('ready cache bypasses all builders and passes arguments and exit code', () => {
  const f = fixture();
  try { seed(f); const result = f.run(['one', '--json', 'space value'], {SHIM_EXIT:'23'}); assert.equal(result.status, 23, result.stderr); assert.deepEqual(JSON.parse(result.stdout), ['one', '--json', 'space value']); assert.equal(f.calls().filter(x => x.name === 'uv').length, 0); } finally { f.clean(); }
});
test('uv wins over python and only build progress reaches stderr', () => {
  const f = fixture(['uv', 'python3']);
  try { const result = f.run(); assert.equal(result.status, 0, result.stderr); assert.deepEqual(JSON.parse(result.stdout), ['sample', '--json']); assert.match(result.stderr, /runtime|entorno/); assert.equal(f.calls().filter(x => x.name === 'uv').length, 3); assert.equal(f.calls().filter(x => x.name === 'python3').length, 0); assert.equal(f.run().stderr, ''); } finally { f.clean(); }
});
test('python fallback checks supported version and installs with hashes', () => {
  const f = fixture(['python3']);
  try { const result = f.run(); assert.equal(result.status, 0, result.stderr); assert.ok(f.calls().some(x => x.args.includes('-c'))); assert.ok(f.calls().some(x => x.args.includes('--require-hashes'))); assert.ok(f.calls().some(x => x.args.includes('--no-deps'))); } finally { f.clean(); }
});
test('first-run Spanish progress is valid UTF-8', () => {
  const f = fixture();
  try { const result=f.run([], {LC_ALL:'es_PE.UTF-8'}); assert.equal(result.status,0,result.stderr); assert.match(result.stderr, /primera ejecuci\u00f3n/); } finally { f.clean(); }
});
test('missing marker or entry rebuilds the cache exactly once', () => {
  for (const missing of ['.ready', win ? 'Scripts/cuanta.exe' : 'bin/cuanta']) {
    const f = fixture();
    try { const runtime = seed(f); fs.unlinkSync(path.join(runtime, missing)); assert.equal(f.run().status, 0); assert.equal(f.run().status, 0); assert.equal(f.calls().filter(x => x.name === 'uv' && x.args[0] === 'venv').length, 1); } finally { f.clean(); }
  }
});
test('missing tools explains the OS fix in English and Spanish', () => {
  const f = fixture([]);
  try { for (const locale of ['en_US.UTF-8', 'es_PE.UTF-8']) { const result=f.run([], {LC_ALL:locale, LANG:locale}); assert.equal(result.status, 1); assert.equal(result.stdout, ''); assert.match(result.stderr, /winget install astral-sh.uv|brew install uv|curl -LsSf/); assert.match(result.stderr, locale.startsWith('es') ? /Instala/ : /Install/); } } finally { f.clean(); }
});
test('failed build leaves no ready runtime and can retry', () => {
  const f = fixture();
  try { assert.equal(f.run([], {SHIM_FAIL:'1'}).status, 19); assert.ok(!fs.existsSync(path.join(f.env.CUANTA_RUNTIME_DIR, version, '.ready'))); assert.equal(f.run().status, 0); } finally { f.clean(); }
});
test('wheel integrity is checked before any builder runs', () => {
  const f = fixture();
  try { fs.writeFileSync(path.join(f.pkg, 'payload', 'cuanta.whl'), 'bad'); const result=f.run(); assert.equal(result.status,1); assert.equal(f.calls().length,0); } finally { f.clean(); }
});
test('keeps two owned versions and leaves unrelated directories alone', () => {
  const f = fixture();
  try { for (const old of ['0.1.0','0.2.0']) { const folder=path.join(f.env.CUANTA_RUNTIME_DIR,old); fs.mkdirSync(folder,{recursive:true}); fs.writeFileSync(path.join(folder,'.ready'),old); } fs.mkdirSync(path.join(f.env.CUANTA_RUNTIME_DIR,'unrelated')); assert.equal(f.run().status,0); const versions=fs.readdirSync(f.env.CUANTA_RUNTIME_DIR).filter(x => /^\d+\.\d+\.\d+$/.test(x)); assert.equal(versions.length,2); assert.ok(fs.existsSync(path.join(f.env.CUANTA_RUNTIME_DIR,'unrelated'))); } finally { f.clean(); }
});
test('concurrent first runs build one runtime', async () => {
  const f=fixture();
  function start() { return new Promise((resolve,reject) => { const child=cp.spawn(process.execPath,[f.command,'sample'],{env:f.env,stdio:['ignore','pipe','pipe']}); let stderr=''; child.stderr.on('data',x=>stderr+=x); child.on('error',reject); child.on('exit',code=>resolve({code,stderr})); }); }
  try { const results=await Promise.all([start(),start()]); for (const result of results) assert.equal(result.code,0,result.stderr); assert.equal(f.calls().filter(x=>x.name==='uv'&&x.args[0]==='venv').length,1); } finally { f.clean(); }
});
for (const signal of ['SIGINT','SIGTERM','SIGHUP']) test(`forwards ${signal} to the child`, {skip:win ? 'POSIX signal delivery is tested on POSIX by design' : false}, async () => {
  const f=fixture();
  try { seed(f); const child=cp.spawn(process.execPath,[f.command,'wait'],{env:f.env,stdio:['ignore','pipe','pipe']}); await new Promise((resolve,reject)=>{child.stdout.once('data',resolve); child.once('error',reject);}); const result=new Promise(resolve=>child.once('exit',resolve)); child.kill(signal); assert.equal(await result,37); } finally { f.clean(); }
});
test('version and whitelist metadata follow pyproject without lifecycle scripts', () => {
  const pkg=require('../package.json');
  const pyproject=fs.readFileSync(path.resolve(source,'../../pyproject.toml'),'utf8');
  assert.equal(pkg.version, /version = "([^"]+)"/.exec(pyproject)[1]);
  assert.equal(pkg.name,'cuanta'); assert.deepEqual(pkg.bin,{cuanta:'bin/cuanta.js'});
  assert.deepEqual(pkg.files,['bin','lib','payload','README.md','LICENSE']);
  assert.equal(pkg.engines.node,'>=18'); assert.ok(!pkg.dependencies); assert.ok(!pkg.scripts);
});
