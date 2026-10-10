"""Nora's one Google identity (design/external-files/00-design.md §4).

`scopes` lists every Google service she uses and the scope each declares;
`auth.GoogleCredentials` holds the ONE OAuth client and refresh token they
all share, and hands each consumer an access token, never the secrets. An API
key (the Maps key under `[route]`) is a different mechanism and stays with
its feature.
"""
