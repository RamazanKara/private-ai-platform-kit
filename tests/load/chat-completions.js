import http from 'k6/http';
import { check, sleep } from 'k6';

export const options = {
  scenarios: {
    chat: {
      executor: 'constant-vus',
      vus: Number(__ENV.VUS || 5),
      duration: __ENV.DURATION || '1m',
    },
  },
  thresholds: {
    http_req_failed: ['rate<0.05'],
    http_req_duration: ['p(95)<10000'],
  },
};

const baseUrl = __ENV.GATEWAY_URL || 'http://127.0.0.1:8080';
const model = __ENV.MODEL_ID || 'llama3.2:1b';
const sandboxId = __ENV.SANDBOX_ID || 'local-lab';
const apiKey = __ENV.PLATFORM_API_KEY || __ENV.API_KEY || '';

export default function () {
  const requestId = `k6-${__VU}-${__ITER}`;
  const payload = JSON.stringify({
    model,
    messages: [
      {
        role: 'user',
        content: 'Reply with a short greeting from AI Platform Ops Lab.',
      },
    ],
  });
  const headers = {
    'Content-Type': 'application/json',
    'X-Request-ID': requestId,
    'X-Sandbox-ID': sandboxId,
    traceparent: '00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01',
  };
  if (apiKey) {
    headers['X-API-Key'] = apiKey;
  }
  const response = http.post(`${baseUrl}/v1/chat/completions`, payload, {
    headers,
    tags: {
      runtime_backend: __ENV.RUNTIME_BACKEND || 'ollama',
      model,
      sandbox_id: sandboxId,
    },
  });
  check(response, {
    'status is 200': (r) => r.status === 200,
    'request id returned': (r) => r.headers['X-Request-Id'] === requestId,
    'sandbox id returned': (r) => r.headers['X-Sandbox-Id'] === sandboxId,
    'has assistant content': (r) => {
      try {
        const body = r.json();
        return Boolean(body.choices[0].message.content);
      } catch (e) {
        return false;
      }
    },
  });
  sleep(1);
}
