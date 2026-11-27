"""Offline read-only validator; never returns credentials or enables runtime use."""
import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path

import asyncpg

from app.services.secret_storage import credential_setting, open_setting


async def validate(connection):
    tables = set(await connection.fetch("SELECT tablename FROM pg_tables WHERE schemaname='public'"))
    names = {row['tablename'] for row in tables}
    required = {'workspaces', 'questions', 'exams', 'exam_questions', 'exam_answers', 'app_settings',
                'exam_answer_revisions', 'evidence', 'recovery_control', 'private_model_calls'}
    if not required <= names:
        raise ValueError('Unsupported business schema; required recovery tables missing')
    invalid = await connection.fetchval(
        "SELECT count(*) FROM pg_constraint WHERE connamespace='public'::regnamespace AND contype='f' AND NOT convalidated")
    if invalid:
        raise ValueError('Unvalidated foreign keys')
    orphans = await connection.fetchval(
        "SELECT count(*) FROM exam_answers a LEFT JOIN exam_questions q ON q.id=a.exam_question_id "
        "LEFT JOIN exams e ON e.id=q.exam_id LEFT JOIN questions k ON k.id=q.question_id "
        "LEFT JOIN workspaces w ON w.id=e.workspace_id "
        "WHERE q.id IS NULL OR e.id IS NULL OR k.id IS NULL OR w.id IS NULL")
    if orphans:
        raise ValueError('Business relationships contain orphan answers')
    evidence = {}
    for table in sorted(required - {'app_settings', 'recovery_control'}):
        payload = await connection.fetchval(
            f"SELECT coalesce(jsonb_agg(to_jsonb(t) ORDER BY t.id), '[]'::jsonb)::text FROM {table} t")
        evidence[table] = {'rows': len(json.loads(payload)), 'sha256': hashlib.sha256(payload.encode()).hexdigest()}
    credentials = 0
    for row in await connection.fetch('SELECT key, value FROM app_settings ORDER BY key'):
        if credential_setting(row['key']):
            # Pure G5 decode is intentionally offline and never invokes a runtime resolver/provider.
            open_setting(row['key'], json.loads(row['value']))
            credentials += 1
    return {'business_fingerprints': evidence, 'orphan_answers': orphans,
            'invalid_foreign_keys': invalid, 'credential_rows_authenticated': credentials,
            'external_authorization_verified': False, 'providers_called': 0}


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    database = os.environ['F6_VALIDATION_DATABASE']
    connection = await asyncpg.connect(database=database, host=os.environ['PGHOST'],
                                      port=int(os.environ['PGPORT']), user=os.environ['PGUSER'],
                                      password=os.environ.get('PGPASSWORD'))
    try:
        async with connection.transaction(readonly=True):
            result = await validate(connection)
        args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    finally:
        await connection.close()


if __name__ == '__main__':
    asyncio.run(main())
