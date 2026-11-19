"""Authenticated HTTP release acceptance; uses the provider configured on the server."""
import argparse
import json
import secrets

import httpx


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--url', default='http://localhost')
    args = parser.parse_args()
    checks = {}
    with httpx.Client(base_url=args.url, timeout=120) as client:
        checks['ready'] = client.get('/health/ready').status_code == 200
        checks['static_frontend'] = '<html' in client.get('/').text.lower()
        checks['monitoring_private'] = client.get('/v1/monitoring/models').status_code == 404
        checks['monitoring_alias_private'] = client.get('/api/v1/monitoring/models').status_code == 404
        checks['docs_private'] = client.get('/docs').status_code == 404
        checks['auth_required'] = client.post('/v1/trips').status_code == 401
        account = client.post('/v1/auth/register', json={
            'email': 'release-' + secrets.token_hex(10) + '@example.com',
            'password': secrets.token_urlsafe(32),
        })
        account.raise_for_status()
        client.headers['Authorization'] = 'Bearer ' + account.json()['access_token']
        response = client.post('/v1/trips')
        response.raise_for_status()
        trip = response.json()['trip_id']
        response = client.post(f'/v1/trips/{trip}/messages', json={
            'text': 'Хочу в Сочи с 21 по 25 ноября из Москвы вдвоём, бюджет 300000 рублей',
        })
        response.raise_for_status()
        state = client.get(f'/v1/trips/{trip}/state')
        state.raise_for_status()
        checks['plan_ready'] = state.json()['outcome'] == 'plan_ready'
        checks['no_blocking_violations'] = not any(
            violation['severity'] == 'blocking' for violation in state.json()['violations'])
        print(json.dumps({'checks': checks, 'outcome': state.json()['outcome']}, ensure_ascii=False, indent=2))
    if not all(checks.values()):
        raise SystemExit(1)


if __name__ == '__main__':
    main()
