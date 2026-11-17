# YouTube media HTTP 403

A successful cookie login and format listing do not prove that YouTube will
serve the video/audio URLs. For authenticated sessions where default clients
return media HTTP 403, the video downloader now re-extracts once using only
`web_creator`. This retry requires the PO provider to be enabled and reachable.
It preserves the requested quality, cookie file and proxy settings. A second
media failure does not repeat this client retry.

## Provider on an Instabox/Linux host with Docker

Install the Python plugin from `requirements.txt`. Run a matching provider
server version (the setup below uses plugin/server 2.0.2):

```bash
docker run -d --name bgutil-provider --restart unless-stopped --init \
  -p 127.0.0.1:4416:4416 brainicism/bgutil-ytdlp-pot-provider:2.0.2
curl --fail http://127.0.0.1:4416/ping
```

Set these local `.env` settings and restart the bot:

```dotenv
YOUTUBE_POT_ENABLED=true
YOUTUBE_POT_BASE_URL=http://127.0.0.1:4416
```

The helper passes the URL via `youtubepot-bgutilhttp:base_url`, the namespace
read by yt-dlp's provider framework. The obsolete `disable_innertube` option is
not sent to bgutil 2.x.

When diagnosing failures, confirm that a token was actually requested and that
media bytes can be downloaded. Listing the provider or opening an HLS manifest
alone is insufficient. PO tokens cannot guarantee access for every account,
video or network. See the [provider documentation](https://github.com/Brainicism/bgutil-ytdlp-pot-provider)
and [yt-dlp PO token guide](https://github.com/yt-dlp/yt-dlp/wiki/PO-Token-Guide).

Regression checks:

```bash
venv/bin/python scripts/check_youtube_media_retry.py
venv/bin/python scripts/check_pot_options.py
```
