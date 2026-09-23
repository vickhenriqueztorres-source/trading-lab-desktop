"""In-memory database simulator for license server unit tests without external Postgres."""

from __future__ import annotations

import copy
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4


class FakeCursor:
    """In-memory SQL cursor simulating psycopg operations for license server."""

    def __init__(self, db: FakeDb) -> None:
        self.db = db
        self._results: list[tuple[Any, ...]] = []
        self._index: int = 0

    def __enter__(self) -> FakeCursor:
        return self

    def __exit__(self, _exc_type: Any, _exc_val: Any, _exc_tb: Any) -> None:
        pass

    def execute(self, query: str, params: tuple[Any, ...] | None = None) -> None:
        q = " ".join(query.split()).lower()
        p = params or ()
        self._results = []
        self._index = 0

        # SELECT 1;
        if q == "select 1;" or q == "select 1":
            self._results = [(1,)]
            return

        # otp_challenges: INSERT
        if "insert into otp_challenges" in q:
            # params: (id, email, pkce_challenge, code_digest, code_plain, expires_at)
            cid, email, pkce_challenge, code_digest, code_plain, expires_at = p
            self.db.otp_challenges[str(cid)] = {
                "id": str(cid),
                "email": email,
                "pkce_challenge": pkce_challenge,
                "code_digest": code_digest,
                "code_plain": code_plain,
                "delivery_status": "pending",
                "attempts": 0,
                "expires_at": expires_at,
                "consumed": False,
                "created_at": datetime.now(UTC),
            }
            return

        # otp_challenges: UPDATE delivery_status
        if "update otp_challenges set delivery_status" in q:
            delivery_status = "sent" if "'sent'" in q else "failed"
            cid = str(p[0])
            if cid in self.db.otp_challenges:
                self.db.otp_challenges[cid]["delivery_status"] = delivery_status
            return

        # otp_challenges: SELECT by id
        if "from otp_challenges where id = %s" in q:
            cid = str(p[0])
            ch = self.db.otp_challenges.get(cid)
            if ch is not None:
                self._results = [
                    (
                        ch["email"],
                        ch["pkce_challenge"],
                        ch["code_digest"],
                        ch["attempts"],
                        ch["expires_at"],
                        ch["consumed"],
                    )
                ]
            return

        # otp_challenges: UPDATE consumed = true
        if "update otp_challenges set consumed = true where id = %s" in q:
            cid = str(p[0])
            if cid in self.db.otp_challenges:
                self.db.otp_challenges[cid]["consumed"] = True
            return

        # otp_challenges: UPDATE attempts, consumed
        if "update otp_challenges set attempts = %s, consumed = %s where id = %s" in q:
            attempts, is_consumed, cid = p
            cid_str = str(cid)
            if cid_str in self.db.otp_challenges:
                self.db.otp_challenges[cid_str]["attempts"] = attempts
                self.db.otp_challenges[cid_str]["consumed"] = is_consumed
            return

        # otp_challenges: SELECT code_plain for email
        if "select code_plain from otp_challenges where email = %s" in q:
            email = p[0]
            matches = [
                ch
                for ch in self.db.otp_challenges.values()
                if ch["email"] == email and ch["code_plain"] is not None
            ]
            matches.sort(key=lambda x: x["created_at"], reverse=True)
            if matches:
                self._results = [(matches[0]["code_plain"],)]
            return

        # customers: INSERT ON CONFLICT RETURNING id
        if "insert into customers" in q:
            if "on conflict" in q and len(p) >= 5:
                cid, email, name, country, notes = p[0], p[1], p[2], p[3], p[4]
                cid_str = str(cid)
                for existing in self.db.customers.values():
                    if existing["email"] == email:
                        if name is not None:
                            existing["name"] = name
                        if country is not None:
                            existing["country"] = country
                        if notes is not None:
                            existing["notes"] = notes
                        existing["updated_at"] = datetime.now(UTC)
                        self._results = [(UUID(existing["id"]),)]
                        return
                self.db.customers[cid_str] = {
                    "id": cid_str,
                    "email": email,
                    "name": name,
                    "country": country,
                    "notes": notes,
                    "created_at": datetime.now(UTC),
                    "updated_at": datetime.now(UTC),
                }
                self._results = [(UUID(cid_str),)]
                return
            else:
                email = p[0]
                for cust_id, cust in self.db.customers.items():
                    if cust["email"] == email:
                        self._results = [(UUID(cust_id),)]
                        return
                new_id = uuid4()
                self.db.customers[str(new_id)] = {
                    "id": str(new_id),
                    "email": email,
                    "name": None,
                    "country": None,
                    "notes": None,
                    "created_at": datetime.now(UTC),
                    "updated_at": datetime.now(UTC),
                }
                self._results = [(new_id,)]
                return

        # customers: SELECT queries
        if "from customers where id = %s" in q:
            cid = str(p[0])
            cust = self.db.customers.get(cid)
            if cust:
                self._results = [
                    (
                        UUID(cust["id"]),
                        cust["email"],
                        cust["name"],
                        cust["country"],
                        cust["notes"],
                        cust["created_at"],
                        cust["updated_at"],
                    )
                ]
            return

        if "select id, email from customers" in q:
            self._results = [(UUID(c["id"]), c["email"]) for c in self.db.customers.values()]
            return

        if "select id, email, name, country, notes, created_at, updated_at from customers" in q:
            self._results = [
                (
                    UUID(c["id"]),
                    c["email"],
                    c["name"],
                    c["country"],
                    c["notes"],
                    c["created_at"],
                    c["updated_at"],
                )
                for c in self.db.customers.values()
            ]
            return

        # licenses: SELECT active
        if "from licenses where customer_id = %s and status = 'active'" in q:
            cid = str(p[0])
            now = p[1] if len(p) > 1 else datetime.now(UTC)
            active_lics = [
                lic
                for lic in self.db.licenses
                if str(lic["customer_id"]) == cid
                and lic["status"] == "active"
                and lic["starts_at"] <= now
                and lic["expires_at"] > now
            ]
            active_lics.sort(key=lambda x: x["expires_at"], reverse=True)
            if active_lics:
                active_lic = active_lics[0]
                self._results = [
                    (
                        active_lic["id"],
                        active_lic["customer_id"],
                        active_lic["plan"],
                        active_lic["status"],
                        active_lic["broker_access"],
                        active_lic["strategy_packs"],
                        active_lic["real_mode_allowed"],
                        active_lic["max_devices"],
                        active_lic["starts_at"],
                        active_lic["expires_at"],
                        active_lic["created_at"],
                        active_lic["updated_at"],
                    )
                ]
            return

        # licenses: INSERT
        if "insert into licenses" in q:
            lid = p[0]
            cid = p[1]
            brokers = (
                p[2] if len(p) > 2 and isinstance(p[2], (list, tuple)) else ("DERIV", "IQ_OPTION")
            )
            packs = p[3] if len(p) > 3 and isinstance(p[3], (list, tuple)) else ("core",)
            real_mode = p[4] if len(p) > 4 and isinstance(p[4], bool) else False
            starts_at = p[-2] if len(p) >= 2 else datetime.now(UTC)
            expires_at = p[-1] if len(p) >= 1 else (datetime.now(UTC) + timedelta(days=30))
            self.db.licenses.append(
                {
                    "id": lid,
                    "customer_id": UUID(str(cid)),
                    "plan": "PRO",
                    "status": "active",
                    "broker_access": brokers,
                    "strategy_packs": packs,
                    "real_mode_allowed": real_mode,
                    "max_devices": 1,
                    "starts_at": starts_at,
                    "expires_at": expires_at,
                    "created_at": datetime.now(UTC),
                    "updated_at": datetime.now(UTC),
                }
            )
            return

        # licenses: SELECT queries
        if "from licenses where customer_id = %s order by expires_at desc limit 1" in q:
            cid = str(p[0])
            lics = [lic for lic in self.db.licenses if str(lic["customer_id"]) == cid]
            lics.sort(key=lambda x: x["expires_at"], reverse=True)
            if lics:
                if "real_mode_allowed" in q:
                    self._results = [(lics[0]["real_mode_allowed"],)]
                else:
                    self._results = [(lics[0]["id"], lics[0]["expires_at"])]
            return

        if "from licenses where customer_id = %s order by expires_at desc" in q:
            cid = str(p[0])
            lics = [lic for lic in self.db.licenses if str(lic["customer_id"]) == cid]
            lics.sort(key=lambda x: x["expires_at"], reverse=True)
            self._results = [
                (
                    lic["id"],
                    lic["customer_id"],
                    lic["plan"],
                    lic["status"],
                    lic["broker_access"],
                    lic["strategy_packs"],
                    lic["real_mode_allowed"],
                    lic["max_devices"],
                    lic["starts_at"],
                    lic["expires_at"],
                    lic["created_at"],
                    lic["updated_at"],
                )
                for lic in lics
            ]
            return

        if "from licenses" in q and "select id, customer_id" in q:
            self._results = [
                (
                    lic["id"],
                    lic["customer_id"],
                    lic["plan"],
                    lic["status"],
                    lic["expires_at"],
                    lic["real_mode_allowed"],
                    lic["max_devices"],
                    lic["starts_at"],
                )
                for lic in self.db.licenses
            ]
            return

        # licenses: UPDATE queries
        if "update licenses set expires_at = %s, status = 'active'" in q:
            new_exp, lic_id = p[0], p[1]
            for lic in self.db.licenses:
                if str(lic["id"]) == str(lic_id):
                    lic["expires_at"] = new_exp
                    lic["status"] = "active"
                    lic["updated_at"] = datetime.now(UTC)
            return

        if "update licenses set status =" in q:
            new_status = "suspended" if "'suspended'" in q else "active"
            cid = str(p[0])
            for lic in self.db.licenses:
                if str(lic["customer_id"]) == cid:
                    lic["status"] = new_status
                    lic["updated_at"] = datetime.now(UTC)
            return

        if "update licenses set real_mode_allowed = %s" in q:
            new_mode, cid = p[0], str(p[1])
            for lic in self.db.licenses:
                if str(lic["customer_id"]) == cid:
                    lic["real_mode_allowed"] = new_mode
                    lic["updated_at"] = datetime.now(UTC)
            return

        # audit_log: INSERT
        if "insert into audit_log" in q:
            action = "other"
            for act in (
                "login",
                "admin_login",
                "license_created",
                "license_renewed",
                "license_suspended",
                "license_reactivated",
                "license_real_mode_toggled",
                "device_released",
                "device_registered",
                "lease_issued",
            ):
                if f"'{act}'" in q:
                    action = act
                    break
            if action == "admin_login":
                cid = None
                actor = p[0]
                details = p[1] if len(p) > 1 else None
            elif len(p) == 3:
                cid, actor, details = p
            elif len(p) == 4:
                cid, actor, action, details = p
            else:
                cid, actor, details = p[0], p[1], p[-1]
            self.db.audit_logs.append(
                {
                    "customer_id": cid,
                    "actor": actor,
                    "action": action,
                    "details": details,
                    "created_at": datetime.now(UTC),
                }
            )
            return

        # api_tokens: INSERT
        if "insert into api_tokens" in q:
            kind = "access" if "'access'" in q else "refresh"
            if len(p) == 4:
                token_digest, cid, fid, expires_at = p
            elif len(p) == 5:
                token_digest, kind, cid, fid, expires_at = p
            else:
                token_digest, cid, fid, expires_at = p[0], p[1], p[2], p[3]
            self.db.api_tokens[token_digest] = {
                "token_digest": token_digest,
                "kind": kind,
                "customer_id": cid,
                "family_id": fid,
                "expires_at": expires_at,
                "used": False,
                "revoked": False,
                "created_at": datetime.now(UTC),
            }
            return

        # api_tokens: SELECT refresh
        if "from api_tokens where token_digest = %s and kind = 'refresh'" in q:
            digest = p[0]
            tok = self.db.api_tokens.get(digest)
            if tok is not None and tok["kind"] == "refresh":
                self._results = [
                    (
                        tok["customer_id"],
                        tok["family_id"],
                        tok["expires_at"],
                        tok["used"],
                        tok["revoked"],
                    )
                ]
            return

        # api_tokens: UPDATE revoke family
        if "update api_tokens set revoked = true where family_id = %s" in q:
            fid = p[0]
            for tok in self.db.api_tokens.values():
                if tok["family_id"] == fid:
                    tok["revoked"] = True
            return

        # api_tokens: UPDATE used = true
        if "update api_tokens set used = true where token_digest = %s" in q:
            digest = p[0]
            if digest in self.db.api_tokens:
                self.db.api_tokens[digest]["used"] = True
            return

        # api_tokens: SELECT access
        if "from api_tokens where token_digest = %s and kind = 'access'" in q:
            digest = p[0]
            tok = self.db.api_tokens.get(digest)
            if tok is not None and tok["kind"] == "access":
                self._results = [
                    (
                        tok["customer_id"],
                        tok["expires_at"],
                        tok["revoked"],
                    )
                ]
            return

        # devices: SELECT queries by device_id
        if "from devices where device_id = %s" in q:
            dev = self.db.devices.get(str(p[0]))
            if dev:
                if "customer_id" in q and "public_key_b64" in q:
                    self._results = [(dev["customer_id"], dev["public_key_b64"], dev["revoked"])]
                elif "public_key_b64" in q:
                    self._results = [(dev["public_key_b64"], dev["revoked"])]
                elif "customer_id" in q:
                    self._results = [(dev["customer_id"], dev["revoked"])]
            return

        # devices: UPDATE last_seen_at

        if "update devices set last_seen_at = now() where device_id = %s" in q:
            dev = self.db.devices.get(str(p[0]))
            if dev:
                dev["last_seen_at"] = datetime.now(UTC)
            return

        # devices: SELECT count
        if "from devices where customer_id = %s and revoked = false" in q:
            cid = UUID(str(p[0]))
            cnt = sum(
                1 for d in self.db.devices.values() if d["customer_id"] == cid and not d["revoked"]
            )
            self._results = [(cnt,)]
            return

        # devices: INSERT
        if "insert into devices" in q:
            dev_id, cid, pub_b64 = p[0], p[1], p[2]
            self.db.devices[str(dev_id)] = {
                "device_id": str(dev_id),
                "customer_id": UUID(str(cid)),
                "public_key_b64": pub_b64,
                "revoked": False,
                "created_at": datetime.now(UTC),
                "last_seen_at": datetime.now(UTC),
            }
            return

        # device_challenges: INSERT
        if "insert into device_challenges" in q:
            ch_id, cid, dev_id, nonce_b64, exp = p[0], p[1], p[2], p[3], p[4]
            self.db.device_challenges[str(ch_id)] = {
                "id": str(ch_id),
                "customer_id": UUID(str(cid)),
                "device_id": str(dev_id),
                "nonce_b64": nonce_b64,
                "expires_at": exp,
                "consumed": False,
                "created_at": datetime.now(UTC),
            }
            return

        # device_challenges: SELECT by id
        if "from device_challenges where id = %s" in q:
            ch = self.db.device_challenges.get(str(p[0]))
            if ch:
                self._results = [
                    (
                        ch["customer_id"],
                        ch["device_id"],
                        ch["nonce_b64"],
                        ch["expires_at"],
                        ch["consumed"],
                    )
                ]
            return

        # device_challenges: UPDATE consumed = true
        if "update device_challenges set consumed = true where id = %s" in q:
            ch = self.db.device_challenges.get(str(p[0]))
            if ch:
                ch["consumed"] = True
            return

        # leases: INSERT
        if "insert into leases" in q:
            lid, cid, dev_id, iss, exp = p[0], p[1], p[2], p[3], p[4]
            self.db.leases[str(lid)] = {
                "lease_id": str(lid),
                "customer_id": UUID(str(cid)),
                "device_id": str(dev_id),
                "issued_at": iss,
                "expires_at": exp,
                "revoked": False,
                "created_at": datetime.now(UTC),
            }
            return

        # leases: SELECT revoked
        if "from leases where lease_id = %s" in q:
            lease_row = self.db.leases.get(str(p[0]))
            if lease_row:
                self._results = [(lease_row["revoked"],)]
            return

        # devices: SELECT for customer detail
        if "from devices where customer_id = %s order by created_at desc" in q:
            cid = str(p[0])
            devs = [d for d in self.db.devices.values() if str(d["customer_id"]) == cid]
            devs.sort(key=lambda x: x["created_at"], reverse=True)
            self._results = [
                (
                    d["device_id"],
                    d["customer_id"],
                    d["public_key_b64"],
                    d.get("label"),
                    d["revoked"],
                    d["created_at"],
                    d["last_seen_at"],
                )
                for d in devs
            ]
            return

        # devices: SELECT count grouped by customer
        if (
            "select customer_id, count(*) from devices where revoked = false group by customer_id"
            in q
        ):
            counts: dict[UUID, int] = {}
            for d in self.db.devices.values():
                if not d["revoked"]:
                    cid_val = d["customer_id"]
                    counts[cid_val] = counts.get(cid_val, 0) + 1
            self._results = [(cid_val, cnt) for cid_val, cnt in counts.items()]
            return

        # devices: UPDATE release device
        if "update devices set revoked = true where device_id = %s and customer_id = %s" in q:
            dev_id, cid = str(p[0]), str(p[1])
            dev = self.db.devices.get(dev_id)
            if dev and str(dev["customer_id"]) == cid:
                dev["revoked"] = True
            return

        # leases: UPDATE revoke leases for customer
        if "update leases set revoked = true where customer_id = %s" in q:
            cid = str(p[0])
            for lease_rec in self.db.leases.values():
                if str(lease_rec["customer_id"]) == cid:
                    lease_rec["revoked"] = True
            return

        # leases: UPDATE revoke leases for device
        if "update leases set revoked = true where device_id = %s and customer_id = %s" in q:
            dev_id, cid = str(p[0]), str(p[1])
            for lease_rec in self.db.leases.values():
                if str(lease_rec["customer_id"]) == cid and str(lease_rec["device_id"]) == dev_id:
                    lease_rec["revoked"] = True
            return

        # api_tokens: UPDATE revoke for customer
        if "update api_tokens set revoked = true where customer_id = %s" in q:
            cid = str(p[0])
            for tok in self.db.api_tokens.values():
                if str(tok["customer_id"]) == cid:
                    tok["revoked"] = True
            return

        # audit_log: SELECT last seen grouped by customer
        if "select customer_id, max(created_at) from audit_log group by customer_id" in q:
            max_map: dict[UUID, datetime] = {}
            for a in self.db.audit_logs:
                cid_opt = a.get("customer_id")
                if cid_opt:
                    try:
                        cid_uuid = UUID(str(cid_opt))
                        if cid_uuid not in max_map or a["created_at"] > max_map[cid_uuid]:
                            max_map[cid_uuid] = a["created_at"]
                    except (ValueError, TypeError):
                        pass
            self._results = [(cid_k, t) for cid_k, t in max_map.items()]
            return

        # audit_log: SELECT for customer detail (last 50)
        if "from audit_log where customer_id = %s order by created_at desc limit 50" in q:
            cid = str(p[0])
            matching = [a for a in self.db.audit_logs if str(a.get("customer_id")) == cid]
            matching.sort(key=lambda x: x["created_at"], reverse=True)
            self._results = [
                (
                    i + 1,
                    a["customer_id"],
                    a["actor"],
                    a["action"],
                    a.get("details"),
                    a["created_at"],
                )
                for i, a in enumerate(matching[:50])
            ]
            return

        # audit_log: SELECT global (last 200)
        if "from audit_log order by created_at desc limit 200" in q:
            all_audits = sorted(self.db.audit_logs, key=lambda x: x["created_at"], reverse=True)
            self._results = [
                (
                    i + 1,
                    a.get("customer_id"),
                    a["actor"],
                    a["action"],
                    a.get("details"),
                    a["created_at"],
                )
                for i, a in enumerate(all_audits[:200])
            ]
            return

        # otp_challenges: SELECT last for email
        if "from otp_challenges where email = %s order by created_at desc limit 1" in q:
            email = p[0]
            matches = [ch for ch in self.db.otp_challenges.values() if ch["email"] == email]
            matches.sort(key=lambda x: x["created_at"], reverse=True)
            if matches:
                self._results = [(matches[0]["created_at"], matches[0]["delivery_status"])]
            return

        # purge expired (cleanup)
        if "delete from" in q:
            return

    def fetchone(self) -> tuple[Any, ...] | None:
        if self._index < len(self._results):
            row = self._results[self._index]
            self._index += 1
            return row
        return None

    def fetchall(self) -> list[tuple[Any, ...]]:
        return list(self._results)


class FakeConnection:
    """Connection wrapping FakeDb."""

    def __init__(self, db: FakeDb) -> None:
        self.db = db
        self.is_committed = False

    def __enter__(self) -> FakeConnection:
        return self

    def __exit__(self, exc_type: Any, _exc_val: Any, _exc_tb: Any) -> None:
        if exc_type is not None:
            self.db.rollback()

    def cursor(self) -> FakeCursor:
        return FakeCursor(self.db)

    def commit(self) -> None:
        self.is_committed = True
        self.db.savepoint()

    def rollback(self) -> None:
        self.db.rollback()


class FakeDb:
    """Stateful in-memory database store."""

    def __init__(self) -> None:
        self.otp_challenges: dict[str, dict[str, Any]] = {}
        self.customers: dict[str, dict[str, Any]] = {}
        self.licenses: list[dict[str, Any]] = []
        self.api_tokens: dict[str, dict[str, Any]] = {}
        self.audit_logs: list[dict[str, Any]] = []
        self.devices: dict[str, dict[str, Any]] = {}
        self.device_challenges: dict[str, dict[str, Any]] = {}
        self.leases: dict[str, dict[str, Any]] = {}

        self._snapshots: list[dict[str, Any]] = []
        self.savepoint()

    def savepoint(self) -> None:
        self._snapshots.append(
            {
                "otp_challenges": copy.deepcopy(self.otp_challenges),
                "customers": copy.deepcopy(self.customers),
                "licenses": copy.deepcopy(self.licenses),
                "api_tokens": copy.deepcopy(self.api_tokens),
                "audit_logs": copy.deepcopy(self.audit_logs),
                "devices": copy.deepcopy(self.devices),
                "device_challenges": copy.deepcopy(self.device_challenges),
                "leases": copy.deepcopy(self.leases),
            }
        )

    def rollback(self) -> None:
        if self._snapshots:
            snap = self._snapshots[-1]
            self.otp_challenges = copy.deepcopy(snap["otp_challenges"])
            self.customers = copy.deepcopy(snap["customers"])
            self.licenses = copy.deepcopy(snap["licenses"])
            self.api_tokens = copy.deepcopy(snap["api_tokens"])
            self.audit_logs = copy.deepcopy(snap["audit_logs"])
            self.devices = copy.deepcopy(snap.get("devices", {}))
            self.device_challenges = copy.deepcopy(snap.get("device_challenges", {}))
            self.leases = copy.deepcopy(snap.get("leases", {}))

    def add_active_license(
        self,
        customer_id: UUID | str,
        *,
        plan: str = "PRO",
        real_mode_allowed: bool = False,
        broker_access: tuple[str, ...] | list[str] = ("DERIV", "IQ_OPTION"),
        strategy_packs: tuple[str, ...] | list[str] = ("core", "deriv-digits", "iqoption-rsi"),
        max_devices: int = 1,
        starts_at: datetime | None = None,
        expires_at: datetime | None = None,
    ) -> dict[str, Any]:
        cid = UUID(str(customer_id))
        now = datetime.now(UTC)
        lic = {
            "id": uuid4(),
            "customer_id": cid,
            "plan": plan,
            "status": "active",
            "broker_access": tuple(broker_access),
            "strategy_packs": tuple(strategy_packs),
            "real_mode_allowed": real_mode_allowed,
            "max_devices": max_devices,
            "starts_at": starts_at or now,
            "expires_at": expires_at or (now + timedelta(days=30)),
            "created_at": now,
            "updated_at": now,
        }
        self.licenses.append(lic)
        self.savepoint()
        return lic
