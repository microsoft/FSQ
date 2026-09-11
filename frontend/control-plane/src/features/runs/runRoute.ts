import type { PlatformId } from '../../api/types';

export interface RunRoute { workspace?: string; platform?: PlatformId; run?: string; step?: string; error?: string }
const platforms = new Set(['web', 'android', 'windows', 'macos']);

export function parseRunRoute(hash: string): RunRoute | null {
  if (hash !== '#runs' && !hash.startsWith('#runs?')) return null;
  const query = new URLSearchParams(hash.slice(6));
  const route: RunRoute = {};
  for (const key of ['workspace', 'platform', 'run', 'step'] as const) {
    const values = query.getAll(key);
    if (values.length > 1 || values.some(value => !value.trim())) return { error: 'Invalid Run address. Use a complete Workspace, platform, and Run identity.' };
    if (values[0]) Object.assign(route, { [key]: values[0] });
  }
  if ([...query.keys()].some(key => !['workspace', 'platform', 'run', 'step'].includes(key)) || route.platform && !platforms.has(route.platform) || route.run && (!route.workspace || !route.platform) || route.step && !route.run || route.platform && !route.workspace) return { error: 'Invalid Run address. Use a complete Workspace, platform, and Run identity.' };
  return route;
}

export function runHash(route: RunRoute): string {
  const query = new URLSearchParams();
  for (const key of ['workspace', 'platform', 'run', 'step'] as const) if (route[key]) query.set(key, route[key]);
  return `#runs${query.size ? `?${query}` : ''}`;
}
