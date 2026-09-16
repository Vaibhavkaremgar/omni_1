// Production builds must still reach the deployed API when the hosting project
// omitted the optional Vercel environment variable. Local development can
// override this with VITE_BACKEND_URL=http://127.0.0.1:8000.
const configuredBackendUrl = import.meta.env.VITE_BACKEND_URL ?? 'https://spirited-reflection-production-39d7.up.railway.app';

/** Build an API URL with one separator and exactly one /api/v1 prefix. */
export function backendUrl(path = ''): string {
  const base = configuredBackendUrl.replace(/\/+$/, '').replace(/\/api\/v1$/, '');
  const normalizedPath = path.replace(/^\/+/, '');
  return `${base}/api/v1/${normalizedPath}`;
}
