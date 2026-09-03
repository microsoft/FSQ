import { copyFile, mkdir, readFile, rm, writeFile } from 'node:fs/promises';
import { dirname, resolve } from 'node:path';

const root = resolve(import.meta.dirname, '..');
const staging = resolve(root, '.frontend-dist');
const entries = [{ name: 'control-plane', target: resolve(root, 'fsq_agent/adapters/control_plane/static') }];

const manifest = JSON.parse(await readFile(resolve(staging, '.vite/manifest.json'), 'utf8'));

function collectEntryFiles(name) {
  const entryKey = Object.keys(manifest).find((key) => manifest[key].isEntry && (manifest[key].src === `${name}/index.html` || key === `${name}/index.html`));
  if (!entryKey) throw new Error(`Vite manifest has no entry for ${name}/index.html`);
  const files = new Set();
  const visited = new Set();
  const visit = (key) => {
    if (visited.has(key)) return;
    visited.add(key);
    const chunk = manifest[key];
    if (!chunk) throw new Error(`Vite manifest import ${key} is missing`);
    for (const file of [chunk.file, ...(chunk.css ?? []), ...(chunk.assets ?? [])]) {
      if (file) files.add(file);
    }
    for (const imported of [...(chunk.imports ?? []), ...(chunk.dynamicImports ?? [])]) visit(imported);
  };
  visit(entryKey);
  return files;
}

for (const { name, target } of entries) {
  const files = collectEntryFiles(name);
  await rm(target, { recursive: true, force: true });
  await mkdir(resolve(target, name), { recursive: true });
  await copyFile(resolve(staging, name, 'index.html'), resolve(target, name, 'index.html'));
  for (const file of files) {
    const destination = resolve(target, file);
    await mkdir(dirname(destination), { recursive: true });
    await copyFile(resolve(staging, file), destination);
  }
  await writeFile(resolve(target, 'entry-assets.json'), `${JSON.stringify({ entry: name, files: [...files].sort() }, null, 2)}\n`);
}
