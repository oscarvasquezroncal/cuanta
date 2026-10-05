const fs = require('node:fs');
const path = require('node:path');
const os = require('node:os');
const crypto = require('node:crypto');
const cp = require('node:child_process');

const numbers = {SIGINT: 2, SIGTERM: 15, SIGHUP: 1};
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
function cacheRoot(platform = process.platform, env = process.env, home = os.homedir()) {
  if (env.CUANTA_RUNTIME_DIR) return path.resolve(env.CUANTA_RUNTIME_DIR);
  if (platform === 'win32') return path.join(env.LOCALAPPDATA || path.join(home, 'AppData', 'Local'), 'cuanta', 'npm');
  if (platform === 'darwin') return path.join(home, 'Library', 'Caches', 'cuanta', 'npm');
  return path.join(env.XDG_CACHE_HOME || path.join(home, '.cache'), 'cuanta', 'npm');
}
function environment() {
  return {...process.env, PYTHONUTF8: '1', PYTHONIOENCODING: process.env.PYTHONIOENCODING || 'utf-8'};
}
function executable(name, env) {
  const endings = process.platform === 'win32' ? ['.exe', '.com', ''] : [''];
  for (const folder of (env.PATH || env.Path || '').split(path.delimiter).filter(Boolean)) {
    for (const ending of endings) {
      const candidate = path.join(folder, name + ending);
      try { if (fs.statSync(candidate).isFile()) { fs.accessSync(candidate, fs.constants.X_OK); return candidate; } } catch {}
    }
  }
  return null;
}
function paths(folder) {
  const bin = path.join(folder, process.platform === 'win32' ? 'Scripts' : 'bin');
  return {python: path.join(bin, process.platform === 'win32' ? 'python.exe' : 'python'), entry: path.join(bin, process.platform === 'win32' ? 'cuanta.exe' : 'cuanta')};
}
function ready(folder) {
  try { return fs.statSync(path.join(folder, '.ready')).isFile() && fs.statSync(paths(folder).entry).isFile(); } catch { return false; }
}
function language(env) { return /(^|[_.-])es([_.-]|$)/i.test(env.LC_ALL || env.LC_MESSAGES || env.LANG || Intl.DateTimeFormat().resolvedOptions().locale) ? 'es' : 'en'; }
function debug(env, text) { if (env.CUANTA_LAUNCHER_DEBUG === '1') process.stderr.write(`cuanta: ${text}\n`); }
function failure(message, exitCode = 1) { const error = new Error(message); error.exitCode = exitCode; return error; }
function builder(env) {
  const uv = executable('uv', env);
  if (uv) return {kind:'uv', file:uv, args:[]};
  const candidates = [['python3', []], ['python', []]];
  if (process.platform === 'win32') candidates.push(['py', ['-3.13']], ['py', ['-3.12']]);
  for (const [name, args] of candidates) {
    const file = executable(name, env);
    if (!file) continue;
    const probe = cp.spawnSync(file, [...args, '-c', 'import sys; print(str(sys.version_info.major) + "." + str(sys.version_info.minor))'], {env, encoding:'utf8', timeout:10000});
    if (probe.status === 0 && /^3\.(12|13)$/.test(probe.stdout.trim())) return {kind:'python', file, args};
  }
  const fix = process.platform === 'win32' ? 'winget install astral-sh.uv' : process.platform === 'darwin' ? 'brew install uv' : 'curl -LsSf https://astral.sh/uv/install.sh | sh';
  throw failure(language(env) === 'es' ? `Instala uv (${fix}) o Python 3.12/3.13 y vuelve a ejecutar cuanta.` : `Install uv (${fix}) or Python 3.12/3.13, then run cuanta again.`);
}
function execute(file, args, env, preparing = false) {
  return new Promise((resolve, reject) => {
    const child = cp.spawn(file, args, {env, stdio: preparing ? ['inherit', 2, 2] : 'inherit'});
    const handlers = Object.fromEntries(Object.keys(numbers).map(signal => [signal, () => child.kill(signal)]));
    for (const [signal, handler] of Object.entries(handlers)) process.on(signal, handler);
    function cleanup() { for (const [signal, handler] of Object.entries(handlers)) process.removeListener(signal, handler); }
    child.once('error', error => { cleanup(); reject(error); });
    child.once('exit', (code, signal) => { cleanup(); resolve(code === null ? 128 + (numbers[signal] || 0) : code); });
  });
}
async function checked(file, args, env) {
  const code = await execute(file, args, env, true);
  if (code !== 0) throw failure(`runtime command exited ${code}`, code);
}
function payload(root, version) {
  const folder = path.join(root, 'payload');
  const manifest = JSON.parse(fs.readFileSync(path.join(folder, 'manifest.json'), 'utf8'));
  if (manifest.version !== version || path.basename(manifest.wheel) !== manifest.wheel || !manifest.wheel.endsWith('.whl')) throw failure('invalid runtime manifest');
  const wheel = path.join(folder, manifest.wheel);
  if (crypto.createHash('sha256').update(fs.readFileSync(wheel)).digest('hex') !== manifest.sha256) throw failure('wheel integrity check failed');
  return {wheel, requirements:path.join(folder, 'requirements.txt')};
}
function alive(pid) { if (!Number.isInteger(pid) || pid <= 0) return false; try { process.kill(pid, 0); return true; } catch(error) { return error.code === 'EPERM'; } }
async function acquire(lock) {
  for (;;) {
    try { const handle = fs.openSync(lock, 'wx', 0o600); fs.writeFileSync(handle, JSON.stringify({pid:process.pid})); fs.closeSync(handle); return; }
    catch(error) {
      if (error.code !== 'EEXIST') throw error;
      try { const value=JSON.parse(fs.readFileSync(lock,'utf8')); if (!alive(value.pid)) { fs.unlinkSync(lock); continue; } }
      catch { try { if (Date.now() - fs.statSync(lock).mtimeMs > 5000) fs.unlinkSync(lock); } catch {} }
      await sleep(100);
    }
  }
}
function relocate(entry, temporary, target) {
  const original = fs.readFileSync(entry);
  let data = original;
  for (const [oldPrefix, newPrefix] of [[temporary, target], [temporary.split(path.sep).join('/'), target.split(path.sep).join('/')]]) {
    const before = Buffer.from(oldPrefix);
    const after = Buffer.from(newPrefix);
    let position;
    while ((position = data.indexOf(before)) !== -1) data = Buffer.concat([data.subarray(0,position), after, data.subarray(position + before.length)]);
  }
  if (!data.equals(original)) fs.writeFileSync(entry, data);
}
function prune(root, current) {
  const versions = fs.readdirSync(root, {withFileTypes:true}).filter(item => item.isDirectory() && /^\d+\.\d+\.\d+(?:[-+][\w.-]+)?$/.test(item.name) && fs.existsSync(path.join(root,item.name,'.ready')));
  versions.sort((a,b) => fs.statSync(path.join(root,b.name,'.ready')).mtimeMs - fs.statSync(path.join(root,a.name,'.ready')).mtimeMs);
  const keep = new Set([current, ...versions.filter(item=>item.name!==current).slice(0,1).map(item=>item.name)]);
  for (const item of versions) if (!keep.has(item.name)) fs.rmSync(path.join(root,item.name), {recursive:true,force:true});
}
async function install(root, target, version, packageRoot, env) {
  fs.mkdirSync(root, {recursive:true});
  const lock = path.join(root, `.${version}.lock`);
  await acquire(lock);
  let temporary;
  try {
    if (ready(target)) return;
    const files = payload(packageRoot, version);
    const tool = builder(env);
    debug(env, `build via ${tool.kind}`);
    process.stderr.write(language(env) === 'es' ? 'cuanta: Preparando el entorno de cuanta; la primera ejecuci\u00f3n puede tardar.\n' : 'cuanta: Preparing cuanta runtime; the first run can take a moment.\n');
    temporary = fs.mkdtempSync(path.join(root, `.${version}-`));
    if (tool.kind === 'uv') {
      await checked(tool.file, ['venv','--python','3.13','--relocatable',temporary], env);
      await checked(tool.file, ['pip','install','--python',paths(temporary).python,'--require-hashes','-r',files.requirements], env);
      await checked(tool.file, ['pip','install','--python',paths(temporary).python,'--no-deps',files.wheel], env);
    } else {
      await checked(tool.file, [...tool.args,'-m','venv',temporary], env);
      await checked(paths(temporary).python, ['-m','pip','install','--require-hashes','-r',files.requirements], env);
      await checked(paths(temporary).python, ['-m','pip','install','--no-deps',files.wheel], env);
    }
    if (!fs.existsSync(paths(temporary).entry)) throw failure('runtime entry point missing after installation');
    relocate(paths(temporary).entry, temporary, target);
    fs.writeFileSync(path.join(temporary,'.ready'), version);
    fs.rmSync(target, {recursive:true,force:true});
    fs.renameSync(temporary,target);
    temporary = null;
    prune(root,version);
  } finally {
    if (temporary) fs.rmSync(temporary,{recursive:true,force:true});
    fs.unlinkSync(lock);
  }
}
async function launch(args, packageRoot) {
  const metadata = JSON.parse(fs.readFileSync(path.join(packageRoot,'package.json'),'utf8'));
  const version = metadata.version;
  if (!/^\d+\.\d+\.\d+(?:[-+][\w.-]+)?$/.test(version)) throw failure('invalid package version');
  const env = environment();
  const root = cacheRoot(process.platform,env);
  const target = path.join(root,version);
  if (!ready(target)) await install(root,target,version,packageRoot,env);
  else debug(env,'ready runtime cache');
  return execute(paths(target).entry,args,env);
}
module.exports = {cacheRoot, launch};
