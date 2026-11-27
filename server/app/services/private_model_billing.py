"""Private requests reserve money before transport, using independent transactions."""
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Literal
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import func, select, text
from sqlalchemy.exc import SQLAlchemyError

from app.config import get_settings
from app.db import SessionLocal
from app.models.private_model import PrivateModelCall

NANO_CNY = 1_000_000_000
CHINA = timezone(timedelta(hours=8))


class BudgetPolicyError(HTTPException):
    """A local policy refusal must never trigger provider fallback or repair."""

    def __init__(self, code: str, *, retry_after: int = 60, status: int = 429,
                 phase: Literal["admission", "settlement"] = "admission", call_id: str | None = None):
        self.code = self.category = code
        self.phase, self.billing_call_id = phase, call_id
        self.safe_for_user = True
        self.retry_after = retry_after
        super().__init__(status, {"code": code, "phase": phase, "message": "私有模型计费未完成，请检查预算、价格或账本状态；已有费用记录保留。"},
                         headers={"Retry-After": str(retry_after)})

    def __str__(self):
        stage = "准入拒绝" if self.phase == "admission" else "结算未完成，实际请求已经准入"
        return f"私有模型{stage}（{self.code}），请检查预算、定价与账本；已有费用记录保留。"


@dataclass(frozen=True)
class BillingScope:
    """Construct only from authenticated workspace or a persisted owned job/trace."""
    workspace_id: int
    operation: str


@dataclass(frozen=True)
class Charge:
    call_id: str
    state: str
    actual_nano_cny: int | None
    charged_nano_cny: int


def now() -> datetime:
    return datetime.now(CHINA)


def money(value) -> int:
    # Decimal strings are operator declarations, never inferred provider prices.
    try:
        if isinstance(value, (bool, float)) or value is None:
            raise ValueError
        amount = Decimal(value) * NANO_CNY
        if not amount.is_finite() or amount < 0 or amount > 10**15 or amount != amount.to_integral_value():
            raise ValueError
        return int(amount)
    except (InvalidOperation, TypeError, ValueError):
        raise BudgetPolicyError("BILLING_CONFIGURATION", status=503) from None


def cost(prompt: int, completion: int, input_price: int, output_price: int) -> int:
    return (prompt * input_price + completion * output_price + 999_999) // 1_000_000


def usage_tokens(usage, *, has_output: bool = True) -> tuple[int, int] | None:
    if not isinstance(usage, dict):
        return None
    values = usage.get("prompt_tokens"), usage.get("completion_tokens")
    if any(type(v) is not int or not 0 <= v <= 100_000_000 for v in values):
        return None
    if values[0] == 0 or (has_output and values[1] == 0):
        return None
    total = usage.get("total_tokens")
    if total is not None and (type(total) is not int or total != sum(values)):
        return None
    return values


async def _lock(db):
    await db.execute(text("SELECT pg_advisory_xact_lock(2026112802)"))


async def reserve(scope: BillingScope | None, cfg: dict, system: str, user: str, max_tokens: int) -> str:
    if (not isinstance(scope, BillingScope) or type(scope.workspace_id) is not int
            or not 0 < scope.workspace_id < 2**31 or not isinstance(scope.operation, str)
            or not 0 < len(scope.operation) <= 160):
        raise BudgetPolicyError("BILLING_SCOPE_REQUIRED", status=503)
    settings = get_settings()
    endpoint = str(cfg.get("base_url") or "").rstrip("/")
    prices = [p for p in settings.private_llm_prices if isinstance(p, dict)
              and (p.get("provider"), str(p.get("base_url") or "").rstrip("/"), p.get("model"))
              == (cfg.get("provider"), endpoint, cfg.get("model"))]
    if len(prices) != 1:
        raise BudgetPolicyError("MODEL_PRICE_REQUIRED", status=503)
    price = prices[0]
    input_price, output_price = money(price.get("input_cny_per_million")), money(price.get("output_cny_per_million"))
    daily_limit = money(settings.private_llm_daily_budget_cny)
    if daily_limit <= 0:
        raise BudgetPolicyError("DAILY_BUDGET_REQUIRED", status=503)
    if type(max_tokens) is not int or not 0 < max_tokens <= 32000:
        raise BudgetPolicyError("OUTPUT_LIMIT_REQUIRED", status=503)
    # UTF-8 bytes bound ordinary byte tokenizers; template overhead is explicit.
    input_ceiling = len(system.encode()) + len(user.encode()) + 384
    reserved = cost(input_ceiling, max_tokens, input_price, output_price)
    clock = now()
    day = clock.astimezone(CHINA).date()
    call_id = str(uuid4())
    try:
        async with SessionLocal.begin() as db:
            await _lock(db)
            reset = datetime.combine(day + timedelta(days=1), datetime.min.time(), CHINA)
            retry_after = max(1, int((reset - clock).total_seconds()) + 1)
            overrun = await db.scalar(select(PrivateModelCall.id).where(
                PrivateModelCall.day == day,
                PrivateModelCall.actual_nano_cny > PrivateModelCall.reserved_nano_cny,
            ).limit(1))
            if overrun is not None:
                raise BudgetPolicyError("RESERVATION_EXCEEDED", retry_after=retry_after)
            spent = await db.scalar(select(func.coalesce(func.sum(PrivateModelCall.charged_nano_cny), 0)).where(
                PrivateModelCall.day == day))
            if spent + reserved > daily_limit:
                raise BudgetPolicyError("DAILY_BUDGET_EXHAUSTED", retry_after=retry_after)
            db.add(PrivateModelCall(id=call_id, workspace_id=scope.workspace_id, operation=scope.operation,
                day=day, provider=cfg["provider"], endpoint=endpoint, model=cfg["model"],
                input_price_nano_per_million=input_price, output_price_nano_per_million=output_price,
                daily_limit_nano_cny=daily_limit, input_ceiling=input_ceiling, max_tokens=max_tokens,
                reserved_nano_cny=reserved, charged_nano_cny=reserved, state="RESERVED"))
    except SQLAlchemyError:
        raise BudgetPolicyError("BILLING_UNAVAILABLE", status=503) from None
    return call_id


async def settle(call_id: str, usage, *, has_output: bool = True) -> Charge:
    """The first settlement wins. Missing/invalid usage never releases the reserve."""
    try:
        async with SessionLocal.begin() as db:
            call = await db.get(PrivateModelCall, call_id)
            if call is not None:
                await _lock(db)
                call = await db.get(PrivateModelCall, call_id, with_for_update=True, populate_existing=True)
            if call is None:
                raise BudgetPolicyError("BILLING_CALL_MISSING", status=503, phase="settlement", call_id=call_id)
            if call.state == "RESERVED":
                tokens = usage_tokens(usage, has_output=has_output)
                if tokens is None:
                    call.state = "UNKNOWN"
                else:
                    call.prompt_tokens, call.completion_tokens = tokens
                    call.actual_nano_cny = cost(*tokens, call.input_price_nano_per_million,
                                                call.output_price_nano_per_million)
                    call.charged_nano_cny = call.actual_nano_cny
                    call.state = "SETTLED"
                call.settled_at = now()
            return Charge(call.id, call.state, call.actual_nano_cny, call.charged_nano_cny)
    except SQLAlchemyError:
        # The committed reservation survives even if settlement is unavailable.
        raise BudgetPolicyError("BILLING_UNAVAILABLE", status=503, phase="settlement", call_id=call_id) from None
