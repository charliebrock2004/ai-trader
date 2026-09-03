"""Explicit money units.

The account has exactly one base accounting currency (GBP by default). Market
instruments are quoted in their own currency. Every crossing between the two
goes through :func:`convert`, which requires an explicit :class:`FxRate` — there
is no implicit "a dollar is a pound" path anywhere in the engine.

Amounts are carried as ``Decimal`` internally so repeated conversion does not
accumulate binary-float drift, and exposed as rounded floats at the boundary.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any, Union

Number = Union[int, float, str, Decimal]

BASE_CURRENCY = "GBP"

#: Currencies the engine knows how to hold or quote in.
KNOWN_CURRENCIES = frozenset({"GBP", "USD", "EUR"})

#: Money is stored to the penny. Contract prices use more precision (see QUANTUM_PRICE).
QUANTUM_MONEY = Decimal("0.01")
QUANTUM_PRICE = Decimal("0.000001")


class CurrencyMismatchError(ValueError):
    """Raised when two amounts in different currencies are combined."""


class MissingFxRateError(RuntimeError):
    """Raised when a conversion is attempted without an explicit rate.

    Fail closed: the engine refuses to guess an exchange rate.
    """


def _dec(value: Number) -> Decimal:
    if isinstance(value, Decimal):
        return value
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:  # pragma: no cover - defensive
        raise ValueError(f"Not a numeric amount: {value!r}") from exc


def normalise_currency(code: str) -> str:
    cleaned = (code or "").strip().upper()
    if cleaned not in KNOWN_CURRENCIES:
        raise ValueError(f"Unknown currency {code!r}. Known: {sorted(KNOWN_CURRENCIES)}.")
    return cleaned


def quantize_money(value: Number) -> Decimal:
    return _dec(value).quantize(QUANTUM_MONEY, rounding=ROUND_HALF_UP)


def quantize_price(value: Number) -> Decimal:
    return _dec(value).quantize(QUANTUM_PRICE, rounding=ROUND_HALF_UP)


@dataclass(frozen=True)
class Money:
    """An amount with a currency attached. Immutable."""

    amount: Decimal
    currency: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "currency", normalise_currency(self.currency))
        object.__setattr__(self, "amount", _dec(self.amount))

    # -- construction -----------------------------------------------------
    @classmethod
    def of(cls, amount: Number, currency: str = BASE_CURRENCY) -> "Money":
        return cls(amount=_dec(amount), currency=currency)

    @classmethod
    def zero(cls, currency: str = BASE_CURRENCY) -> "Money":
        return cls(amount=Decimal("0"), currency=currency)

    # -- arithmetic -------------------------------------------------------
    def _same(self, other: "Money") -> None:
        if self.currency != other.currency:
            raise CurrencyMismatchError(
                f"Cannot combine {self.currency} with {other.currency}. "
                "Convert explicitly with an FxRate first."
            )

    def __add__(self, other: "Money") -> "Money":
        self._same(other)
        return Money(self.amount + other.amount, self.currency)

    def __sub__(self, other: "Money") -> "Money":
        self._same(other)
        return Money(self.amount - other.amount, self.currency)

    def __mul__(self, factor: Number) -> "Money":
        return Money(self.amount * _dec(factor), self.currency)

    def __neg__(self) -> "Money":
        return Money(-self.amount, self.currency)

    def __lt__(self, other: "Money") -> bool:
        self._same(other)
        return self.amount < other.amount

    def __le__(self, other: "Money") -> bool:
        self._same(other)
        return self.amount <= other.amount

    def __gt__(self, other: "Money") -> bool:
        self._same(other)
        return self.amount > other.amount

    def __ge__(self, other: "Money") -> bool:
        self._same(other)
        return self.amount >= other.amount

    # -- presentation -----------------------------------------------------
    def rounded(self) -> "Money":
        return Money(quantize_money(self.amount), self.currency)

    @property
    def float_amount(self) -> float:
        return float(quantize_money(self.amount))

    def to_dict(self) -> dict[str, Any]:
        return {"amount": self.float_amount, "currency": self.currency}

    def __str__(self) -> str:  # pragma: no cover - display only
        return f"{self.float_amount:.2f} {self.currency}"


@dataclass(frozen=True)
class FxRate:
    """One explicit exchange rate observation.

    ``rate`` is how many units of ``quote`` one unit of ``base`` buys, i.e.
    ``amount_in_quote = amount_in_base * rate``.
    """

    base: str
    quote: str
    rate: Decimal
    as_of: str
    source: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "base", normalise_currency(self.base))
        object.__setattr__(self, "quote", normalise_currency(self.quote))
        rate = _dec(self.rate)
        if rate <= 0:
            raise ValueError("FX rate must be positive.")
        object.__setattr__(self, "rate", rate)

    def inverse(self) -> "FxRate":
        return FxRate(
            base=self.quote,
            quote=self.base,
            rate=Decimal(1) / self.rate,
            as_of=self.as_of,
            source=self.source,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "base": self.base,
            "quote": self.quote,
            "rate": float(self.rate),
            "as_of": self.as_of,
            "source": self.source,
        }


def convert(amount: Money, to_currency: str, rate: FxRate | None) -> Money:
    """Convert ``amount`` into ``to_currency`` using an explicit rate.

    A same-currency conversion is a no-op and needs no rate. Anything else
    without a matching rate raises :class:`MissingFxRateError` — the engine
    never guesses.
    """
    target = normalise_currency(to_currency)
    if amount.currency == target:
        return amount
    if rate is None:
        raise MissingFxRateError(
            f"No FX rate available for {amount.currency}->{target}. Refusing to guess."
        )
    if rate.base == amount.currency and rate.quote == target:
        return Money(amount.amount * rate.rate, target)
    if rate.base == target and rate.quote == amount.currency:
        return Money(amount.amount / rate.rate, target)
    raise MissingFxRateError(
        f"FX rate {rate.base}->{rate.quote} does not cover {amount.currency}->{target}."
    )


def money_float(value: Number) -> float:
    """Round a bare number to money precision. For legacy float call sites."""
    return float(quantize_money(value))
