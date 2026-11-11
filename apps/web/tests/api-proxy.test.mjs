import assert from 'node:assert/strict';
import { afterEach, test } from 'node:test';

import { GET, HEAD, POST } from '../app/api/v1/[...path]/route.ts';

const originalFetch = globalThis.fetch;
const originalApiUrl = process.env.API_INTERNAL_URL;
const originalNodeEnv = process.env.NODE_ENV;

afterEach(() => {
  globalThis.fetch = originalFetch;
  if (originalApiUrl === undefined) delete process.env.API_INTERNAL_URL;
  else process.env.API_INTERNAL_URL = originalApiUrl;
  if (originalNodeEnv === undefined) delete process.env.NODE_ENV;
  else process.env.NODE_ENV = originalNodeEnv;
});

test('proxies a write to the bound API with its method, query, and body', async () => {
  process.env.NODE_ENV = 'production';
  process.env.API_INTERNAL_URL = 'https://private-api.internal/';
  let upstreamUrl;
  let upstreamOptions;
  globalThis.fetch = async (url, options) => {
    upstreamUrl = url;
    upstreamOptions = options;
    return new Response('{"id":"created"}', {
      status: 201,
      headers: {
        'content-type': 'application/json',
        'set-cookie': 'internal=value',
      },
    });
  };

  const response = await POST(new Request('https://linepilot.example/api/v1/jobs?facility_id=one&facility_id=two', {
    method: 'POST',
    headers: {
      'content-type': 'application/json',
      origin: 'https://linepilot.example',
      cookie: 'vercel_auth=secret',
      authorization: 'Bearer browser-value',
    },
    body: '{"job_number":"LP-1"}',
  }));

  assert.equal(upstreamUrl.href, 'https://private-api.internal/api/v1/jobs?facility_id=one&facility_id=two');
  assert.equal(upstreamOptions.method, 'POST');
  assert.equal(upstreamOptions.duplex, 'half');
  assert.equal(upstreamOptions.cache, 'no-store');
  assert.equal(await new Response(upstreamOptions.body).text(), '{"job_number":"LP-1"}');
  assert.equal(upstreamOptions.headers.get('content-type'), 'application/json');
  assert.equal(upstreamOptions.headers.has('cookie'), false);
  assert.equal(upstreamOptions.headers.has('authorization'), false);
  assert.equal(response.status, 201);
  assert.equal(response.headers.get('content-type'), 'application/json');
  assert.equal(response.headers.get('cache-control'), 'private, no-store');
  assert.equal(response.headers.has('set-cookie'), false);
  assert.deepEqual(await response.json(), { id: 'created' });
});

test('passes report downloads and partial responses without buffering them', async () => {
  process.env.NODE_ENV = 'production';
  process.env.API_INTERNAL_URL = 'https://private-api.internal/';
  const stream = new ReadableStream({
    start(controller) {
      controller.enqueue(new TextEncoder().encode('part-one'));
      controller.enqueue(new TextEncoder().encode('part-two'));
      controller.close();
    },
  });
  globalThis.fetch = async (url, options) => {
    assert.equal(url.href, 'https://private-api.internal/api/v1/reports/123/download?format=csv');
    assert.equal(options.headers.get('range'), 'bytes=0-15');
    return new Response(stream, {
      status: 206,
      headers: {
        'content-type': 'text/csv',
        'content-disposition': 'attachment; filename="report.csv"',
        'content-range': 'bytes 0-15/32',
      },
    });
  };

  const response = await GET(new Request('https://linepilot.example/api/v1/reports/123/download?format=csv', {
    headers: { range: 'bytes=0-15' },
  }));
  assert.equal(response.status, 206);
  assert.equal(response.headers.get('content-disposition'), 'attachment; filename="report.csv"');
  assert.equal(response.headers.get('content-range'), 'bytes 0-15/32');
  assert.equal(await response.text(), 'part-onepart-two');
});

test('fails closed without a production binding and rejects cross-origin writes', async () => {
  process.env.NODE_ENV = 'production';
  delete process.env.API_INTERNAL_URL;
  globalThis.fetch = () => { throw new Error('The API must not be called'); };

  const missingBinding = await GET(new Request('https://linepilot.example/api/v1/health'));
  assert.equal(missingBinding.status, 503);

  const crossOrigin = await POST(new Request('https://linepilot.example/api/v1/jobs', {
    method: 'POST',
    headers: { origin: 'https://other.example' },
  }));
  assert.equal(crossOrigin.status, 403);
});

test('never forwards internal API paths, including encoded variants', async () => {
  process.env.NODE_ENV = 'production';
  process.env.API_INTERNAL_URL = 'https://private-api.internal/';
  globalThis.fetch = () => { throw new Error('Private path reached the API binding'); };

  for (const path of [
    '/api/v1/internal',
    '/api/v1/internal/reconcile-runs',
    '/api/v1/%69nternal/reconcile-runs',
    '/api/v1/%2569nternal/reconcile-runs',
    '/api/v1/%2Finternal/reconcile-runs',
  ]) {
    const response = await POST(new Request(`https://linepilot.example${path}`, { method: 'POST' }));
    assert.equal(response.status, 404, path);
  }
});

test('uses localhost only in development and returns an empty HEAD response', async () => {
  process.env.NODE_ENV = 'development';
  delete process.env.API_INTERNAL_URL;
  globalThis.fetch = async (url) => {
    assert.equal(url.href, 'http://127.0.0.1:8000/api/v1/health');
    return new Response(null, { status: 200, headers: { 'content-type': 'application/json' } });
  };

  const response = await HEAD(new Request('http://localhost:3000/api/v1/health', { method: 'HEAD' }));
  assert.equal(response.status, 200);
  assert.equal(response.body, null);
  assert.equal(response.headers.get('content-type'), 'application/json');
});

test('preserves a binding base path and rewrites internal redirects to the public origin', async () => {
  process.env.NODE_ENV = 'production';
  process.env.API_INTERNAL_URL = 'https://private-api.internal/binding';
  globalThis.fetch = async (url) => {
    assert.equal(url.href, 'https://private-api.internal/binding/api/v1/jobs');
    return new Response(null, {
      status: 307,
      headers: { location: 'https://private-api.internal/binding/api/v1/jobs/' },
    });
  };

  const response = await GET(new Request('https://linepilot.example/api/v1/jobs'));
  assert.equal(response.status, 307);
  assert.equal(response.headers.get('location'), 'https://linepilot.example/api/v1/jobs/');
});
