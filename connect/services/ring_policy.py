"""Calculate ring admission from an explicit immutable policy snapshot.

This service does not authenticate callers or collectors, adopt policies, send
configuration, enforce a carrier route, or prove an audible handset tone.
Authorized integrations must supply the snapshot and independently established
ingress context. After-hours admission has no exception surface in this first
foundation; caller ID, digits and signaling headers cannot create authority.
"""

from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
import math
import re
from zoneinfo import ZoneInfo


_MAXIMUM_INTEGER = (1 << 63) - 1


def _nonempty_string(value):
    return isinstance(value, str) and bool(value.strip())


def _positive_integer(value):
    return type(value) is int and 0 < value <= _MAXIMUM_INTEGER


def _finite_nonnegative(value):
    if type(value) is int:
        return 0 <= value <= _MAXIMUM_INTEGER
    return type(value) is float and math.isfinite(value) and value >= 0


def _utc(value):
    if not _nonempty_string(value):
        raise ValueError("A UTC timestamp string is required")
    instant = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if instant.tzinfo is None or instant.utcoffset() != timedelta(0):
        raise ValueError("A UTC timestamp with an explicit offset is required")
    return instant.astimezone(timezone.utc)


@dataclass(frozen=True)
class RecipientScope:
    company_id: int
    person_id: int
    endpoint_id: int
    configuration_epoch: int

    def __post_init__(self):
        if not all(_positive_integer(value) for value in (
                self.company_id, self.person_id, self.endpoint_id,
                self.configuration_epoch)):
            raise ValueError("Recipient identifiers and epoch must be positive integers")


@dataclass(frozen=True)
class AccountBinding:
    provider: str
    tool_id: int
    slot: int

    def __post_init__(self):
        if (not _nonempty_string(self.provider)
                or not _positive_integer(self.tool_id)
                or not _positive_integer(self.slot)):
            raise ValueError("An explicit provider, tool and one-based slot are required")


@dataclass(frozen=True)
class WeeklyInterval:
    weekday: int
    opening_minute: int
    closing_minute: int

    def __post_init__(self):
        if (type(self.weekday) is not int or self.weekday not in range(7)
                or type(self.opening_minute) is not int
                or type(self.closing_minute) is not int
                or not 0 <= self.opening_minute < self.closing_minute <= 1440):
            raise ValueError("Use Monday=0..Sunday=6 and a nonempty same-day minute interval")


@dataclass(frozen=True)
class RingPolicySnapshot:
    revision: str
    recipient: RecipientScope
    timezone: str
    bindings: tuple[AccountBinding, ...]
    intervals: tuple[WeeklyInterval, ...]
    closed_dates: tuple[str, ...]
    maximum_clock_uncertainty_seconds: float
    maximum_event_age_seconds: float
    business_tone: str

    def __post_init__(self):
        if not _nonempty_string(self.revision) or type(self.recipient) is not RecipientScope:
            raise ValueError("An explicit revision and recipient scope are required")
        if not _nonempty_string(self.timezone):
            raise ValueError("An explicit named timezone is required")
        ZoneInfo(self.timezone)
        if (type(self.bindings) is not tuple or not self.bindings
                or any(type(binding) is not AccountBinding for binding in self.bindings)):
            raise ValueError("Bindings must be a nonempty immutable tuple")
        if len({binding.slot for binding in self.bindings}) != len(self.bindings):
            raise ValueError("A recipient endpoint must have distinct account slots")
        if len({(binding.provider, binding.tool_id) for binding in self.bindings}) != len(self.bindings):
            raise ValueError("Duplicate provider/tool bindings are not allowed")
        if (type(self.intervals) is not tuple
                or any(type(interval) is not WeeklyInterval for interval in self.intervals)):
            raise ValueError("Intervals must be an immutable tuple")
        if type(self.closed_dates) is not tuple:
            raise ValueError("Closures must be an immutable tuple of ISO dates")
        for closure in self.closed_dates:
            if not isinstance(closure, str) or date.fromisoformat(closure).isoformat() != closure:
                raise ValueError("Closures must use exact YYYY-MM-DD dates")
        if (not _finite_nonnegative(self.maximum_clock_uncertainty_seconds)
                or self.maximum_clock_uncertainty_seconds > 60
                or not _finite_nonnegative(self.maximum_event_age_seconds)):
            raise ValueError("Finite clock limits are required; uncertainty support is at most 60 seconds")
        if (not isinstance(self.business_tone, str)
                or not re.fullmatch(r'[a-z0-9][a-z0-9_-]{0,63}', self.business_tone)):
            raise ValueError("Use a reviewed local tone identifier, not a URL or signaling header")

    def digest(self):
        serialized = json.dumps(asdict(self), sort_keys=True, separators=(',', ':'))
        return hashlib.sha256(serialized.encode()).hexdigest()

    def is_business_time(self, instant):
        local = instant.astimezone(ZoneInfo(self.timezone))
        if local.date().isoformat() in self.closed_dates:
            return False
        minute = (local.hour * 60 + local.minute + local.second / 60
                  + local.microsecond / 60000000)
        return any(interval.weekday == local.weekday()
                   and interval.opening_minute <= minute < interval.closing_minute
                   for interval in self.intervals)


@dataclass(frozen=True)
class EvaluationContext:
    event_id: str
    call_id: str
    recipient: RecipientScope
    binding: AccountBinding
    observed_at: str
    evaluated_at: str
    clock_uncertainty_seconds: float | None = None
    ingress_verified: bool = False
    ingress_policy_bound: bool = False


@dataclass(frozen=True)
class RingDecision:
    admission: str
    reason: str
    policy_sha256: str | None
    requested_local_tone: str | None = None
    auto_answer: bool = False


def evaluate_ring_policy(snapshot, context):
    """Calculate an allow/deny/unknown result, without performing an action.

    Affirmative ingress flags describe independently verified internal context;
    this function cannot authenticate those flags. Do not populate them from
    incoming caller JSON. Missing evidence cannot permit ringing.
    """
    if type(snapshot) is not RingPolicySnapshot:
        return RingDecision('unknown', 'policy-snapshot-missing', None)
    fingerprint = snapshot.digest()

    def result(admission, reason, tone=None):
        return RingDecision(admission, reason, fingerprint, tone)

    if type(context) is not EvaluationContext:
        return result('unknown', 'verified-context-missing')
    if not all(_nonempty_string(value) for value in (context.event_id, context.call_id)):
        return result('deny', 'invalid-event-identity')
    if (type(context.recipient) is not RecipientScope
            or context.recipient != snapshot.recipient):
        return result('deny', 'recipient-or-epoch-mismatch')
    if type(context.binding) is not AccountBinding or context.binding not in snapshot.bindings:
        return result('deny', 'unassigned-provider-tool-slot')
    if context.ingress_verified is not True or context.ingress_policy_bound is not True:
        return result('unknown', 'ingress-unverified-or-bypass-unclosed')
    try:
        observed = _utc(context.observed_at)
        evaluated = _utc(context.evaluated_at)
    except (ValueError, TypeError, OverflowError):
        return result('unknown', 'invalid-clock')
    uncertainty = context.clock_uncertainty_seconds
    if (not _finite_nonnegative(uncertainty)
            or uncertainty > snapshot.maximum_clock_uncertainty_seconds):
        return result('unknown', 'clock-uncertainty-exceeded')
    age = (evaluated - observed).total_seconds()
    if age < 0 or age > snapshot.maximum_event_age_seconds:
        return result('unknown', 'stale-or-future-event')
    try:
        earliest = evaluated - timedelta(seconds=uncertainty)
        latest = evaluated + timedelta(seconds=uncertainty)
        states = {snapshot.is_business_time(instant)
                  for instant in (earliest, evaluated, latest)}
        # Check intermediate UTC seconds too: multiple one-minute intervals
        # can hide a closed gap between permissive endpoint samples. Weekly
        # boundaries and timezone offsets resolve to whole seconds. The
        # snapshot's 60-second uncertainty ceiling bounds this work.
        cursor = earliest.replace(microsecond=0)
        while cursor < latest:
            cursor += timedelta(seconds=1)
            if cursor < latest:
                states.add(snapshot.is_business_time(cursor))
        if len(states) != 1:
            return result('unknown', 'clock-spans-policy-boundary')
    except (ValueError, OverflowError):
        return result('unknown', 'clock-out-of-range')
    if states == {True}:
        return result('allow', 'business-hours', snapshot.business_tone)
    return result('deny', 'after-hours-closed')
