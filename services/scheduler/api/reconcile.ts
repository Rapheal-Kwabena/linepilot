import { handleSchedule } from '@vercel/schedules';

export const POST = handleSchedule(async () => {
  const binding = process.env.API_INTERNAL_URL;
  const secret = process.env.CRON_SECRET;
  if (!binding || !secret) {
    throw new Error('Reconciliation requires the API binding and CRON_SECRET');
  }

  const baseUrl = new URL(binding);
  if (!['http:', 'https:'].includes(baseUrl.protocol)) {
    throw new Error('Reconciliation API binding must be an HTTP URL');
  }
  if (!baseUrl.pathname.endsWith('/')) baseUrl.pathname += '/';
  const url = new URL('api/v1/internal/reconcile-runs', baseUrl);

  const response = await fetch(url, {
    method: 'POST',
    headers: { Authorization: `Bearer ${secret}` },
    cache: 'no-store',
  });
  if (!response.ok) {
    throw new Error(`Reconciliation API returned HTTP ${response.status}`);
  }
});
