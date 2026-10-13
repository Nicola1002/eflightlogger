"""Google OAuth 2.0 (authorization-code flow) for the local single-user app.

The OAuth client comes from client_secret.json (Google Cloud console), the user's token is kept in
data/google_token.json and refreshed automatically.
"""
import os
import secrets
import threading
from pathlib import Path

import requests
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow

# the app only ever runs on http://127.0.0.1 – oauthlib refuses plain http without this
os.environ.setdefault("OAUTHLIB_INSECURE_TRANSPORT", "1")
# Google may return the scopes in another order / with extras; do not treat that as an error
os.environ.setdefault("OAUTHLIB_RELAX_TOKEN_SCOPE", "1")

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/userinfo.profile",
]
USERINFO_URL = "https://openidconnect.googleapis.com/v1/userinfo"


class GoogleAuth:
    def __init__(self, base_dir, secret_file, redirect_uri):
        self.secret = (base_dir / secret_file)
        self.token = base_dir / "data" / "google_token.json"
        self.key_file = base_dir / "data" / "flask_secret.key"
        self.redirect_uri = redirect_uri
        self.lock = threading.Lock()
        self._user = None  # cached userinfo of the current token

    def secret_key(self):
        """Persistent random key for Flask's session cookie (created on first start)."""
        self.key_file.parent.mkdir(exist_ok=True)
        if not self.key_file.exists():
            self.key_file.write_text(secrets.token_hex(32))
        return self.key_file.read_text().strip()

    def configured(self):
        return self.secret.exists()

    def _flow(self):
        if not self.configured():
            raise RuntimeError(f"{self.secret.name} fehlt – siehe README (Google-Cloud-Projekt / OAuth-Client)")
        flow = Flow.from_client_secrets_file(str(self.secret), scopes=SCOPES)
        flow.redirect_uri = self.redirect_uri
        return flow

    def start(self):
        """-> (authorization url, state, code_verifier)"""
        flow = self._flow()
        # offline + consent: guarantees a refresh token, so the login survives the 1 h access-token lifetime
        url, state = flow.authorization_url(access_type="offline", prompt="consent",
                                            include_granted_scopes="true")
        return url, state, flow.code_verifier

    def finish(self, authorization_response, state, code_verifier):
        flow = self._flow()
        flow.code_verifier = code_verifier
        flow.fetch_token(authorization_response=authorization_response)
        with self.lock:
            self._save(flow.credentials)
            self._user = None

    def _save(self, creds):
        self.token.parent.mkdir(exist_ok=True)
        self.token.write_text(creds.to_json(), encoding="utf-8")
        try:
            os.chmod(self.token, 0o600)
        except OSError:
            pass

    def credentials(self):
        """Valid credentials or None when not signed in."""
        with self.lock:
            if not self.token.exists():
                return None
            try:
                creds = Credentials.from_authorized_user_file(str(self.token), SCOPES)
            except (ValueError, KeyError):
                return None
            if creds.valid:
                return creds
            if creds.refresh_token:
                try:
                    creds.refresh(Request())
                except Exception:  # revoked / expired refresh token (e.g. consent screen in 'Testing')
                    self.token.unlink(missing_ok=True)
                    return None
                self._save(creds)
                return creds
            return None

    def user(self):
        """{'email', 'name'} of the signed-in user, or None."""
        creds = self.credentials()
        if creds is None:
            return None
        if self._user is None:
            try:
                r = requests.get(USERINFO_URL, headers={"Authorization": f"Bearer {creds.token}"}, timeout=10)
                r.raise_for_status()
            except requests.RequestException:
                return {"email": None, "name": None}  # signed in, profile just not reachable right now
            j = r.json()
            self._user = {"email": j.get("email"), "name": j.get("name") or j.get("email")}
        return self._user

    def logout(self):
        with self.lock:
            self.token.unlink(missing_ok=True)
            self._user = None
