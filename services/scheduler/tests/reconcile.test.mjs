import assert from 'node:assert/strict';
import { afterEach, test } from 'node:test';

import { POST } from '../api/reconcile.ts';

const originalFetch = globalThis.fetch;
const originalBinding = process.env.API_INTERNAL_URL;
const originalSecret = process.env.CRON_SECRET;

const scheduleHeaders = {
  'ce-specversion': '1.0',
  'ce-type': 'com.vercel.schedule.v1beta',
  'ce-source': '/schedule/default/reconcile-runs',
  'ce-id': 'execution-1',
  'ce-vssscheduledat': '2026-11-11T12:00:00.000Z',
  'ce-vssscheduleid': 'schedule-1',
  'ce-vssschedulename': 'reconcile-runs',
  'ce-vssnamespace': 'default',
  'ce-vssschedulesource': 'static',
  'ce-vssexpression': '*/15 * * * *',
};

function dispatch(headers = scheduleHeaders) {
  return new Request('https://scheduler.internal/api/reconcile', { method: 'POST', headers });
}

afterEach(() => {
  globalThis.fetch = originalFetch;
  if (originalBinding === undefined) delete process.env.API_INTERNAL_URL;
  else process.env.API_INTERNAL_URL = originalBinding;
  if (originalSecret === undefined) delete process.env.CRON_SECRET;
  else process.env.CRON_SECRET = originalSecret;
});

test('rejects requests without a schedule dispatch', async () => {
  globalThis.fetch = () => { throw new Error('Unexpected API call'); };
  const response = await POST(dispatch({}));
  assert.equal(response.status, 400);
});

test('fails closed when its API binding or secret is missing', async () => {
  delete process.env.API_INTERNAL_URL;
  delete process.env.CRON_SECRET;
  globalThis.fetch = () => { throw new Error('Unexpected API call'); };

  await assert.rejects(() => POST(dispatch()), /requires the API binding and CRON_SECRET/);
  process.env.API_INTERNAL_URL = 'https://private-api.internal/';
  await assert.rejects(() => POST(dispatch()), /requires the API binding and CRON_SECRET/);
});

test('calls the private reconciliation route with a server-held bearer secret', async () => {
  process.env.API_INTERNAL_URL = 'https://private-api.internal/binding';
  process.env.CRON_SECRET = 'test-only-secret';
  globalThis.fetch = async (url, options) => {
    assert.equal(url.href, 'https://private-api.internal/binding/api/v1/internal/reconcile-runs');
    assert.equal(options.method, 'POST');
    assert.equal(options.headers.Authorization, 'Bearer test-only-secret');
    assert.equal(options.cache, 'no-store');
    return new Response('{"republished":1}', { status: 200 });
  };

  const response = await POST(dispatch());
  assert.equal(response.status, 200);
});

test('surfaces a failed backend reconciliation run', async () => {
  process.env.API_INTERNAL_URL = 'https://private-api.internal/';
  process.env.CRON_SECRET = 'test-only-secret';
  globalThis.fetch = async () => new Response(null, { status: 503 });

  await assert.rejects(() => POST(dispatch()), /HTTP 503/);
});
