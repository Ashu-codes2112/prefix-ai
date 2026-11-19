/**
 * The content security policy, with a fresh nonce for every page request (see src/proxy.ts).
 *
 * Scripts run only with the nonce: Next.js reads it from the request's policy and puts it on
 * its own scripts, and 'strict-dynamic' lets those load the page's chunks. No inline script
 * without the nonce runs, so 'unsafe-inline' is gone from script-src. Development adds
 * 'unsafe-eval', which React uses there for its error overlay.
 *
 * Styles keep 'unsafe-inline': animations set style attributes, which nonces cannot cover.
 */
export function contentSecurityPolicy(nonce: string, dev: boolean): string {
  return [
    "default-src 'self'",
    `script-src 'self' 'nonce-${nonce}' 'strict-dynamic'${dev ? " 'unsafe-eval'" : ""}`,
    "style-src 'self' 'unsafe-inline'",
    "img-src 'self' data: blob:",
    "font-src 'self' data:",
    "connect-src 'self'",
    "manifest-src 'self'",
    "object-src 'none'",
    "base-uri 'self'",
    "form-action 'self'",
    "frame-ancestors 'none'",
  ].join("; ");
}

/** 128 random bits, base64: unguessable, and new for every request. */
export function newNonce(): string {
  const bytes = new Uint8Array(16);
  crypto.getRandomValues(bytes);
  return btoa(String.fromCharCode(...bytes));
}
