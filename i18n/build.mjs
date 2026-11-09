import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const directory = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(directory, '..');
export const locales = ['en', 'zh_CN', 'zh_TW', 'ja', 'ko'];
const messages = {};
for (const name of fs.readdirSync(directory).filter(name => name.endsWith('.json')).sort()) {
  const fragment = JSON.parse(fs.readFileSync(path.join(directory, name), 'utf8').replace(/^\uFEFF/, ''));
  for (const [key, values] of Object.entries(fragment)) {
    if (messages[key]) throw new Error(`Duplicate message: ${key}`);
    if (!/^[a-zA-Z0-9_]+$/.test(key)) throw new Error(`Invalid key: ${key}`);
    let placeholders;
    for (const locale of locales) {
      if (typeof values[locale] !== 'string' || !values[locale].trim()) throw new Error(`Missing ${locale}: ${key}`);
      const current = [...new Set(values[locale].match(/\$[1-9]/g) || [])].sort().join(',');
      if (placeholders !== undefined && current !== placeholders) throw new Error(`Placeholder mismatch: ${locale}/${key}`);
      placeholders = current;
    }
    messages[key] = values;
  }
}
for (const locale of locales) {
  const catalog = Object.fromEntries(Object.entries(messages).sort(([a], [b]) => a.localeCompare(b)).map(([key, values]) => [key, { message: values[locale] }]));
  const destination = path.join(root, 'extension', '_locales', locale);
  fs.mkdirSync(destination, { recursive: true });
  fs.writeFileSync(path.join(destination, 'messages.json'), JSON.stringify(catalog, null, 2) + '\n');
}
console.log(`Generated ${Object.keys(messages).length} messages in ${locales.length} languages.`);
