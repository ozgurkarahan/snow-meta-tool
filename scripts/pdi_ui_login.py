"""Interactive-login keep-alive for a ServiceNow PDI (headless Chromium via Playwright).

Per ServiceNow KB3140725 only an interactive login to the PDI itself counts as
activity against the 10-day reclamation; REST/API traffic does not. This signs in
to <instance>/login.do as a DEDICATED NON-ADMIN user, confirms the landing page,
then logs out.

Env:
    SN_INSTANCE_URL        instance (falls back to certs/sn-oauth-config.json)
    SN_KEEPALIVE_USER      dedicated user, e.g. pdi.keepalive (never `admin`)
    SN_KEEPALIVE_PASSWORD  its password

Prints one JSON line. Exit 0 on success or when not configured (skipped),
1 on login failure, 2 on refused/invalid configuration.
Setup: python -m pip install playwright && python -m playwright install chromium
"""
import json
import os
import sys
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent))
import sn_jwt  # noqa: E402

FORBIDDEN_USERS = {"admin", "system", "maint"}
LOGIN_ERROR_SELECTOR = ".outputmsg_error, .notification-error, #output_messages .outputmsg_text"


def emit(result, reason, **extra):
    print(json.dumps({"result": result, "reason": reason, **extra}, separators=(",", ":")))


def check_config(env) -> tuple[str, str] | None:
    """Return (status, reason) to stop early, or None to proceed."""
    user, password = (env.get("SN_KEEPALIVE_USER") or "").strip(), env.get("SN_KEEPALIVE_PASSWORD") or ""
    if not user or not password:
        return "SKIPPED", "SN_KEEPALIVE_USER / SN_KEEPALIVE_PASSWORD not configured"
    if user.lower() in FORBIDDEN_USERS:
        return "REFUSED", "refusing to use a privileged account; use a dedicated non-admin user"
    return None


def assess_landing(url: str, password_field_visible: bool, error_text: str | None) -> tuple[bool, str]:
    path = urlparse(url).path.lower()
    if error_text:
        return False, "login rejected (invalid credentials or locked user)"
    if "change_password" in path or "password_reset" in path or "pwd_reset" in path:
        return False, "landed on a password change page; clear password_needs_reset for the keep-alive user"
    if "multi_factor" in path or "mfa" in path:
        return False, "MFA prompt; the keep-alive user must not require MFA"
    if password_field_visible or path.endswith("/login.do") or path.endswith("/login_redirect.do"):
        return False, "still on the login form"
    if "developer.servicenow.com" in urlparse(url).netloc:
        return False, "redirected to the developer portal (instance hibernating?)"
    return True, "signed in"


def login_once(instance: str, user: str, password: str, timeout_ms: int = 60000) -> tuple[bool, str]:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        try:
            page = browser.new_context().new_page()
            page.set_default_timeout(timeout_ms)
            resp = page.goto(f"{instance}/login.do", wait_until="domcontentloaded")
            if resp is None or resp.status >= 500:
                return False, f"login page HTTP {resp.status if resp else 'none'} (instance down?)"
            page.fill("#user_name", user)
            page.fill("#user_password", password)
            with page.expect_navigation(wait_until="domcontentloaded"):
                page.click("#sysverb_login")
            try:
                page.wait_for_load_state("networkidle", timeout=20000)
            except Exception:  # landing pages may keep long-polling; domcontentloaded is enough
                pass
            pw_field = page.locator("#user_password")
            err = page.locator(LOGIN_ERROR_SELECTOR)
            error_text = err.first.inner_text().strip() if err.count() and err.first.is_visible() else None
            ok, reason = assess_landing(page.url, pw_field.count() > 0 and pw_field.first.is_visible(),
                                        error_text)
            if ok:
                page.goto(f"{instance}/logout.do", wait_until="domcontentloaded")
            return ok, reason
        finally:
            browser.close()


def main(env=None, login=login_once) -> int:
    env = os.environ if env is None else env
    stop = check_config(env)
    if stop:
        emit(*stop)
        return 0 if stop[0] == "SKIPPED" else 2
    instance = sn_jwt.load_config(env=env).instance
    if not instance.startswith("https://"):
        emit("REFUSED", "SN_INSTANCE_URL missing or not https")
        return 2
    try:
        ok, reason = login(instance, env["SN_KEEPALIVE_USER"].strip(), env["SN_KEEPALIVE_PASSWORD"])
    except ImportError:
        emit("FAILED", "playwright not installed (pip install playwright && playwright install chromium)")
        return 1
    except Exception as exc:  # never echo page content or credentials
        emit("FAILED", f"browser error: {type(exc).__name__}")
        return 1
    emit("OK" if ok else "FAILED", reason, instance=instance)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
