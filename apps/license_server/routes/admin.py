"""Admin panel routes (server-rendered via Jinja2) for Trading Lab License Server."""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import secrets
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Annotated, Any
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from apps.license_server.admin_auth import sign_csrf, sign_session, verify_csrf
from apps.license_server.db import with_conn
from apps.license_server.dependencies import require_admin
from apps.license_server.entitlements import PRO_BROKERS, PRO_STRATEGY_PACKS
from apps.license_server.mail import get_mail_provider
from apps.license_server.settings import Settings, get_settings
from packages.identity import normalize_email

logger = logging.getLogger("license_server.admin")

router = APIRouter(prefix="/admin", tags=["admin"])

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


def _format_dt(dt: datetime | None) -> str:
    if dt is None:
        return ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC).strftime("%d/%m/%Y %H:%M")


def _dump_json(val: Any) -> str:
    if val is None:
        return ""
    if isinstance(val, str):
        return val
    try:
        return json.dumps(val, ensure_ascii=False)
    except Exception:
        return str(val)


def _settings(request: Request) -> Settings:
    return getattr(request.app.state, "settings", None) or get_settings()


# ---------------------------------------------------------------------------
# Admin Authentication Routes
# ---------------------------------------------------------------------------


@router.get("/login", response_class=HTMLResponse)
def admin_login_page(
    request: Request,
    challenge_id: str | None = Query(None),
) -> Any:
    """Render admin login page (request email or input OTP)."""
    settings = _settings(request)
    session_cookie = request.cookies.get("admin_session")
    if session_cookie:
        from apps.license_server.admin_auth import verify_session

        valid_email = verify_session(session_cookie, settings.admin_session_secret)
        if valid_email and valid_email == settings.admin_email.lower():
            return RedirectResponse(url="/admin", status_code=status.HTTP_303_SEE_OTHER)

    csrf_token = sign_csrf(settings.admin_session_secret)
    dev_code: str | None = None
    is_dummy_challenge: bool = False
    if settings.environment == "development" and challenge_id:
        try:
            with with_conn() as conn, conn.cursor() as cur:
                cur.execute(
                    "SELECT code_plain FROM otp_challenges WHERE id = %s;",
                    (challenge_id,),
                )
                row = cur.fetchone()
                if row and row[0]:
                    dev_code = str(row[0])
                else:
                    is_dummy_challenge = True
        except Exception:
            pass

    return templates.TemplateResponse(
        request=request,
        name="admin_login.html",
        context={
            "challenge_id": challenge_id,
            "csrf_token": csrf_token,
            "error": None,
            "dev_code": dev_code,
            "is_dummy_challenge": is_dummy_challenge,
            "admin_email": settings.admin_email,
        },
    )


@router.post("/login", response_class=HTMLResponse)
def admin_request_otp(
    request: Request,
    email: Annotated[str, Form()],
    csrf_token: Annotated[str, Form()],
) -> Any:
    """Start admin OTP login process."""
    settings = _settings(request)
    if not verify_csrf(csrf_token, settings.admin_session_secret):
        return templates.TemplateResponse(
            request=request,
            name="admin_login.html",
            context={
                "challenge_id": None,
                "csrf_token": sign_csrf(settings.admin_session_secret),
                "error": "Sessão expirada. Por favor, tente novamente.",
            },
            status_code=400,
        )

    norm_email = normalize_email(email)
    if norm_email != settings.admin_email.lower():
        # Do not send email; display OTP input with a dummy ID to prevent enumeration
        dummy_id = str(uuid4())
        return RedirectResponse(
            url=f"/admin/login?challenge_id={dummy_id}",
            status_code=status.HTTP_303_SEE_OTHER,
        )

    # Generate 6-digit OTP
    code = f"{secrets.randbelow(1_000_000):06d}"
    code_digest = hashlib.sha256(code.encode("ascii")).hexdigest()
    challenge_id = uuid4()
    now = datetime.now(UTC)
    expires_at = now + timedelta(minutes=5)
    code_plain = code if settings.mail_provider == "console" else None

    with with_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO otp_challenges (
                id, email, pkce_challenge, code_digest, code_plain,
                delivery_status, attempts, expires_at, consumed, created_at
            ) VALUES (
                %s, %s, %s, %s, %s, 'pending', 0, %s, false, now()
            );
            """,
            (challenge_id, norm_email, "admin_internal", code_digest, code_plain, expires_at),
        )
        conn.commit()

    try:
        mail_provider = get_mail_provider(settings)
        mail_provider.send_otp(norm_email, code, 5)
        with with_conn() as conn, conn.cursor() as cur:
            cur.execute(
                "UPDATE otp_challenges SET delivery_status = 'sent' WHERE id = %s;",
                (challenge_id,),
            )
            conn.commit()
    except Exception as exc:
        logger.error("Failed to deliver admin OTP email: %s", exc)
        with with_conn() as conn, conn.cursor() as cur:
            cur.execute(
                "UPDATE otp_challenges SET delivery_status = 'failed' WHERE id = %s;",
                (challenge_id,),
            )
            conn.commit()
        return templates.TemplateResponse(
            request=request,
            name="admin_login.html",
            context={
                "challenge_id": None,
                "csrf_token": sign_csrf(settings.admin_session_secret),
                "error": "Falha ao enviar e-mail com código. Tente novamente mais tarde.",
            },
            status_code=503,
        )

    return RedirectResponse(
        url=f"/admin/login?challenge_id={challenge_id}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.post("/login/verify", response_class=HTMLResponse)
def admin_verify_otp(
    request: Request,
    challenge_id: Annotated[str, Form()],
    otp_code: Annotated[str, Form()],
    csrf_token: Annotated[str, Form()],
) -> Any:
    """Verify admin OTP code and create authenticated session cookie."""
    settings = _settings(request)
    if not verify_csrf(csrf_token, settings.admin_session_secret):
        return templates.TemplateResponse(
            request=request,
            name="admin_login.html",
            context={
                "challenge_id": challenge_id,
                "csrf_token": sign_csrf(settings.admin_session_secret),
                "error": "Token de segurança inválido. Tente novamente.",
            },
            status_code=400,
        )

    try:
        ch_uuid = UUID(challenge_id)
    except ValueError:
        return templates.TemplateResponse(
            request=request,
            name="admin_login.html",
            context={
                "challenge_id": None,
                "csrf_token": sign_csrf(settings.admin_session_secret),
                "error": "Código de verificação inválido ou expirado.",
            },
            status_code=400,
        )

    with with_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT email, pkce_challenge, code_digest, attempts, expires_at, consumed
            FROM otp_challenges
            WHERE id = %s;
            """,
            (ch_uuid,),
        )
        row = cur.fetchone()
        if not row:
            return templates.TemplateResponse(
                request=request,
                name="admin_login.html",
                context={
                    "challenge_id": None,
                    "csrf_token": sign_csrf(settings.admin_session_secret),
                    "error": "Código de verificação inválido ou expirado.",
                },
                status_code=400,
            )

        email, _pkce, expected_digest, attempts, expires_at, consumed = row
        now = datetime.now(UTC)

        if consumed or (expires_at and expires_at <= now):
            return templates.TemplateResponse(
                request=request,
                name="admin_login.html",
                context={
                    "challenge_id": None,
                    "csrf_token": sign_csrf(settings.admin_session_secret),
                    "error": "Código expirado. Solicite um novo código.",
                },
                status_code=400,
            )

        if attempts >= 5:
            cur.execute(
                "UPDATE otp_challenges SET consumed = true WHERE id = %s;",
                (ch_uuid,),
            )
            conn.commit()
            return templates.TemplateResponse(
                request=request,
                name="admin_login.html",
                context={
                    "challenge_id": None,
                    "csrf_token": sign_csrf(settings.admin_session_secret),
                    "error": (
                        "Número excessivo de tentativas incorretas. Solicite um novo código."
                    ),
                },
                status_code=400,
            )

            supplied_digest = hashlib.sha256(otp_code.strip().encode("ascii")).hexdigest()
            if not hmac.compare_digest(supplied_digest, expected_digest):
                new_attempts = attempts + 1
                cur.execute(
                    "UPDATE otp_challenges SET attempts = %s, consumed = %s WHERE id = %s;",
                    (new_attempts, new_attempts >= 5, ch_uuid),
                )
                conn.commit()
                return templates.TemplateResponse(
                    request=request,
                    name="admin_login.html",
                    context={
                        "challenge_id": challenge_id,
                        "csrf_token": sign_csrf(settings.admin_session_secret),
                        "error": "Código incorreto. Verifique e tente novamente.",
                    },
                    status_code=401,
                )

            # Valid OTP: consume challenge and audit login
            cur.execute(
                "UPDATE otp_challenges SET consumed = true WHERE id = %s;",
                (ch_uuid,),
            )
            cur.execute(
                """
                INSERT INTO audit_log (customer_id, actor, action, details, created_at)
                VALUES (NULL, %s, 'admin_login', %s, now());
                """,
                (
                    settings.admin_email,
                    json.dumps({"ip": request.client.host if request.client else "unknown"}),
                ),
            )
            conn.commit()

    session_token = sign_session(settings.admin_email, settings.admin_session_secret)
    response = RedirectResponse(url="/admin", status_code=status.HTTP_303_SEE_OTHER)
    is_prod = settings.environment in {"production", "prod"}
    response.set_cookie(
        key="admin_session",
        value=session_token,
        max_age=43200,
        httponly=True,
        secure=is_prod,
        samesite="lax",
    )
    return response


@router.post("/logout")
def admin_logout(
    request: Request,
    csrf_token: Annotated[str, Form()],
) -> Any:
    """Clear admin session cookie and redirect to login."""
    settings = _settings(request)
    if not verify_csrf(csrf_token, settings.admin_session_secret):
        raise HTTPException(status_code=400, detail="CSRF inválido")
    response = RedirectResponse(url="/admin/login", status_code=status.HTTP_303_SEE_OTHER)
    response.delete_cookie(key="admin_session")
    return response


# ---------------------------------------------------------------------------
# Admin Management Pages & Actions
# ---------------------------------------------------------------------------


@router.get("", response_class=HTMLResponse)
def admin_dashboard(
    request: Request,
    admin_email: Annotated[str, Depends(require_admin)],
    q: str | None = Query(None),
) -> Any:
    """Admin dashboard with counters, search, and customer list."""
    settings = _settings(request)
    now = datetime.now(UTC)
    now_plus_7d = now + timedelta(days=7)

    with with_conn() as conn, conn.cursor() as cur:
        # 1. Fetch all customers
        cur.execute(
            "SELECT id, email, name, country, notes, created_at, updated_at FROM customers;"
        )
        customers_raw = cur.fetchall()

        # 2. Fetch all licenses
        cur.execute(
            """
            SELECT id, customer_id, plan, status, expires_at,
                   real_mode_allowed, max_devices, starts_at
            FROM licenses;
            """
        )
        licenses_raw = cur.fetchall()

        # 3. Fetch device counts per customer
        cur.execute(
            """
            SELECT customer_id, count(*)
            FROM devices
            WHERE revoked = false
            GROUP BY customer_id;
            """
        )
        devices_raw = cur.fetchall()

        # 4. Fetch last seen per customer from audit_log
        cur.execute("SELECT customer_id, max(created_at) FROM audit_log GROUP BY customer_id;")
        last_seen_raw = cur.fetchall()

    # Map latest license per customer
    latest_licenses: dict[str, Any] = {}
    for lic in licenses_raw:
        cid = str(lic[1])
        existing = latest_licenses.get(cid)
        if not existing or lic[4] > existing[4]:
            latest_licenses[cid] = lic

    # Compute counters across all licenses
    active_count = 0
    expiring_soon_count = 0
    expired_count = 0

    for lic in latest_licenses.values():
        status_val = lic[3]
        expires_at = lic[4]
        starts_at = lic[7] if len(lic) > 7 else now
        if status_val == "active" and starts_at <= now < expires_at:
            active_count += 1
            if expires_at <= now_plus_7d:
                expiring_soon_count += 1
        else:
            expired_count += 1

    # Map device counts
    active_device_counts: dict[str, int] = {str(r[0]): int(r[1]) for r in devices_raw if r[0]}
    last_seen_map: dict[str, datetime] = {str(r[0]): r[1] for r in last_seen_raw if r[0]}

    # Build customer view objects
    customer_list: list[dict[str, Any]] = []
    query_str = (q or "").strip().lower()

    for c in customers_raw:
        cid = str(c[0])
        email = str(c[1])
        name = c[2] or ""
        country = c[3] or ""

        if query_str and (query_str not in email.lower() and query_str not in name.lower()):
            continue

        lic = latest_licenses.get(cid)
        plan = lic[2] if lic else "Nenhum"
        status_val = lic[3] if lic else "Sem licença"
        expires_at = lic[4] if lic else None
        real_mode = bool(lic[5]) if lic else False
        max_dev = int(lic[6]) if lic else 1
        act_dev = active_device_counts.get(cid, 0)
        last_seen = last_seen_map.get(cid)

        days_left: int | None = None
        if expires_at:
            delta = (expires_at - now).total_seconds()
            days_left = int(delta // 86400)

        customer_list.append(
            {
                "id": cid,
                "email": email,
                "name": name,
                "country": country,
                "plan": plan,
                "status": status_val,
                "real_mode_allowed": real_mode,
                "expires_at": expires_at,
                "expires_at_formatted": _format_dt(expires_at),
                "expires_in_days": days_left,
                "active_devices": act_dev,
                "max_devices": max_dev,
                "last_seen_formatted": _format_dt(last_seen),
            }
        )

    customer_list.sort(key=lambda x: x["email"])

    csrf_token = sign_csrf(settings.admin_session_secret)
    return templates.TemplateResponse(
        request=request,
        name="admin_dashboard.html",
        context={
            "admin_email": admin_email,
            "csrf_token": csrf_token,
            "active_count": active_count,
            "expiring_soon_count": expiring_soon_count,
            "expired_count": expired_count,
            "customers": customer_list,
            "query": q or "",
        },
    )


@router.get("/customers/new", response_class=HTMLResponse)
def admin_new_customer_page(
    request: Request,
    admin_email: Annotated[str, Depends(require_admin)],
) -> Any:
    """Render customer creation form."""
    settings = _settings(request)
    csrf_token = sign_csrf(settings.admin_session_secret)
    return templates.TemplateResponse(
        request=request,
        name="admin_customer_new.html",
        context={
            "admin_email": admin_email,
            "csrf_token": csrf_token,
            "error": None,
        },
    )


@router.post("/customers/new")
def admin_create_customer(
    request: Request,
    admin_email: Annotated[str, Depends(require_admin)],
    email: Annotated[str, Form()],
    csrf_token: Annotated[str, Form()],
    name: Annotated[str | None, Form()] = None,
    country: Annotated[str | None, Form()] = None,
    days: Annotated[int, Form()] = 30,
    real_mode_allowed: Annotated[bool, Form()] = False,
    notes: Annotated[str | None, Form()] = None,
) -> Any:
    """Create customer and initial PRO license."""
    settings = _settings(request)
    if not verify_csrf(csrf_token, settings.admin_session_secret):
        raise HTTPException(status_code=400, detail="CSRF inválido")

    norm_email = normalize_email(email)
    customer_id = uuid4()
    license_id = uuid4()
    now = datetime.now(UTC)
    expires_at = now + timedelta(days=max(1, days))

    with with_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
                INSERT INTO customers (id, email, name, country, notes, created_at, updated_at)
                VALUES (%s, %s, %s, %s, %s, now(), now())
                ON CONFLICT (email) DO UPDATE
                SET name = COALESCE(EXCLUDED.name, customers.name),
                    country = COALESCE(EXCLUDED.country, customers.country),
                    notes = COALESCE(EXCLUDED.notes, customers.notes),
                    updated_at = now()
                RETURNING id;
                """,
            (customer_id, norm_email, name, country, notes),
        )
        row = cur.fetchone()
        if row:
            customer_id = row[0]

        cur.execute(
            """
                INSERT INTO licenses (
                    id, customer_id, plan, status, broker_access, strategy_packs,
                    real_mode_allowed, max_devices, starts_at, expires_at, created_at, updated_at
                ) VALUES (
                    %s, %s, 'PRO', 'active', %s, %s, %s, 1, %s, %s, now(), now()
                );
                """,
            (
                license_id,
                customer_id,
                list(PRO_BROKERS),
                list(PRO_STRATEGY_PACKS),
                real_mode_allowed,
                now,
                expires_at,
            ),
        )

        cur.execute(
            """
                INSERT INTO audit_log (customer_id, actor, action, details, created_at)
                VALUES (%s, %s, 'license_created', %s, now());
                """,
            (
                customer_id,
                admin_email,
                json.dumps(
                    {
                        "plan": "PRO",
                        "days": days,
                        "real_mode_allowed": real_mode_allowed,
                        "notes": notes,
                    }
                ),
            ),
        )
        conn.commit()

    return RedirectResponse(
        url=f"/admin/customers/{customer_id}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.get("/customers/{customer_id}", response_class=HTMLResponse)
def admin_customer_detail(
    request: Request,
    customer_id: UUID,
    admin_email: Annotated[str, Depends(require_admin)],
) -> Any:
    """Render customer detail card, license details, devices, and audit log."""
    settings = _settings(request)
    now = datetime.now(UTC)

    with with_conn() as conn, conn.cursor() as cur:
        # 1. Customer record
        cur.execute(
            """
            SELECT id, email, name, country, notes, created_at, updated_at
            FROM customers WHERE id = %s;
            """,
            (customer_id,),
        )
        cust_row = cur.fetchone()
        if not cust_row:
            raise HTTPException(status_code=404, detail="Cliente não encontrado")

        # 2. Licenses
        cur.execute(
            """
            SELECT id, customer_id, plan, status, broker_access, strategy_packs,
                   real_mode_allowed, max_devices, starts_at, expires_at, created_at, updated_at
            FROM licenses
            WHERE customer_id = %s
            ORDER BY expires_at DESC;
            """,
            (customer_id,),
        )
        lic_rows = cur.fetchall()

        # 3. Devices
        cur.execute(
            """
            SELECT device_id, customer_id, public_key_b64, label,
                   revoked, created_at, last_seen_at
            FROM devices
            WHERE customer_id = %s
            ORDER BY created_at DESC;
            """,
            (customer_id,),
        )
        dev_rows = cur.fetchall()

        # 4. Audit logs (last 50)
        cur.execute(
            """
            SELECT id, customer_id, actor, action, details, created_at
            FROM audit_log
            WHERE customer_id = %s
            ORDER BY created_at DESC
            LIMIT 50;
            """,
            (customer_id,),
        )
        audit_rows = cur.fetchall()

        # 5. Last OTP challenge
        cur.execute(
            """
            SELECT created_at, delivery_status
            FROM otp_challenges
            WHERE email = %s
            ORDER BY created_at DESC
            LIMIT 1;
            """,
            (cust_row[1],),
        )
        otp_row = cur.fetchone()

    # Process customer
    customer_dict = {
        "id": str(cust_row[0]),
        "email": cust_row[1],
        "name": cust_row[2],
        "country": cust_row[3],
        "notes": cust_row[4],
        "created_at_formatted": _format_dt(cust_row[5]),
    }

    # Process active/latest license
    license_dict = None
    if lic_rows:
        latest = lic_rows[0]
        exp = latest[9]
        days_left = int((exp - now).total_seconds() // 86400) if exp else 0
        license_dict = {
            "id": str(latest[0]),
            "plan": latest[2],
            "status": latest[3],
            "broker_access": list(latest[4]),
            "strategy_packs": list(latest[5]),
            "real_mode_allowed": bool(latest[6]),
            "max_devices": int(latest[7]),
            "starts_at_formatted": _format_dt(latest[8]),
            "expires_at_formatted": _format_dt(exp),
            "days_left": days_left,
            "is_expired": (exp <= now) if exp else True,
        }

    # Process devices
    devices_list = [
        {
            "device_id": str(d[0]),
            "public_key_b64": d[2],
            "label": d[3],
            "revoked": bool(d[4]),
            "created_at_formatted": _format_dt(d[5]),
            "last_seen_formatted": _format_dt(d[6]),
        }
        for d in dev_rows
    ]

    # Process audit logs
    audit_list = [
        {
            "id": a[0],
            "actor": a[2],
            "action": a[3],
            "details_json": _dump_json(a[4]),
            "created_at_formatted": _format_dt(a[5]),
        }
        for a in audit_rows
    ]

    # Process last OTP text
    if otp_row:
        otp_time = _format_dt(otp_row[0])
        otp_status = otp_row[1]
        last_otp_text = f"Último código enviado às {otp_time} (Status: {otp_status})"
    else:
        last_otp_text = "Nenhum código OTP solicitado até o momento."

    csrf_token = sign_csrf(settings.admin_session_secret)
    return templates.TemplateResponse(
        request=request,
        name="admin_customer_detail.html",
        context={
            "admin_email": admin_email,
            "csrf_token": csrf_token,
            "customer": customer_dict,
            "license": license_dict,
            "devices": devices_list,
            "audit_logs": audit_list,
            "last_otp_text": last_otp_text,
        },
    )


@router.post("/customers/{customer_id}/renew")
def admin_renew_license(
    request: Request,
    customer_id: UUID,
    csrf_token: Annotated[str, Form()],
    admin_email: Annotated[str, Depends(require_admin)],
) -> Any:
    """Add 30 days to license expiration: max(now, expires_at) + 30 days."""
    settings = _settings(request)
    if not verify_csrf(csrf_token, settings.admin_session_secret):
        raise HTTPException(status_code=400, detail="CSRF inválido")

    now = datetime.now(UTC)

    with with_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, expires_at
            FROM licenses
            WHERE customer_id = %s
            ORDER BY expires_at DESC
            LIMIT 1;
            """,
            (customer_id,),
        )
        lic_row = cur.fetchone()
        if not lic_row:
            # Create a new PRO license if none existed
            new_expires_at = now + timedelta(days=30)
            cur.execute(
                """
                INSERT INTO licenses (
                    id, customer_id, plan, status, broker_access, strategy_packs,
                    real_mode_allowed, max_devices, starts_at, expires_at,
                    created_at, updated_at
                ) VALUES (
                    %s, %s, 'PRO', 'active', %s, %s, false, 1, %s, %s, now(), now()
                );
                """,
                (
                    uuid4(),
                    customer_id,
                    list(PRO_BROKERS),
                    list(PRO_STRATEGY_PACKS),
                    now,
                    new_expires_at,
                ),
            )
        else:
            lic_id, old_expires_at = lic_row
            base_time = max(now, old_expires_at)
            new_expires_at = base_time + timedelta(days=30)
            cur.execute(
                """
                UPDATE licenses
                SET expires_at = %s, status = 'active', updated_at = now()
                WHERE id = %s;
                """,
                (new_expires_at, lic_id),
            )

        cur.execute(
            """
            INSERT INTO audit_log (customer_id, actor, action, details, created_at)
            VALUES (%s, %s, 'license_renewed', %s, now());
            """,
            (
                customer_id,
                admin_email,
                json.dumps({"new_expires_at": new_expires_at.isoformat()}),
            ),
        )
        conn.commit()

    return RedirectResponse(
        url=f"/admin/customers/{customer_id}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.post("/customers/{customer_id}/suspend")
def admin_suspend_customer(
    request: Request,
    customer_id: UUID,
    csrf_token: Annotated[str, Form()],
    admin_email: Annotated[str, Depends(require_admin)],
) -> Any:
    """Suspend license, revoke all api_tokens and revoke all leases."""
    settings = _settings(request)
    if not verify_csrf(csrf_token, settings.admin_session_secret):
        raise HTTPException(status_code=400, detail="CSRF inválido")

    with with_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE licenses SET status = 'suspended', updated_at = now() WHERE customer_id = %s;",
            (customer_id,),
        )
        cur.execute(
            "UPDATE api_tokens SET revoked = true WHERE customer_id = %s;",
            (customer_id,),
        )
        cur.execute(
            "UPDATE leases SET revoked = true WHERE customer_id = %s;",
            (customer_id,),
        )
        cur.execute(
            """
                INSERT INTO audit_log (customer_id, actor, action, details, created_at)
                VALUES (%s, %s, 'license_suspended', %s, now());
                """,
            (customer_id, admin_email, json.dumps({"reason": "manual_suspension"})),
        )
        conn.commit()

    return RedirectResponse(
        url=f"/admin/customers/{customer_id}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.post("/customers/{customer_id}/reactivate")
def admin_reactivate_customer(
    request: Request,
    customer_id: UUID,
    csrf_token: Annotated[str, Form()],
    admin_email: Annotated[str, Depends(require_admin)],
) -> Any:
    """Reactivate a suspended customer license."""
    settings = _settings(request)
    if not verify_csrf(csrf_token, settings.admin_session_secret):
        raise HTTPException(status_code=400, detail="CSRF inválido")

    with with_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE licenses SET status = 'active', updated_at = now() WHERE customer_id = %s;",
            (customer_id,),
        )
        cur.execute(
            """
                INSERT INTO audit_log (customer_id, actor, action, details, created_at)
                VALUES (%s, %s, 'license_reactivated', %s, now());
                """,
            (customer_id, admin_email, json.dumps({"reason": "manual_reactivation"})),
        )
        conn.commit()

    return RedirectResponse(
        url=f"/admin/customers/{customer_id}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.post("/customers/{customer_id}/toggle-real-mode")
def admin_toggle_real_mode(
    request: Request,
    customer_id: UUID,
    csrf_token: Annotated[str, Form()],
    admin_email: Annotated[str, Depends(require_admin)],
) -> Any:
    """Toggle real_mode_allowed on license and revoke existing leases to force reissuance."""
    settings = _settings(request)
    if not verify_csrf(csrf_token, settings.admin_session_secret):
        raise HTTPException(status_code=400, detail="CSRF inválido")

    with with_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT real_mode_allowed
            FROM licenses
            WHERE customer_id = %s
            ORDER BY expires_at DESC
            LIMIT 1;
            """,
            (customer_id,),
        )
        row = cur.fetchone()
        current_mode = bool(row[0]) if row else False
        new_mode = not current_mode

        cur.execute(
            """
            UPDATE licenses
            SET real_mode_allowed = %s, updated_at = now()
            WHERE customer_id = %s;
            """,
            (new_mode, customer_id),
        )
        cur.execute(
            "UPDATE leases SET revoked = true WHERE customer_id = %s;",
            (customer_id,),
        )
        cur.execute(
            """
            INSERT INTO audit_log (customer_id, actor, action, details, created_at)
            VALUES (%s, %s, 'license_real_mode_toggled', %s, now());
            """,
            (customer_id, admin_email, json.dumps({"real_mode_allowed": new_mode})),
        )
        conn.commit()

    return RedirectResponse(
        url=f"/admin/customers/{customer_id}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.post("/customers/{customer_id}/devices/{device_id}/release")
def admin_release_device(
    request: Request,
    customer_id: UUID,
    device_id: str,
    csrf_token: Annotated[str, Form()],
    admin_email: Annotated[str, Depends(require_admin)],
) -> Any:
    """Release a client's device: mark revoked=true and revoke associated leases."""
    settings = _settings(request)
    if not verify_csrf(csrf_token, settings.admin_session_secret):
        raise HTTPException(status_code=400, detail="CSRF inválido")

    with with_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE devices SET revoked = true WHERE device_id = %s AND customer_id = %s;",
            (device_id, customer_id),
        )
        cur.execute(
            "UPDATE leases SET revoked = true WHERE device_id = %s AND customer_id = %s;",
            (device_id, customer_id),
        )
        cur.execute(
            """
                INSERT INTO audit_log (customer_id, actor, action, details, created_at)
                VALUES (%s, %s, 'device_released', %s, now());
                """,
            (customer_id, admin_email, json.dumps({"device_id": device_id})),
        )
        conn.commit()

    return RedirectResponse(
        url=f"/admin/customers/{customer_id}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.get("/audit", response_class=HTMLResponse)
def admin_global_audit(
    request: Request,
    admin_email: Annotated[str, Depends(require_admin)],
) -> Any:
    """Render the last 200 global audit events."""
    settings = _settings(request)

    with with_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
                SELECT id, customer_id, actor, action, details, created_at
                FROM audit_log
                ORDER BY created_at DESC
                LIMIT 200;
                """,
        )
        audit_rows = cur.fetchall()

        # Map customer emails
        cur.execute("SELECT id, email FROM customers;")
        cust_map = {str(r[0]): str(r[1]) for r in cur.fetchall()}

    audit_list = [
        {
            "id": a[0],
            "customer_id": str(a[1]) if a[1] else None,
            "customer_email": cust_map.get(str(a[1])) if a[1] else None,
            "actor": a[2],
            "action": a[3],
            "details_json": _dump_json(a[4]),
            "created_at_formatted": _format_dt(a[5]),
        }
        for a in audit_rows
    ]

    csrf_token = sign_csrf(settings.admin_session_secret)
    return templates.TemplateResponse(
        request=request,
        name="admin_audit.html",
        context={
            "admin_email": admin_email,
            "csrf_token": csrf_token,
            "audit_logs": audit_list,
        },
    )
