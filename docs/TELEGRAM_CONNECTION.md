# Telegram connection port

The bot uses Telegram MTProto on TCP port 80 by default. The protocol and
transport encryption are unchanged; this is not an HTTP connection.
Some hosting networks accept TCP connections to Telegram on port 443 but close
them during the initial authentication handshake. PyroTGFork can report this as
`KeyError: 0` from `Auth.unpack`, before checking the bot token.

To select a different port, set PyroTGFork's existing environment variable before
starting the bot:

```sh
PYROGRAM_REPLIT_NWTRAFIK_PORT=5222 python magic.py
```

Use `443` to restore the library default. This setting applies to production
Telegram connections, including media connections, and does not affect download
proxies or the Telegram test servers. Restart the bot after changing it.

When diagnosing this error, test the initial `ReqPqMulti` handshake rather than
only checking whether a TCP connection opens. A successful handshake returns
`ResPQ` and needs no credentials. Do not delete session files or rotate bot
credentials solely because of this error.
