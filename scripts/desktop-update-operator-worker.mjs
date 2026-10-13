const READ_ACTIONS = new Set(['status', 'history']);
const WRITE_ACTIONS = new Set(['recover', 'prepare', 'publish']);
const JSON_HEADERS = {
  'Cache-Control': 'no-store',
  'Content-Type': 'application/json',
};

function json(body, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: JSON_HEADERS });
}

function equalSecret(left, right) {
  const a = new TextEncoder().encode(String(left || ''));
  const b = new TextEncoder().encode(String(right || ''));
  let different = a.length ^ b.length;
  const length = Math.max(a.length, b.length);
  for (let index = 0; index < length; index++) {
    different |= (a[index] || 0) ^ (b[index] || 0);
  }
  return different === 0;
}

function validEnvironment(env) {
  return env.TARGET_ORIGIN === 'https://backbone-mem.nowledge.co'
    && /^[a-zA-Z0-9_.@-]{1,100}$/.test(env.OPERATOR_NAME || '')
    && /^[a-f0-9]{64}$/.test(env.INVOCATION_NONCE || '')
    && Number.isFinite(Date.parse(env.EXPIRES_AT || ''))
    && Date.now() < Date.parse(env.EXPIRES_AT);
}

async function readRequest(request) {
  const contentLength = Number(request.headers.get('Content-Length') || 0);
  if (contentLength > 32_768) throw new Error('request_invalid');
  const value = await request.json();
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    throw new Error('request_invalid');
  }
  const allowed = READ_ACTIONS.has(value.action) || WRITE_ACTIONS.has(value.action);
  if (!allowed) throw new Error('request_invalid');
  return value;
}

async function readOperatorToken(env) {
  if (!env.DESKTOP_UPDATE_OPERATOR_TOKENS_STORE) throw new Error('operator_store_unavailable');
  const operators = JSON.parse(await env.DESKTOP_UPDATE_OPERATOR_TOKENS_STORE.get());
  if (!operators || Array.isArray(operators) || typeof operators !== 'object') {
    throw new Error('operator_store_unavailable');
  }
  const credentials = Object.values(operators);
  if (credentials.some((value) => typeof value !== 'string' || value.length < 32)
      || new Set(credentials).size !== credentials.length) {
    throw new Error('operator_store_unavailable');
  }
  const token = operators[env.OPERATOR_NAME];
  if (typeof token !== 'string') throw new Error('operator_not_configured');
  return token;
}

export async function handleOperatorRequest(request, env, fetcher = fetch) {
  try {
    const url = new URL(request.url);
    if (request.method !== 'POST' || url.pathname !== '/invoke') {
      return json({ error_code: 'method_not_allowed' }, 405);
    }
    if (!validEnvironment(env)) return json({ error_code: 'operator_expired' }, 403);
    if (!equalSecret(request.headers.get('X-Invocation-Nonce'), env.INVOCATION_NONCE)) {
      return json({ error_code: 'operator_denied' }, 401);
    }

    const operation = await readRequest(request);
    const token = await readOperatorToken(env);
    const history = operation.action === 'history';
    const read = READ_ACTIONS.has(operation.action);
    const target = new URL(history ? '/admin/desktop-updates/history' : '/admin/desktop-updates', env.TARGET_ORIGIN);
    if (history && operation.limit) target.searchParams.set('limit', String(operation.limit));
    const response = await fetcher(target, {
      method: read ? 'GET' : 'POST',
      redirect: 'manual',
      headers: {
        Authorization: `Bearer ${token}`,
        'Content-Type': 'application/json',
        'User-Agent': 'Nowledge-Mem-Release-Operator/1',
      },
      ...(read ? {} : { body: JSON.stringify(operation) }),
    });
    if (response.status >= 300 && response.status < 400) {
      return json({ error_code: 'operator_upstream_redirect' }, 502);
    }
    const result = await response.json();
    if (response.ok && !read && result.actor !== env.OPERATOR_NAME) {
      return json({ error_code: 'operator_identity_mismatch' }, 502);
    }
    return json(result, response.status);
  } catch {
    return json({ error_code: 'operator_gateway_unavailable' }, 503);
  }
}

export default {
  fetch(request, env) {
    return handleOperatorRequest(request, env);
  },
};
