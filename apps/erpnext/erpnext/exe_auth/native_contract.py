"""Private in-process contract. Not a public endpoint or a dispatch decoder.

The trusted Core parent must supply its original successful first-start result.
No file, HTTP request, historical action lookup or reconstructed tuple is accepted
as a substitute. The separately reviewed parent owns that handoff capability.
"""

import re
import time
from datetime import datetime

UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\Z")
HASH = re.compile(r"[0-9a-f]{64}\Z")
INPUT = ("job_id", "lease_token", "attempt", "worker_id", "company_id", "deployment_id", "product", "profile_sha256", "config_sha256", "initializer_sha256", "request_key", "intent_id", "action_id")
BOUND = tuple(k for k in INPUT if k != "lease_token")
OUTPUT = (*BOUND, "owner_subject", "sql_time", "lease_expires_at")
START = ("intent_id", "action_id", "started_attempt", "started_by", "started_at")


class Refused(Exception):
    """Fixed private refusal; upstream errors/credentials must not be printed."""


def exact(value, keys):
    if type(value) is not dict or set(value) != set(keys):
        raise Refused("invalid_private_contract")


def stamp(value):
    if type(value) is not str or not 1 <= len(value) <= 40:
        raise Refused("invalid_sql_time")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.utcoffset() is None:
            raise ValueError()
        return parsed.timestamp()
    except (ValueError, OverflowError):
        raise Refused("invalid_sql_time") from None


def validate_tuple(value):
    exact(value, INPUT)
    for key in ("job_id", "lease_token", "company_id", "deployment_id", "request_key", "intent_id", "action_id"):
        if type(value[key]) is not str or not UUID.fullmatch(value[key]):
            raise Refused("invalid_tuple")
    for key in ("profile_sha256", "config_sha256", "initializer_sha256"):
        if type(value[key]) is not str or not HASH.fullmatch(value[key]):
            raise Refused("invalid_tuple")
    if value["product"] != "erp-site" or type(value["attempt"]) is not int or not 1 <= value["attempt"] <= 2147483647 or type(value["worker_id"]) is not str or not re.fullmatch(r"[a-zA-Z0-9._:-]{1,128}", value["worker_id"]):
        raise Refused("invalid_tuple")
    return dict(value)


class CurrentOwner:
    """Every actual read shortens the ORIGINAL monotonic admitted deadline.

    read is the private parent's actual parameterized Core function invocation.
    Core itself checks its effective runtime privileges; no weaker duplicate SQL
    guard or caller-supplied owner projection authorizes native work here.
    """
    def __init__(self, value, read, original_end, cleanup_seconds=30, clock=time.monotonic, deadline_bound=None):
        self.value = validate_tuple(value)
        if not callable(read) or type(original_end) not in (int, float) or not 0 < cleanup_seconds <= 30:
            raise Refused("invalid_private_contract")
        self.read, self.clock = read, clock
        now = clock()
        if not now < original_end <= now + 300:
            raise Refused("invalid_deadline")
        self.end, self.cleanup_seconds, self.owner = original_end, cleanup_seconds, None
        if deadline_bound is not None and not callable(deadline_bound):
            raise Refused("invalid_private_deadline")
        self.deadline_bound = deadline_bound

    def live(self, required_seconds=0):
        if self.deadline_bound is not None:
            bound = self.deadline_bound()
            if type(bound) not in (int, float):
                raise Refused("invalid_private_deadline")
            self.end = min(self.end, bound)
        if self.clock() + self.cleanup_seconds + required_seconds >= self.end:
            raise Refused("insufficient_lease")

    def observe(self, required_seconds=0):
        self.live(required_seconds)
        before = self.clock()
        row = self.read(dict(self.value))
        after = self.clock()
        exact(row, OUTPUT)
        if any(row[k] != self.value[k] for k in BOUND) or type(row["owner_subject"]) is not str or not UUID.fullmatch(row["owner_subject"]):
            raise Refused("changed_action")
        left = stamp(row["lease_expires_at"]) - stamp(row["sql_time"]) - (after - before) - .001
        if not 0 < left <= 300 or after < before:
            raise Refused("expired_action")
        self.end = min(self.end, after + left)
        if self.owner is not None and self.owner != row["owner_subject"]:
            raise Refused("changed_creator")
        self.owner = row["owner_subject"]
        self.live(required_seconds)
        return dict(row)


def successful_start(start, value):
    """Structural consistency only, NOT dispatch provenance/authentication.

    Called solely after the parent consumes its original first-writer capability.
    Deliberately no JSON decoder, retry lookup or standalone CLI is exported.
    """
    exact(start, START)
    if start["intent_id"] != value["intent_id"] or start["action_id"] != value["action_id"] or start["started_attempt"] != value["attempt"] or start["started_by"] != value["worker_id"]:
        raise Refused("changed_start")
    stamp(start["started_at"])
