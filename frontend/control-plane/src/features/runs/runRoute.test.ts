import { describe, expect, it } from 'vitest';
import { parseRunRoute, runHash } from './runRoute';

describe('durable Runs identity', () => {
  it('round trips explicit workspace and execution identity', () => {
    const route = { workspace: 'demo workspace', platform: 'web' as const, run: 'run-1', step: 'call/2' };
    expect(parseRunRoute(runHash(route))).toEqual(route);
  });
  it('distinguishes ordinary entry and incomplete detail identity', () => {
    expect(parseRunRoute('')).toBeNull();
    expect(parseRunRoute('#runs')).toEqual({});
    expect(parseRunRoute('#runs?run=run-1')).toHaveProperty('error');
    expect(parseRunRoute('#runs?workspace=a&platform=other')).toHaveProperty('error');
  });
});
