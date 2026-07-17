# Market-scan config — <Company>

> Template placeholder — replace with your own. Your market's source list (news sites, communities, regulators), watch keywords, and competitor set. `market-scan` and the radar read this.

## News & developer sources  *(scan step 1A)*

The publications, communities, and developer feeds `market-scan` sweeps each week for your domain's
news — plus the query templates. Replace the examples with sources relevant to your market.

| Source | What to look for | URL |
|---|---|---|
| <Industry news site> | <launches, funding, notable moves in your space> | `example.com` |
| <Developer community> | <releases, adoption signals, technical debates> | `example.com` |
| <Trade / regulatory press> | <compliance & enforcement news> | `example.com` |

Query templates (last 7 days), scaled to PROFILE `target_markets`:

```
"<your core topic>" OR "<adjacent topic>" — published last 7 days
<your space> funding OR launch site:<news-site> — last 7 days
```
