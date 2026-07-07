"""
get_drive_refresh_token.py — ONE-TIME local script to obtain a Google Drive
OAuth refresh token for the job screener.

Why this exists: service accounts can't create new files in a personal
Google Drive (no storage quota of their own — see src/drive.py's docstring).
The fix is to authenticate as yourself instead, via a real OAuth 2.0 user
credential. This script runs that one-time authorization flow.

Before running this:
1. In the same GCP project as your service account, go to
   APIs & Services -> Credentials -> Create Credentials -> OAuth client ID
   -> Application type: Desktop app -> name it anything -> Create.
2. Copy the Client ID and Client Secret it gives you.
3. Make sure the OAuth consent screen (APIs & Services -> OAuth consent
   screen) has: User type = External, the drive.file scope added, and
   Publishing status = "In Production" (NOT "Testing" — Testing-status
   refresh tokens expire after 7 days, which would silently break the daily
   automation after a week). You'll see an "unverified app" warning during
   the login below — that's expected for a personal single-user tool; click
   "Advanced" -> "Go to [app name] (unsafe)" to proceed.

Usage:
    pip install google-auth-oauthlib   (if not already installed)
    python scripts/get_drive_refresh_token.py

This opens a browser window for you to log into your own Google account and
grant Drive access. It never sends your credentials anywhere but Google —
the Client ID/Secret are typed in below, not hardcoded, since this file is
committed to the repo.
"""

from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = ["https://www.googleapis.com/auth/drive.file"]


def main():
    client_id = input("OAuth Client ID: ").strip()
    client_secret = input("OAuth Client Secret: ").strip()

    client_config = {
        "installed": {
            "client_id": client_id,
            "client_secret": client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": ["http://localhost"],
        }
    }

    flow = InstalledAppFlow.from_client_config(client_config, SCOPES)
    # prompt="consent" forces Google to issue a refresh token even if you've
    # authorized this app before (it only does so by default on first consent).
    creds = flow.run_local_server(port=0, prompt="consent")

    if not creds.refresh_token:
        print("\nNo refresh token was returned. Try again — if this repeats, "
              "revoke the app's access at https://myaccount.google.com/permissions "
              "and re-run this script.")
        return

    print("\n" + "=" * 60)
    print("Success! Store these as GitHub repository secrets:")
    print("=" * 60)
    print(f"GOOGLE_OAUTH_CLIENT_ID     = {client_id}")
    print(f"GOOGLE_OAUTH_CLIENT_SECRET = {client_secret}")
    print(f"GOOGLE_OAUTH_REFRESH_TOKEN = {creds.refresh_token}")
    print("=" * 60)


if __name__ == "__main__":
    main()
