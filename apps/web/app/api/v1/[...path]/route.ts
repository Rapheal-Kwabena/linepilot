export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

const API_PREFIX = '/api/v1/';
const REQUEST_HEADERS = [
  'accept',
  'accept-language',
  'content-type',
  'if-modified-since',
  'if-none-match',
  'if-range',
  'range',
] as const;
const RESPONSE_HEADERS = [
  'accept-ranges',
  'content-disposition',
  'content-language',
  'content-range',
  'content-type',
  'etag',
  'last-modified',
] as const;

function errorResponse(status: number, code: string, message: string): Response {
  return Response.json(
    { error: { code, message, details: null } },
    { status, headers: { 'Cache-Control': 'private, no-store' } },
  );
}

function isPrivateApiPath(pathname: string): boolean {
  let path = pathname;
  for (let attempt = 0; attempt < 4; attempt += 1) {
    const segments = path.split('/').filter(Boolean).map((part) => part.toLowerCase());
    if (segments[0] === 'api' && segments[1] === 'v1' && segments[2] === 'internal') return true;

    try {
      const decoded = decodeURIComponent(path);
      if (decoded === path) return false;
      path = decoded;
    } catch {
      return true;
    }
  }
  return false;
}

function internalApiUrl(): URL | null {
  const value = process.env.API_INTERNAL_URL?.trim()
    || (process.env.NODE_ENV === 'development' ? 'http://127.0.0.1:8000' : '');
  if (!value) return null;

  try {
    const url = new URL(value);
    if (!['http:', 'https:'].includes(url.protocol)) return null;
    if (!url.pathname.endsWith('/')) url.pathname += '/';
    return url;
  } catch {
    return null;
  }
}

async function proxy(request: Request): Promise<Response> {
  const incomingUrl = new URL(request.url);
  if (!incomingUrl.pathname.startsWith(API_PREFIX)) {
    return errorResponse(404, 'not_found', 'API route not found');
  }
  if (isPrivateApiPath(incomingUrl.pathname)) {
    return errorResponse(404, 'not_found', 'API route not found');
  }

  // Browser writes must originate from this deployment. The Vercel Authentication
  // cookie is checked at public ingress, before this function runs.
  if (!['GET', 'HEAD', 'OPTIONS'].includes(request.method)) {
    const origin = request.headers.get('origin');
    if (origin && origin !== incomingUrl.origin) {
      return errorResponse(403, 'forbidden_origin', 'Cross-origin API writes are not allowed');
    }
  }

  const baseUrl = internalApiUrl();
  if (!baseUrl) {
    return errorResponse(503, 'api_unavailable', 'LINEPILOT API is unavailable');
  }

  const upstreamUrl = new URL(incomingUrl.pathname.slice(1) + incomingUrl.search, baseUrl);
  const headers = new Headers();
  for (const name of REQUEST_HEADERS) {
    const value = request.headers.get(name);
    if (value !== null) headers.set(name, value);
  }

  const body = ['GET', 'HEAD'].includes(request.method) ? null : request.body;
  const options: RequestInit & { duplex?: 'half' } = {
    method: request.method,
    headers,
    cache: 'no-store',
    redirect: 'manual',
    signal: request.signal,
  };
  if (body) {
    options.body = body;
    options.duplex = 'half';
  }

  try {
    const upstream = await fetch(upstreamUrl, options);
    const responseHeaders = new Headers({ 'Cache-Control': 'private, no-store' });
    for (const name of RESPONSE_HEADERS) {
      const value = upstream.headers.get(name);
      if (value !== null) responseHeaders.set(name, value);
    }

    const location = upstream.headers.get('location');
    if (location) {
      const redirectUrl = new URL(location, upstreamUrl);
      if (redirectUrl.origin === upstreamUrl.origin) {
        if (baseUrl.pathname !== '/' && redirectUrl.pathname.startsWith(baseUrl.pathname)) {
          redirectUrl.pathname = '/' + redirectUrl.pathname.slice(baseUrl.pathname.length);
        }
        redirectUrl.protocol = incomingUrl.protocol;
        redirectUrl.host = incomingUrl.host;
      }
      responseHeaders.set('Location', redirectUrl.toString());
    }

    const noBody = request.method === 'HEAD' || [204, 205, 304].includes(upstream.status);
    return new Response(noBody ? null : upstream.body, {
      status: upstream.status,
      statusText: upstream.statusText,
      headers: responseHeaders,
    });
  } catch {
    return errorResponse(502, 'api_unavailable', 'LINEPILOT API is unavailable');
  }
}

export const GET = proxy;
export const POST = proxy;
export const PUT = proxy;
export const PATCH = proxy;
export const DELETE = proxy;
export const HEAD = proxy;
export const OPTIONS = proxy;
