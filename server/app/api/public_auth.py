"""Session-cookie authentication for the public CareerPilot entry point."""

import re
import secrets
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from pydantic import AfterValidator, BaseModel, Field, SecretStr, StringConstraints
from sqlalchemy import delete, func, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.i18n import message as localized_message
from app.db import get_session
from app.models.exam import Exam
from app.models.learning_feedback import LearningActivity
from app.models.public_auth import PublicAccount, PublicSession
from app.models.question import ImportSession
from app.models.workspace import User, Workspace, WorkspaceMember
from app.services.public_identity import (
    AttemptLimit,
    auth_config,
    client_ip,
    credential_digest,
    hash_password,
    password_matches,
    record_attempt,
)

_SESSION_AGE = timedelta(days=7)
_ATTEMPT_WINDOW = timedelta(minutes=15)
_SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


def validate_auth_config() -> None:
    """Call during public app startup, before serving requests."""
    auth_config()


async def public_origin_guard(request: Request, call_next) -> Response:
    try:
        config = auth_config()
    except RuntimeError:
        return JSONResponse({"detail": localized_message("auth.unconfigured")}, status_code=503)

    if request.headers.getlist("host") != [config.host]:
        return JSONResponse({"detail": localized_message("auth.host")}, status_code=403)
    if request.method not in _SAFE_METHODS and request.headers.getlist("origin") != [config.origin]:
        return JSONResponse({"detail": localized_message("auth.origin")}, status_code=403)
    return await call_next(request)


class AuthRoute(APIRoute):
    def get_route_handler(self):
        handle = super().get_route_handler()

        async def auth_handler(request: Request) -> Response:
            try:
                response = await handle(request)
            except RequestValidationError:
                response = JSONResponse(
                    {"detail": localized_message("auth.format")},
                    status_code=422,
                )
            except HTTPException as exc:
                response = JSONResponse({"detail": exc.detail}, status_code=exc.status_code, headers=exc.headers)
            response.headers["Cache-Control"] = "no-store"
            return response

        return auth_handler


router = APIRouter(prefix="/auth", route_class=AuthRoute)
Database = Annotated[AsyncSession, Depends(get_session)]
Username = Annotated[
    str,
    StringConstraints(strip_whitespace=True, pattern=r"^[A-Za-z0-9_]{3,32}$"),
    AfterValidator(str.lower),
]


class PasswordInput(BaseModel):
    password: SecretStr = Field(min_length=1, max_length=128)


class LoginInput(PasswordInput):
    username: Username


class RegisterInput(LoginInput):
    password: SecretStr = Field(min_length=12, max_length=128)


class RecoveryInput(RegisterInput):
    recovery_code: Annotated[str, StringConstraints(strip_whitespace=True, pattern=r"^[A-Za-z0-9_-]{32}$")]


class AccountInfo(BaseModel):
    username: str
    workspace_id: int


class RecoveryInfo(AccountInfo):
    recovery_code: str


def _cookie_digest(request: Request) -> str | None:
    token = request.cookies.get(auth_config().cookie_name, "")
    if re.fullmatch(r"[A-Za-z0-9_-]{43}", token):
        return credential_digest("session", token)
    return None


def _unauthenticated() -> HTTPException:
    return HTTPException(status_code=401, detail={"code": "session_required", "message": localized_message("errors.sign_in_to_continue")})


def _workspace_expectation(request: Request) -> list[str]:
    # EventSource cannot send custom request headers.
    return request.headers.getlist("x-expected-workspace") + request.query_params.getlist("expected_workspace_id")


async def _session_account(request: Request, session: Database) -> PublicAccount:
    digest = _cookie_digest(request)
    if digest is not None:
        account = await session.scalar(
            select(PublicAccount)
            .join(PublicSession, PublicSession.account_id == PublicAccount.id)
            .where(PublicSession.token_hash == digest, PublicSession.expires_at > func.now())
        )
        if account is not None:
            expected = _workspace_expectation(request)
            if expected and expected != [str(account.workspace_id)]:
                raise HTTPException(
                    status_code=409,
                    detail={"code": "workspace_changed", "message": localized_message("errors.the_signed_in_account_has_changed")},
                )
            return account
    raise _unauthenticated()


async def current_workspace(
    request: Request,
    session: Database,
    account: Annotated[PublicAccount, Depends(_session_account)],
) -> Workspace:
    workspace = await session.get(Workspace, account.workspace_id)
    if workspace is None:
        raise _unauthenticated()
    request.state.registered_workspace_slug = workspace.slug
    return workspace


async def _lock_username(session: AsyncSession, username: str) -> PublicAccount | None:
    return await session.scalar(select(PublicAccount).where(PublicAccount.username == username).with_for_update())


async def _open_session(request: Request, response: Response, session: AsyncSession, account_id: int) -> None:
    existing_digest = _cookie_digest(request)
    await session.execute(
        delete(PublicSession).where(
            PublicSession.account_id == account_id,
            or_(PublicSession.expires_at <= func.now(), PublicSession.token_hash == existing_digest),
        )
    )
    token = secrets.token_urlsafe(32)
    session.add(
        PublicSession(
            token_hash=credential_digest("session", token),
            account_id=account_id,
            expires_at=datetime.now(UTC) + _SESSION_AGE,
        )
    )
    await session.commit()
    config = auth_config()
    response.set_cookie(
        config.cookie_name,
        token,
        max_age=int(_SESSION_AGE.total_seconds()),
        path="/",
        secure=config.secure_cookie,
        httponly=True,
        samesite="strict",
    )


def _clear_cookie(response: Response) -> None:
    config = auth_config()
    response.delete_cookie(
        config.cookie_name, path="/", secure=config.secure_cookie, httponly=True, samesite="strict"
    )


@router.post("/register", status_code=201)
async def register(body: RegisterInput, request: Request, response: Response, session: Database) -> RecoveryInfo:
    await record_attempt(session, AttemptLimit("register-ip", client_ip(request), 6, timedelta(hours=1)))

    password_hash = await hash_password(body.password.get_secret_value())
    recovery_code = secrets.token_urlsafe(24)
    nonce = secrets.token_hex(16)
    workspace = Workspace(slug=f"seed-{nonce}", name=body.username, target_role="", target_level="", is_demo=False)
    user = User(email=f"seed-{nonce}@accounts.invalid", display_name=body.username)
    session.add_all([workspace, user])
    await session.flush()
    session.add(WorkspaceMember(workspace_id=workspace.id, user_id=user.id, role="owner"))
    account_id = await session.scalar(
        insert(PublicAccount)
        .values(
            username=body.username,
            workspace_id=workspace.id,
            user_id=user.id,
            password_hash=password_hash,
            recovery_hash=credential_digest("recovery", recovery_code),
        )
        .on_conflict_do_nothing(index_elements=[PublicAccount.username])
        .returning(PublicAccount.id)
    )
    if account_id is None:
        await session.rollback()
        raise HTTPException(status_code=409, detail=localized_message("errors.this_username_is_taken_choose_another"))
    workspace_id = workspace.id
    await _open_session(request, response, session, account_id)
    return RecoveryInfo(username=body.username, workspace_id=workspace_id, recovery_code=recovery_code)


@router.post("/login")
async def login(body: LoginInput, request: Request, response: Response, session: Database) -> AccountInfo:
    await record_attempt(
        session,
        AttemptLimit("login-ip", client_ip(request), 30, _ATTEMPT_WINDOW),
        AttemptLimit("login-name", body.username, 10, _ATTEMPT_WINDOW),
    )
    account = await _lock_username(session, body.username)
    password = body.password.get_secret_value()
    if account is None:
        await password_matches(password)
        raise HTTPException(status_code=401, detail=localized_message("auth.credentials"))
    if not await password_matches(password, account.password_hash):
        raise HTTPException(status_code=401, detail=localized_message("auth.credentials"))

    info = AccountInfo(username=account.username, workspace_id=account.workspace_id)
    await _open_session(request, response, session, account.id)
    return info


@router.post("/recover")
async def recover(body: RecoveryInput, request: Request, response: Response, session: Database) -> RecoveryInfo:
    await record_attempt(
        session,
        AttemptLimit("recover-ip", client_ip(request), 15, _ATTEMPT_WINDOW),
        AttemptLimit("recover-name", body.username, 6, _ATTEMPT_WINDOW),
    )
    account = await _lock_username(session, body.username)
    supplied = credential_digest("recovery", body.recovery_code)
    if account is None or not secrets.compare_digest(account.recovery_hash, supplied):
        raise HTTPException(status_code=401, detail=localized_message("auth.recovery"))

    recovery_code = secrets.token_urlsafe(24)
    account.password_hash = await hash_password(body.password.get_secret_value())
    account.recovery_hash = credential_digest("recovery", recovery_code)
    await session.execute(delete(PublicSession).where(PublicSession.account_id == account.id))
    info = RecoveryInfo(username=account.username, workspace_id=account.workspace_id, recovery_code=recovery_code)
    await _open_session(request, response, session, account.id)
    return info


@router.get("/me")
async def me(account: Annotated[PublicAccount, Depends(_session_account)]) -> AccountInfo:
    return AccountInfo(username=account.username, workspace_id=account.workspace_id)


@router.post("/logout", status_code=204)
async def logout(request: Request, response: Response, session: Database) -> None:
    bound = bool(_workspace_expectation(request))
    if bound:
        await _session_account(request, session)
    digest = _cookie_digest(request)
    if digest is not None:
        await session.execute(delete(PublicSession).where(PublicSession.token_hash == digest))
        await session.commit()
    # A bound browser may have installed another account's cookie while this request was in flight.
    if not bound:
        _clear_cookie(response)


@router.delete("/account", status_code=204)
async def delete_account(
    body: PasswordInput,
    request: Request,
    response: Response,
    session: Database,
    authenticated: Annotated[PublicAccount, Depends(_session_account)],
) -> None:
    account_id = authenticated.id
    await record_attempt(session, AttemptLimit("delete-account", str(account_id), 10, _ATTEMPT_WINDOW))

    # Recheck the session after acquiring the lock. Recovery may have revoked it while we waited.
    account = await session.get(PublicAccount, account_id, with_for_update=True, populate_existing=True)
    if account is None or (await _session_account(request, session)).id != account_id:
        raise _unauthenticated()
    if not await password_matches(body.password.get_secret_value(), account.password_hash):
        raise HTTPException(status_code=401, detail=localized_message("errors.incorrect_password_your_account_has_not_been_deleted"))

    # Imported items and exam questions can reference questions owned by this workspace.
    workspace = await session.scalar(
        select(Workspace)
        .where(
            Workspace.id == account.workspace_id,
            Workspace.is_demo.is_(False),
            Workspace.slug.regexp_match(r"^seed-[0-9a-f]{32}$"),
        )
        .with_for_update()
    )
    if workspace is None:
        raise HTTPException(status_code=409, detail=localized_message("errors.this_workspace_cannot_be_deleted_through_a_registered_account"))
    from app.services.public_native_runtime import remove_native_workspace
    await remove_native_workspace(workspace)
    workspace_id = workspace.id
    await session.execute(delete(LearningActivity).where(LearningActivity.workspace_id == workspace_id))
    user_id = account.user_id
    await session.execute(delete(ImportSession).where(ImportSession.workspace_id == workspace_id))
    await session.execute(delete(Exam).where(Exam.workspace_id == workspace_id))
    await session.execute(delete(Workspace).where(Workspace.id == workspace_id))
    await session.execute(delete(User).where(User.id == user_id))
    await session.commit()
    if not _workspace_expectation(request):
        _clear_cookie(response)
