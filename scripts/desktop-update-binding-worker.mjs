// Runs only in a Cloudflare-authenticated remote preview. Never deploy this
// module as a public Worker. The plan is generated before creating the preview.
import { environment, operationJSON, planDigest } from './approved-plan.mjs';

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    if (url.search) return new Response('Denied', { status: 403 });
    if (request.method === 'GET' && url.pathname === '/health') {
      return Response.json({ environment, planDigest });
    }
    let path;
    if (request.method === 'GET' && url.pathname === '/status') path = '/status';
    else if (request.method === 'GET' && url.pathname === '/history') path = '/history?limit=100';
    else if (request.method === 'POST' && url.pathname === '/operation' && operationJSON !== null) {
      if (await request.text() !== operationJSON) return new Response('Unapproved operation', { status: 403 });
      path = '/operation';
    } else return new Response('Denied', { status: 403 });
    const stub = env.CONTROL.get(env.CONTROL.idFromName(`desktop:${environment}`));
    // Cloudflare authenticates the account capability that creates this preview
    // and grants its external binding. The actor names this trusted tool, never
    // a user-supplied header or an impersonated human operator.
    return stub.fetch(`https://control${path}`, {
      method: request.method, headers: { 'X-Desktop-Operator': 'release-ci' },
      ...(path === '/operation' ? { body: operationJSON } : {}),
    });
  },
};
