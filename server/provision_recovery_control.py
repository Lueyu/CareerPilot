"""Offline, explicit registration of an operator-reviewed known installation."""
import argparse
import asyncio

from sqlalchemy.engine import make_url

from app.config import get_settings
from app.db import SessionLocal, engine
from app.services.recovery_control import provision_known_installation


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--confirm', required=True)
    args = parser.parse_args()
    database = make_url(get_settings().database_url).database
    if args.confirm != f'PROVISION KNOWN INSTALLATION {database}':
        parser.error('Confirmation must bind the reviewed known installation database')
    async with SessionLocal.begin() as session:
        await provision_known_installation(session)
    print('Known installation registered. No restore activation or credential reconciliation performed.')
    await engine.dispose()


if __name__ == '__main__':
    asyncio.run(main())
