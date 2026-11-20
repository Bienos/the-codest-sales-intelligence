# ADR-0003 Vite/React SPA served by the API instead of Next.js
All authorisation is server-side; the UI needs no SSR. A static bundle served by FastAPI removes a Node runtime from production. SSO is delegated to an authenticating proxy that forwards the OIDC bearer; the API verifies RS256/JWKS itself. Revisit if SEO/SSR or edge rendering is required.
