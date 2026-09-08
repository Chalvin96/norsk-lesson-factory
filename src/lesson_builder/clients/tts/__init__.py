"""Not a check itself — third-party text-to-speech client adapters.

``google`` wraps Google Cloud Text-to-Speech: service-account OAuth tokens,
bounded transient retries, one mid-run token refresh, and audio decoding.
Provider-neutral voice allocation and cache identity stay in the application
audio operation; distribution validation owns provider-independent WAV checks.
"""
