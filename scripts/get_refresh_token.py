#!/usr/bin/env python3
"""
One-time helper: obtain a Spotify refresh token via browser OAuth flow.

Run this locally before your first deployment:
    pip install spotipy
    python scripts/get_refresh_token.py

Then copy the printed refresh token into your GitHub Secret: SPOTIFY_REFRESH_TOKEN
"""
import os
import sys


def main() -> None:
    try:
        from spotipy.oauth2 import SpotifyOAuth
    except ImportError:
        print("ERROR: spotipy is not installed. Run: pip install spotipy")
        sys.exit(1)

    client_id = os.getenv("SPOTIPY_CLIENT_ID") or input("Spotify Client ID: ").strip()
    client_secret = os.getenv("SPOTIPY_CLIENT_SECRET") or input("Spotify Client Secret: ").strip()
    redirect_uri = os.getenv("SPOTIPY_REDIRECT_URI", "http://127.0.0.1:8888/callback")

    print(f"\nRedirect URI: {redirect_uri}")
    print("Make sure this URI is registered in your Spotify Developer App settings.\n")

    auth_manager = SpotifyOAuth(
        client_id=client_id,
        client_secret=client_secret,
        redirect_uri=redirect_uri,
        scope="playlist-modify-public playlist-modify-private playlist-read-private",
        open_browser=True,
        cache_path=None,
    )

    print("Opening browser for Spotify authorization...")
    auth_url = auth_manager.get_authorize_url()
    print(f"\nIf the browser does not open automatically, visit:\n  {auth_url}\n")

    import webbrowser
    webbrowser.open(auth_url)

    code = input(
        "After authorizing, paste the full redirect URL here\n"
        "(e.g. http://127.0.0.1:8888/callback?code=...): "
    ).strip()

    # Extract the code from the URL if the user pasted the full URL
    if "?code=" in code:
        code = code.split("?code=")[1].split("&")[0]

    token_info = auth_manager.get_access_token(code, as_dict=True, check_cache=False)
    refresh_token = token_info.get("refresh_token", "")

    if not refresh_token:
        print("\nERROR: No refresh token received. Make sure your app is not in 'client credentials' mode.")
        sys.exit(1)

    print("\n" + "=" * 60)
    print("SUCCESS! Add this as a GitHub Secret named: SPOTIFY_REFRESH_TOKEN")
    print("=" * 60)
    print(refresh_token)
    print("=" * 60 + "\n")


if __name__ == "__main__":
    main()
